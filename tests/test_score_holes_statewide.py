"""Tests for the statewide S3/S4 scoring path.

Offline: the grid/tile maths and the threshold gate are pure NumPy; the
Earth Engine download itself is the CLI's job.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio.transform
from affine import Affine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state import embed_holes, score_holes_statewide, statewide_repair


# ---- the threshold gate -------------------------------------------------------------------


def _grid(rows, cols):
    transform = rasterio.transform.from_origin(1_000_000.0, 2_000_000.0, 30.0, 30.0)
    return rows, cols, transform


def test_gated_add_back_keeps_unconditional_strata_regardless_of_scores():
    rows, cols, transform = _grid(12, 12)
    strata = np.zeros((rows, cols), dtype=np.uint8)
    strata[:4] = 1  # S1, 4x12 = 48 px = 10.7 ac: above the 5 ac MMU
    strata[4:8] = 3  # S3
    scored = np.full((2, rows, cols), 0, dtype=np.uint16)  # prob 0, sim -1 everywhere
    hole = strata > 0
    gate = statewide_repair.GatedScores(
        similarity_threshold=0.9, decision_threshold=0.5)
    add_back = statewide_repair.scored_add_back(strata, hole, scored, gate)
    assert add_back[:4].all()          # S1: unconditional, low scores cannot veto
    assert not add_back[4:8].any()     # S3: below both thresholds stays a hole


def test_gated_add_back_accepts_conditional_strata_only_on_both_thresholds():
    rows, cols, transform = _grid(9, 12)
    strata = np.zeros((rows, cols), dtype=np.uint8)
    strata[0:3] = 3   # S3, 36 px = 8 ac
    strata[3:6] = 4   # S4
    strata[6:9] = 2   # S2

    def scored_for(prob, sim):
        out = np.full((2, rows, cols), 0, dtype=np.uint16)
        out[0] = round(prob * embed_holes.SCORE_SCALE)
        out[1] = round((sim + 1) * embed_holes.SCORE_SCALE)
        return out

    gate = statewide_repair.GatedScores(
        similarity_threshold=0.9, decision_threshold=0.5)

    pass_both = statewide_repair.scored_add_back(
        strata, np.ones((rows, cols), bool), scored_for(0.8, 0.95), gate)
    assert pass_both[0:3].all() and pass_both[3:6].all()

    sim_only = statewide_repair.scored_add_back(
        strata, np.ones((rows, cols), bool), scored_for(0.2, 0.95), gate)
    assert not sim_only[0:3].any() and not sim_only[3:6].any()

    # S2 stays unconditional whatever the scores say.
    assert statewide_repair.scored_add_back(
        strata, np.ones((rows, cols), bool), scored_for(0.0, 0.0), gate)[6:9].all()


def test_gated_add_back_applies_the_mmu_and_respects_the_hole_mask():
    rows, cols, transform = _grid(16, 16)
    strata = np.full((rows, cols), 4, dtype=np.uint8)
    prob = np.full((rows, cols), 0.9)
    sim = np.full((rows, cols), 0.95)
    scored = np.stack([np.round(prob * embed_holes.SCORE_SCALE),
                       np.round((sim + 1) * embed_holes.SCORE_SCALE)]).astype(np.uint16)
    gate = statewide_repair.GatedScores(
        similarity_threshold=0.9, decision_threshold=0.5)

    # An isolated pixel passes both thresholds but falls to the 5 ac MMU;
    # a 6x6 block (8 ac) and a 7x7 block (10.9 ac) survive.
    hole_masked = np.zeros((rows, cols), bool)
    hole_masked[7, 0] = True
    hole_masked[0:6, 0:6] = True
    hole_masked[8:15, 8:15] = True
    out = statewide_repair.scored_add_back(
        strata, hole_masked, scored, gate, min_acres=5.0)
    assert out[0:6, 0:6].all() and out[8:15, 8:15].all()
    assert out.sum() == 36 + 49  # both blocks survive, the lone pixel does not


# ---- the statewide tile maths --------------------------------------------------------------


def test_strata_grid_tiles_split_into_2d_tiles_within_budget_and_width():
    transform = rasterio.transform.from_origin(1_000_000.0, 2_000_000.0, 30.0, 30.0)
    rows, cols = 500, 300
    tiles = score_holes_statewide.grid_tiles(transform, rows, cols, max_tile_pixels=60_000,
                                   max_tile_width=200)
    for window, (left, bottom, right, top) in tiles:
        assert window.width <= 200
        assert window.height * window.width <= 60_000
        # every tile edge on the exact 30 m grid
        assert top == 2_000_000.0 - window.row_off * 30.0
        assert left == 1_000_000.0 + window.col_off * 30.0
        assert bottom == top - window.height * 30.0
        assert right == left + window.width * 30.0
    # full coverage, no gaps: count once per unique (row_off, col_off)
    for row_band in range(0, rows, 100):  # 60_000 // 200 = 300 rows per band
        pass
    band_rows = 60_000 // 200
    assert band_rows == 300
    n_row_bands = (rows + band_rows - 1) // band_rows
    n_col_bands = (cols + 200 - 1) // 200
    assert len(tiles) == n_row_bands * n_col_bands
    assert len({(w.row_off, w.col_off) for w, _ in tiles}) == len(tiles)


def test_strata_grid_tiles_cover_the_whole_grid_exactly_once():
    transform = rasterio.transform.from_origin(1_000_000.0, 2_000_000.0, 30.0, 30.0)
    rows, cols = 130, 450
    tiles = score_holes_statewide.grid_tiles(transform, rows, cols, max_tile_pixels=10_000,
                                   max_tile_width=200)
    canvas = np.zeros((rows, cols), dtype=int)
    for window, _ in tiles:
        canvas[window.row_off:window.row_off + window.height,
               window.col_off:window.col_off + window.width] += 1
    assert (canvas == 1).all()


def test_tile_download_params_carry_the_tile_transform():
    bounds = (1_000_000.0, 1_999_910.0, 1_000_900.0, 2_000_000.0)
    params = embed_holes.tile_download_params(bounds)
    assert params["crs"] == "EPSG:5070"
    assert params["crs_transform"][0] == 30.0
    assert params["crs_transform"][2] == 1_000_000.0
    assert params["dimensions"] == [30, 3]


def test_canvas_offsets_reject_an_off_grid_tile():
    good = Affine(30.0, 0, 1_000_000.0, 0, -30.0, 2_000_000.0 - 60 * 30.0)
    row0, col0 = embed_holes.canvas_offsets(good, (1_000_000.0, 2_000_000.0))
    assert (row0, col0) == (60, 0)
    bad = Affine(30.0, 0, 1_000_000.0 + 7.5, 0, -30.0, 2_000_000.0 - 60 * 30.0)
    with pytest.raises(ValueError, match="not aligned"):
        embed_holes.canvas_offsets(bad, (1_000_000.0, 2_000_000.0))
