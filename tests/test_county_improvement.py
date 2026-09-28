"""County-by-county improvement: the pure core, the grid reads, and the stitch."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from pipeline.s1_initial_state.add_back_methods import AddBackMethod, ConsensusRule, MethodMasks
from pipeline.s1_initial_state.county_improvement import (
    OUTSIDE,
    Grid,
    TreeMapProvenance as TP,
    hansen_rule,
    improve_block,
    obata_rule,
    read_on_grid,
    stitch_rasters,
)
from pipeline.s1_initial_state.ownership_repair import OwnershipProvenance as OP

PX = 0.2224  # acres per pixel


def block(tm, land=None, bookends=None, obata=None, nwos=None, **kw):
    tm = np.array(tm, dtype=np.uint32)
    land = np.ones(tm.shape, dtype=bool) if land is None else np.array(land, dtype=bool)
    masks = MethodMasks({
        AddBackMethod.BOOKENDS: None if bookends is None else np.array(bookends, dtype=bool),
        AddBackMethod.OBATA_DISTURBANCE: None if obata is None else np.array(obata, dtype=bool),
    })
    nwos = np.full(tm.shape, 3, dtype=np.uint8) if nwos is None else np.array(nwos, dtype=np.uint8)
    kw.setdefault("rule", ConsensusRule.UNION)
    kw.setdefault("min_acres", 0.0)
    return improve_block(tm, land, masks, np.zeros(tm.shape, dtype=np.uint8), nwos, **kw)


def test_an_accepted_patch_takes_its_neighbours_plot_and_is_marked_added_back():
    r = block([[7, 7, 7, 7],
               [7, 0, 0, 7],
               [7, 7, 7, 7]],
              bookends=[[0, 0, 0, 0],
                        [0, 1, 1, 0],
                        [0, 0, 0, 0]])
    assert r.improved[1, 1:3].tolist() == [7, 7]
    assert r.provenance[1, 1:3].tolist() == [TP.ADDED_BACK, TP.ADDED_BACK]
    assert r.provenance[0, 0] == TP.PUBLISHED


def test_a_hole_no_method_proposes_stays_unmapped_land():
    r = block([[7, 0, 0]], bookends=[[0, 1, 0]])
    assert r.improved.tolist() == [[7, 7, 0]]
    assert r.provenance.tolist() == [[TP.PUBLISHED, TP.ADDED_BACK, TP.UNMAPPED_LAND]]


def test_water_is_never_added_back():
    r = block([[7, 0]], land=[[1, 0]], bookends=[[0, 1]])
    assert r.improved.tolist() == [[7, 0]]
    assert r.provenance.tolist() == [[TP.PUBLISHED, TP.WATER]]


def test_patches_under_the_minimum_area_are_dropped():
    tm = [[7, 0, 7, 0, 0, 7]]
    proposed = [[0, 1, 0, 1, 1, 0]]
    r = block(tm, bookends=proposed, min_acres=2 * PX)
    assert r.provenance[0].tolist() == [TP.PUBLISHED, TP.UNMAPPED_LAND, TP.PUBLISHED,
                                        TP.ADDED_BACK, TP.ADDED_BACK, TP.PUBLISHED]


def test_at_least_two_accepts_only_where_two_methods_agree():
    r = block([[7, 0, 0, 7]], bookends=[[0, 1, 1, 0]], obata=[[0, 1, 0, 0]],
              rule=ConsensusRule.AT_LEAST_TWO)
    assert r.provenance[0].tolist() == [TP.PUBLISHED, TP.ADDED_BACK, TP.UNMAPPED_LAND, TP.PUBLISHED]
    assert r.method_bits[0].tolist() == [0, 1 | 2, 1, 0]


def test_added_back_forest_gets_the_nearest_known_owner():
    r = block([[7, 0, 0]], bookends=[[0, 1, 0]], nwos=[[4, 1, 1]])
    assert r.ownership.tolist() == [[4, 4, 1]]
    assert r.ownership_provenance.tolist() == [[OP.PUBLISHED, OP.IMPUTED, OP.NOT_FOREST]]


def test_obata_rule_accepts_cuts_dated_2010_through_2022():
    years = np.array([0, 2009, 2010, 2016, 2022, 2023])
    assert obata_rule(years).tolist() == [False, False, True, True, True, False]


def test_hansen_rule_needs_loss_2001_2022_on_ground_that_was_30pct_canopy():
    lossyear = np.array([0, 1, 22, 23, 10])
    cover = np.array([90, 90, 30, 90, 29])
    assert hansen_rule(lossyear, cover).tolist() == [False, True, True, False, False]


def _write(path, values, transform, nodata):
    values = np.asarray(values)
    with rasterio.open(path, "w", driver="GTiff", height=values.shape[0], width=values.shape[1],
                       count=1, dtype=values.dtype, crs="EPSG:5070", transform=transform,
                       nodata=nodata) as dst:
        dst.write(values, 1)


def test_read_on_grid_pads_past_the_source_edge_with_fill(tmp_path):
    src = tmp_path / "src.tif"
    _write(src, np.array([[1, 2], [3, 4]], dtype=np.uint8), Affine(30, 0, 0, 0, -30, 60), 0)
    grid = Grid(Affine(30, 0, 30, 0, -30, 90), (3, 2))  # one row above, one column right
    assert read_on_grid(src, grid, fill=9).tolist() == [[9, 9], [2, 9], [4, 9]]


def test_read_on_grid_refuses_an_off_grid_source(tmp_path):
    src = tmp_path / "src.tif"
    _write(src, np.ones((2, 2), dtype=np.uint8), Affine(30, 0, 15, 0, -30, 60), 0)
    with pytest.raises(ValueError, match="off the grid"):
        read_on_grid(src, Grid(Affine(30, 0, 0, 0, -30, 60), (2, 2)), fill=0)


def test_stitch_pastes_each_county_where_it_has_data_first_county_winning_overlaps(tmp_path):
    a, b = tmp_path / "a.tif", tmp_path / "b.tif"
    _write(a, np.array([[1, 1], [OUTSIDE, 1]], dtype=np.uint8), Affine(30, 0, 0, 0, -30, 60), OUTSIDE)
    _write(b, np.array([[2, OUTSIDE], [2, 2]], dtype=np.uint8), Affine(30, 0, 30, 0, -30, 30), OUTSIDE)
    out = tmp_path / "aoi.tif"
    assert stitch_rasters([a, b], out) == 1  # one pixel both counties claim
    with rasterio.open(out) as s:
        assert s.read(1).tolist() == [[1, 1, OUTSIDE], [OUTSIDE, 1, OUTSIDE], [OUTSIDE, 2, 2]]
        assert s.transform == Affine(30, 0, 0, 0, -30, 60)
        assert s.nodata == OUTSIDE
