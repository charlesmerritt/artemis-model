"""Tests for the FVS-input builder (pipeline/s4_fvs/build_fvs_inputs.py)."""

import copy
from pathlib import Path
import sys

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s4_fvs.build_fvs_inputs import (
    build_fvs_inputs,
    build_stand_init,
    build_tree_init,
    filter_and_renormalize_weights,
    impute_nearest_runnable,
    ladder_decisions,
    summarize_tree_sources,
)
from pipeline.s4_fvs.fallback_treelists import load_fallback_policy

CRS = "EPSG:5070"


def _weights():
    # MU 1: p1 (0.6), p2 (0.4); MU 2: only p3.
    return pd.DataFrame({
        "MU_ID": ["1", "1", "2"],
        "PLT_CN": ["p1", "p2", "p3"],
        "WEIGHT": [0.6, 0.4, 1.0],
    })


def _tree_init():
    return pd.DataFrame({
        "STAND_CN": ["p1", "p1", "p2", "p3"],
        "TREE_ID": [1, 2, 3, 4],
        "SPECIES": [131, 131, 111, 121],
        "DIAMETER": [6.0, 8.0, 10.0, 12.0],
        "TREE_COUNT": [10.0, 10.0, 5.0, 8.0],
    })


def test_filter_and_renormalize_drops_below_min_then_renorms():
    w = pd.DataFrame({"MU_ID": ["1", "1", "1"], "PLT_CN": ["a", "b", "c"], "WEIGHT": [0.90, 0.07, 0.03]})
    out = filter_and_renormalize_weights(w, min_weight=0.05)
    assert set(out["PLT_CN"]) == {"a", "b"}          # c (0.03) dropped
    assert out["WEIGHT"].sum() == pytest.approx(1.0)  # renormalised
    assert out.set_index("PLT_CN").loc["a", "WEIGHT"] == pytest.approx(0.90 / 0.97)


def test_build_tree_init_scales_tpa_by_weight_and_relabels_stand():
    trees, runnable = build_tree_init(_weights(), _tree_init(), min_weight=0.05)
    assert runnable == {"1", "2"}
    assert set(trees["STAND_ID"]) == {"MU_1", "MU_2"}
    # p1 trees (TPA 10) scaled by 0.6 -> 6.0
    p1 = trees[(trees["MU_ID"] == "1") & (trees["PLT_CN"] == "p1")]
    assert p1["TREE_COUNT"].tolist() == pytest.approx([6.0, 6.0])
    # p2 tree (TPA 5) scaled by 0.4 -> 2.0
    p2 = trees[(trees["MU_ID"] == "1") & (trees["PLT_CN"] == "p2")]
    assert p2["TREE_COUNT"].iloc[0] == pytest.approx(2.0)
    assert (trees["TREE_SOURCE"] == "FIA_WEIGHTED_DIRECT").all()


def test_build_tree_init_marks_unmatched_units_not_runnable():
    weights = pd.DataFrame({"MU_ID": ["9"], "PLT_CN": ["missing"], "WEIGHT": [1.0]})
    trees, runnable = build_tree_init(weights, _tree_init())
    assert runnable == set()
    assert trees.empty


def test_build_stand_init_one_row_per_runnable_unit():
    attrs = pd.DataFrame({"MU_ID": ["1", "2", "3"], "ACRES": [10.0, 20.0, 5.0]})
    stands = build_stand_init(attrs, runnable_mu_ids={"1", "2"})
    assert set(stands["STAND_ID"]) == {"MU_1", "MU_2"}
    assert (stands["VARIANT"] == "SN").all()
    assert (stands["INV_YEAR"] == 2022).all()
    assert "3" not in set(stands["MU_ID"])


def test_impute_nearest_runnable_copies_from_nearest_unit():
    # MU 1 runnable (has trees), MU 2 empty and adjacent -> inherits MU 1's trees.
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100)],
        crs=CRS,
    )
    trees, runnable = build_tree_init(
        pd.DataFrame({"MU_ID": ["1"], "PLT_CN": ["p1"], "WEIGHT": [1.0]}), _tree_init()
    )
    out = impute_nearest_runnable(units, trees, runnable)
    mu2 = out[out["MU_ID"] == "2"]
    assert not mu2.empty
    assert (mu2["TREE_SOURCE"] == "IMPUTED_NEAREST").all()
    assert (mu2["DONOR_STAND_ID"] == "MU_1").all()
    assert (mu2["STAND_ID"] == "MU_2").all()


def test_build_fvs_inputs_end_to_end_covers_all_units():
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2", "3"], "ACRES": [10.0, 20.0, 5.0]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100), box(500, 500, 560, 560)],
        crs=CRS,
    )
    # Only MU 1 and 2 have direct weights; MU 3 must be imputed from its nearest (MU 2).
    weights = pd.DataFrame({"MU_ID": ["1", "2"], "PLT_CN": ["p1", "p3"], "WEIGHT": [1.0, 1.0]})
    stands, trees = build_fvs_inputs(units, weights, _tree_init())
    assert set(stands["STAND_ID"]) == {"MU_1", "MU_2", "MU_3"}
    assert set(trees["MU_ID"]) == {"1", "2", "3"}
    assert (trees[trees["MU_ID"] == "3"]["TREE_SOURCE"] == "IMPUTED_NEAREST").all()


# ---- initialization ladder ------------------------------------------------------------

def _runnable_one():
    """MU 1 runnable from plot p1; everything else is a gap."""
    return build_tree_init(
        pd.DataFrame({"MU_ID": ["1"], "PLT_CN": ["p1"], "WEIGHT": [1.0]}), _tree_init()
    )


def test_ladder_sends_a_close_unit_to_a_donor():
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"]},
        geometry=[box(0, 0, 100, 100), box(600, 0, 700, 100)],   # 500 m apart
        crs=CRS,
    )
    _, runnable = _runnable_one()
    decisions = ladder_decisions(units, runnable)
    assert decisions["MU_ID"].tolist() == ["2"]
    assert decisions.loc[0, "method"] == "nearest_runnable_unit"
    assert decisions.loc[0, "donor_id"] == "1"
    assert decisions.loc[0, "donor_distance_m"] == pytest.approx(500.0)


def test_ladder_refuses_a_distant_donor_and_falls_to_a_fixed_list():
    """The bound this whole mechanism exists for: no more 40 km donors."""
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"]},
        geometry=[box(0, 0, 100, 100), box(40_000, 0, 40_100, 100)],
        crs=CRS,
    )
    _, runnable = _runnable_one()
    decisions = ladder_decisions(units, runnable)
    assert decisions.loc[0, "method"] == "fallback_slot"
    assert decisions.loc[0, "tree_source"] == "FALLBACK_FIXED"
    assert decisions.loc[0, "donor_id"] is None


def test_ladder_prefers_a_further_same_type_donor_over_a_closer_mismatch():
    """Forest type beats proximity inside 5 km — a pine list is not a hardwood stand."""
    units = gpd.GeoDataFrame(
        {"MU_ID": ["pine", "hwd", "gap"], "FORTYPCD": [161, 505, 505]},
        geometry=[
            box(0, 0, 100, 100),            # pine, 100 m from the gap
            box(4_000, 0, 4_100, 100),      # hardwood, 3.8 km from the gap
            box(200, 0, 300, 100),          # the gap, hardwood
        ],
        crs=CRS,
    )
    trees, _ = build_tree_init(
        pd.DataFrame({"MU_ID": ["pine", "hwd"], "PLT_CN": ["p1", "p3"], "WEIGHT": [1.0, 1.0]}),
        _tree_init(),
    )
    decisions = ladder_decisions(units, {"pine", "hwd"})
    assert decisions.loc[0, "donor_id"] == "hwd"
    assert bool(decisions.loc[0, "same_forest_type"])
    assert decisions.loc[0, "rung"] == "donor_unit_same_type"


def test_ladder_routes_an_unknown_forest_type_to_the_default_slot():
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"]},
        geometry=[box(0, 0, 100, 100), box(40_000, 0, 40_100, 100)],
        crs=CRS,
    )
    _, runnable = _runnable_one()
    decisions = ladder_decisions(units, runnable)
    assert decisions.loc[0, "slot"] == "mixed_pine_hardwood_established"


def test_ladder_report_needs_no_resolved_slots():
    """This is the measurement tool for the unmeasured hole-prevalence question."""
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"]},
        geometry=[box(0, 0, 100, 100), box(40_000, 0, 40_100, 100)],
        crs=CRS,
    )
    _, runnable = _runnable_one()
    assert not ladder_decisions(units, runnable).empty     # no lock file exists


def test_ladder_decisions_is_empty_when_every_unit_is_runnable():
    units = gpd.GeoDataFrame({"MU_ID": ["1"]}, geometry=[box(0, 0, 100, 100)], crs=CRS)
    assert ladder_decisions(units, {"1"}).empty


# ---- fixed-list substitution ----------------------------------------------------------

def _far_units():
    return gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"], "unit_area_ha": [10.0, 40.0]},
        geometry=[box(0, 0, 100, 100), box(40_000, 0, 40_100, 100)],
        crs=CRS,
    )


def test_unresolved_fixed_list_raises_rather_than_substituting():
    """The config's stated position: an arbitrary substitute would be invisible downstream."""
    trees, runnable = _runnable_one()
    with pytest.raises(RuntimeError, match="--resolve"):
        impute_nearest_runnable(_far_units(), trees, runnable, tree_init=_tree_init())


def test_skip_mode_leaves_the_unit_out_instead_of_failing():
    trees, runnable = _runnable_one()
    out = impute_nearest_runnable(
        _far_units(), trees, runnable, tree_init=_tree_init(), on_missing_fallback="skip"
    )
    assert set(out["MU_ID"]) == {"1"}          # unit 2 absent, not silently wrong


def test_bad_on_missing_fallback_value_is_rejected():
    trees, runnable = _runnable_one()
    with pytest.raises(ValueError, match="on_missing_fallback"):
        impute_nearest_runnable(_far_units(), trees, runnable, on_missing_fallback="maybe")


def _policy_with_lock(tmp_path, plt_cn="p3"):
    """A policy whose lock file pins every slot to one plot in the test tree table."""
    policy = copy.deepcopy(load_fallback_policy())
    lock = tmp_path / "fallback.lock.yaml"
    lock.write_text(yaml.safe_dump(
        {"version": 1, "slots": {slot: {"plt_cn": plt_cn} for slot in policy["slots"]}}
    ))
    policy["lock_file"] = str(lock)
    return policy


def test_a_resolved_fixed_list_initializes_the_unit_from_its_pinned_plot(tmp_path):
    trees, runnable = _runnable_one()
    out = impute_nearest_runnable(
        _far_units(), trees, runnable, tree_init=_tree_init(),
        policy=_policy_with_lock(tmp_path, plt_cn="p3"),
    )
    mu2 = out[out["MU_ID"] == "2"]
    assert not mu2.empty
    assert (mu2["TREE_SOURCE"] == "FALLBACK_FIXED").all()
    assert (mu2["FALLBACK_SLOT"] == "mixed_pine_hardwood_established").all()
    assert (mu2["STAND_ID"] == "MU_2").all()
    assert mu2["STAND_CN"].tolist() == ["p3"]      # the pinned donor plot


def test_a_pin_missing_from_the_tree_table_is_an_error(tmp_path):
    trees, runnable = _runnable_one()
    with pytest.raises(RuntimeError, match="not present in the supplied tree table"):
        impute_nearest_runnable(
            _far_units(), trees, runnable, tree_init=_tree_init(),
            policy=_policy_with_lock(tmp_path, plt_cn="not_a_plot"),
        )


# ---- provenance reporting -------------------------------------------------------------

def test_summarize_tree_sources_reports_the_three_required_cuts(tmp_path):
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2", "3"], "unit_area_ha": [10.0, 40.0, 50.0]},
        geometry=[box(0, 0, 100, 100), box(600, 0, 700, 100), box(40_000, 0, 40_100, 100)],
        crs=CRS,
    )
    trees, runnable = _runnable_one()
    out = impute_nearest_runnable(
        units, trees, runnable, tree_init=_tree_init(), policy=_policy_with_lock(tmp_path)
    )
    report = summarize_tree_sources(out, units)

    by_source = report["by_source"].set_index("TREE_SOURCE")
    assert by_source.loc["FIA_WEIGHTED_DIRECT", "unit_area_ha"] == 10.0
    assert by_source.loc["IMPUTED_NEAREST", "unit_area_ha"] == 40.0
    assert by_source.loc["FALLBACK_FIXED", "unit_area_ha"] == 50.0
    assert report["by_source"]["share"].sum() == pytest.approx(1.0)

    assert report["donor_distance"].loc[0, "n_units"] == 1
    assert report["donor_distance"].loc[0, "share_over_2km"] == 0.0

    by_slot = report["by_slot"].set_index("FALLBACK_SLOT")
    assert by_slot.loc["mixed_pine_hardwood_established", "unit_area_ha"] == 50.0


def test_summarize_falls_back_to_unit_counts_without_an_area_column():
    trees, _ = _runnable_one()
    report = summarize_tree_sources(trees)
    assert report["by_source"]["units"].sum() == 1.0
