"""County-by-county improvement: the pure core, the grid reads, and the stitch."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import Affine

from pipeline.s1_initial_state.add_back_methods import AddBackMethod, ConsensusRule, MethodMasks
from pipeline.s1_initial_state.county_improvement import (
    OUTSIDE,
    Grid,
    TreeMapProvenance as TP,
    county_summary,
    gate_rejections,
    hansen_rule,
    improve_block,
    obata_rule,
    Inputs,
    read_on_grid,
    run_manifest,
    SUM_KEYS,
    stitch_rasters,
    sum_county_summaries,
    sum_gate_rejections,
)
from pipeline.s1_initial_state.evt_gate import EvtGatePolicy
from pipeline.s1_initial_state.impute_establishment import EstablishmentMode as M
from pipeline.s1_initial_state.ownership_repair import OwnershipProvenance as OP

MODES = {1: M.SCALED_YOUNG, 2: M.DONOR_AS_IS, 3: M.SCALED_YOUNG, 4: M.SCALED_YOUNG}

PX = 0.2224  # acres per pixel


def block(tm, land=None, bookends=None, obata=None, nwos=None, strata=None, eligible=None, **kw):
    tm = np.array(tm, dtype=np.uint32)
    land = np.ones(tm.shape, dtype=bool) if land is None else np.array(land, dtype=bool)
    # Default S2 (tree at both bookends): donor as is, provenance 2.
    strata = np.full(tm.shape, 2, dtype=np.uint8) if strata is None else np.array(strata, dtype=np.uint8)
    masks = MethodMasks({
        AddBackMethod.BOOKENDS: None if bookends is None else np.array(bookends, dtype=bool),
        AddBackMethod.OBATA_DISTURBANCE: None if obata is None else np.array(obata, dtype=bool),
    })
    nwos = np.full(tm.shape, 3, dtype=np.uint8) if nwos is None else np.array(nwos, dtype=np.uint8)
    kw.setdefault("rule", ConsensusRule.UNION)
    kw.setdefault("min_acres", 0.0)
    kw.setdefault("stratum_modes", MODES)
    if eligible is not None:
        kw["eligible"] = np.array(eligible, dtype=bool)
    return improve_block(tm, land, masks, strata, nwos, **kw)


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


def test_a_cut_or_regrowing_patch_is_marked_added_back_young_and_standing_forest_added_back():
    # Left patch S1 (logged 2016, tree 2024): scaled young list. Right patch S2: donor as is.
    r = block([[7, 0, 0, 7, 0, 0, 7]],
              bookends=[[0, 1, 1, 0, 1, 1, 0]],
              strata=[[0, 1, 1, 0, 2, 2, 0]])
    assert r.provenance[0].tolist() == [TP.PUBLISHED, TP.ADDED_BACK_YOUNG, TP.ADDED_BACK_YOUNG,
                                        TP.PUBLISHED, TP.ADDED_BACK, TP.ADDED_BACK, TP.PUBLISHED]
    assert r.improved[0].tolist() == [7] * 7          # both keep the donor TM_ID
    assert r.added_back[0].tolist() == [False, True, True, False, True, True, False]
    assert sorted(r.assignments["establishment_mode"]) == ["donor_as_is", "scaled_young"]


def test_a_patch_without_a_stratum_is_scaled_young():
    r = block([[7, 0, 7]], bookends=[[0, 1, 0]], strata=[[0, 0, 0]])
    assert r.provenance[0, 1] == TP.ADDED_BACK_YOUNG


def test_the_evt_gate_rejects_proposals_on_ineligible_pixels():
    r = block([[7, 0, 0, 7]], bookends=[[0, 1, 1, 0]], eligible=[[1, 1, 0, 1]])
    assert r.provenance[0].tolist() == [TP.PUBLISHED, TP.ADDED_BACK, TP.UNMAPPED_LAND, TP.PUBLISHED]


def test_the_evt_gate_applies_before_the_minimum_patch_area():
    # Three proposed pixels clear a 2-pixel MMU; gated down to one, the patch is dropped.
    r = block([[7, 0, 0, 0, 7]], bookends=[[0, 1, 1, 1, 0]], eligible=[[1, 1, 0, 0, 1]],
              min_acres=2 * PX)
    assert TP.added_back(r.provenance).sum() == 0


def test_the_evt_gate_applies_before_the_consensus_rule():
    # Both methods agree on both holes; the gate strips the right one from every method,
    # so its two votes become none.
    r = block([[7, 0, 0, 7]], bookends=[[0, 1, 1, 0]], obata=[[0, 1, 1, 0]],
              eligible=[[1, 1, 0, 1]], rule=ConsensusRule.AT_LEAST_TWO)
    assert r.provenance[0].tolist() == [TP.PUBLISHED, TP.ADDED_BACK, TP.UNMAPPED_LAND, TP.PUBLISHED]


def test_method_bits_are_after_the_gate_and_raw_bits_before_it():
    r = block([[7, 0, 0, 7]], bookends=[[0, 1, 1, 0]], obata=[[0, 0, 1, 0]],
              eligible=[[1, 1, 0, 1]])
    assert r.method_bits[0].tolist() == [0, 1, 0, 0]
    assert r.method_bits_raw[0].tolist() == [0, 1, 1 | 2, 0]


def test_without_an_eligibility_mask_raw_and_gated_bits_agree():
    r = block([[7, 0, 0, 7]], bookends=[[0, 1, 1, 0]])
    assert r.method_bits.tolist() == r.method_bits_raw.tolist()


def test_added_back_covers_both_provenance_codes():
    prov = np.array([TP.WATER, TP.PUBLISHED, TP.ADDED_BACK, TP.UNMAPPED_LAND, TP.ADDED_BACK_YOUNG])
    assert TP.added_back(prov).tolist() == [False, False, True, False, True]


def test_county_summary_counts_added_back_acres_by_mode_and_both_as_holes():
    r = block([[7, 0, 0, 7, 0, 0, 7, 0]],
              bookends=[[0, 1, 1, 0, 1, 0, 0, 0]],
              strata=[[0, 1, 1, 0, 2, 2, 0, 5]])
    patches = r.assignments.assign(county_pixels=1)
    whole = (slice(None), slice(None))
    s = county_summary("12003", r, np.zeros(r.provenance.shape, dtype=np.uint8),
                       np.ones(r.provenance.shape, dtype=bool), whole,
                       np.zeros(r.provenance.shape, dtype=bool), ConsensusRule.UNION, 0.0, "all_forest", 63,
                       pd.DataFrame(patches), evt_gate=EvtGatePolicy.NONE)
    assert s["added_back_acres"] == pytest.approx(round(3 * PX, 1))
    assert s["added_back_acres_by_mode"] == {"scaled_young": round(2 * PX, 1),
                                             "donor_as_is": round(1 * PX, 1)}
    assert s["hole_acres"] == pytest.approx(round(5 * PX, 1))   # 3 added back + 2 still unmapped


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


def county(fips, name, acres, credit, young):
    summary = {k: acres for k in SUM_KEYS}
    summary.update(county_fips=fips, county=name, rule="union",
                   added_back_credit_acres={"bookends": credit, "hansen_loss": None},
                   added_back_acres_by_mode={"scaled_young": young},
                   methods={"bookends": {"status": "ok"}, "hansen_loss": {"status": "missing"}},
                   evt_gate={"policy": "evt2022_agriculture_developed_v2",
                             "raw_proposal_acres": acres, "rejected_acres": credit})
    return summary


def test_county_summaries_sum_to_the_aoi_total_keeping_a_missing_method_missing():
    total = sum_county_summaries([county("12001", "Alachua", 1.04, 2.0, 0.5),
                                  county("12003", "Baker", 2.03, 3.0, 1.25)])

    assert total["added_back_acres"] == 3.1  # summed, then rounded once
    assert total["counties"] == ["12001 Alachua", "12003 Baker"]
    assert total["added_back_credit_acres"] == {"bookends": 5.0, "hansen_loss": None}
    assert total["added_back_acres_by_mode"] == {"scaled_young": 1.8}
    assert total["methods"] == {"bookends": "ok", "hansen_loss": "missing"}
    assert total["rule"] == "union"
    assert total["evt_gate"] == {"policy": "evt2022_agriculture_developed_v2",
                                 "raw_proposal_acres": 3.1, "rejected_acres": 5.0}


def test_county_summary_records_the_gate_and_what_it_rejected():
    r = block([[7, 0, 0, 0, 7]], bookends=[[0, 1, 1, 0, 0]], obata=[[0, 0, 1, 1, 0]],
              eligible=[[1, 1, 0, 0, 1]])
    whole = (slice(None), slice(None))
    shape = r.provenance.shape
    s = county_summary("12003", r, np.zeros(shape, dtype=np.uint8), np.ones(shape, dtype=bool),
                       whole, np.zeros(shape, dtype=bool), ConsensusRule.UNION, 0.0, "all_forest",
                       63, pd.DataFrame(r.assignments.assign(county_pixels=1)),
                       evt_gate=EvtGatePolicy.AGRICULTURE_V1)
    assert s["evt_gate"] == {"policy": "evt2022_agriculture_v1",
                             "raw_proposal_acres": round(3 * PX, 1),
                             "rejected_acres": round(2 * PX, 1)}


LEGEND = pd.DataFrame({"VALUE": [7755, 7296, 9001],
                       "EVT_NAME": ["Crops", "Developed-Low Intensity", "Pine"]})


def test_gate_rejections_count_rejected_pixels_by_evt_class_largest_first():
    evt = np.array([[7755, 7296, 7296], [9001, 7755, 7296]])
    rejected = np.array([[1, 1, 1], [0, 0, 1]], dtype=bool)
    table = gate_rejections(evt, rejected, LEGEND)
    assert table.to_dict("list") == {
        "evt_value": [7296, 7755], "rejected_pixels": [3, 1],
        "rejected_acres": [round(3 * PX, 4), round(PX, 4)],
        "EVT_NAME": ["Developed-Low Intensity", "Crops"]}


def test_gate_rejections_sum_over_counties_by_evt_class():
    a = gate_rejections(np.array([7755, 7296]), np.array([True, True]), LEGEND)
    b = gate_rejections(np.array([7296, 7296]), np.array([True, True]), LEGEND)
    total = sum_gate_rejections([a, b])
    assert total["evt_value"].tolist() == [7296, 7755]
    assert total["rejected_pixels"].tolist() == [3, 1]
    assert total["rejected_acres"].tolist() == [round(3 * PX, 4), round(PX, 4)]
    assert total["EVT_NAME"].tolist() == ["Developed-Low Intensity", "Crops"]


def test_the_run_manifest_records_the_gate_rule_and_inputs():
    total = {"counties": ["12003 Baker"], "rule": "union", "methods": {"bookends": "run"},
             "evt_gate": {"policy": "evt2022_agriculture_developed_v2", "rejected_acres": 1.0}}
    m = run_manifest(total, Inputs(hansen=Path("/x/hansen.tif")))
    assert m["evt_gate"] == "evt2022_agriculture_developed_v2"
    assert m["rule"] == "union" and m["counties"] == ["12003 Baker"]
    assert m["inputs"]["hansen"] == "/x/hansen.tif" and m["inputs"]["obata"] is None


def test_gate_rejected_is_a_raw_proposal_the_gate_stripped_from_every_method():
    r = block([[7, 0, 0, 0, 7]], bookends=[[0, 1, 1, 0, 0]], obata=[[0, 0, 1, 1, 0]],
              eligible=[[1, 1, 1, 0, 1]])
    assert r.gate_rejected[0].tolist() == [False, False, False, True, False]
