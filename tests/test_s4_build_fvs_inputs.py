"""Tests for the FVS-input builder (pipeline/s4_fvs/build_fvs_inputs.py)."""

from pathlib import Path
import sys

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s4_fvs.build_fvs_inputs import (
    build_fvs_inputs,
    build_stand_init,
    build_tree_init,
    filter_and_renormalize_weights,
    impute_nearest_runnable,
)

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
