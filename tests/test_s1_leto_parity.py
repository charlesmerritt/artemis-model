"""Behavioral parity checks between legacy LETO equations and the Python port."""

from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.weights import build_plot_weights
from pipeline.s1_initial_state.leto_initial_state import (
    build_management_unit_crosswalk,
    filter_and_normalize_weights,
    impute_missing_tree_rows,
    prepare_direct_tree_rows,
)

LEGACY_TREE_COLUMNS = [
    "STAND_ID",
    "TREE_ID",
    "SPECIES",
    "DIAMETER",
    "HT",
    "CRRATIO",
    "TREE_COUNT",
    "MU_ID",
    "PLT_CN",
    "WEIGHT",
    "Species_FIA",
    "TREE_SOURCE",
    "DONOR_STAND_ID",
    "NEAR_DIST",
]


def _legacy_prepare_tree_rows(weights, trees, species_lookup):
    """Independent pandas transcription of LETO_CSV_PIPELINE.txt."""
    legacy = weights.merge(trees, on="PLT_CN", how="left", indicator=True)
    legacy = legacy.loc[legacy["_merge"] == "both"].copy()
    legacy = legacy.loc[legacy["STATUSCD"] == "1"].copy()
    legacy = legacy.rename(
        columns={
            "CN": "TREE_ID",
            "INVYR": "INV_YEAR",
            "SPCD": "Species_FIA",
            "DIA": "DIAMETER",
            "CR": "CRRATIO",
            "TPA_UNADJ": "TREE_COUNT",
        }
    )
    legacy["Species_FIA_CLEAN"] = (
        pd.to_numeric(legacy["Species_FIA"], errors="coerce")
        .astype("Int64")
        .astype("string")
        .str.zfill(3)
    )
    legacy["SPECIES"] = legacy["Species_FIA_CLEAN"].map(species_lookup)
    legacy["STAND_ID"] = "MU_" + legacy["Stand_ID"].astype("string")
    for column in ["DIAMETER", "HT", "ACTUALHT", "CRRATIO", "TREE_COUNT", "WEIGHT"]:
        legacy[column] = pd.to_numeric(legacy[column], errors="coerce")
    legacy["TREE_COUNT"] = legacy["TREE_COUNT"] * legacy["WEIGHT"]
    legacy["HT"] = legacy["HT"].fillna(legacy["ACTUALHT"])
    legacy = legacy.dropna(subset=["STAND_ID", "SPECIES", "DIAMETER", "TREE_COUNT"])
    legacy = legacy.loc[legacy["TREE_COUNT"] > 0].copy()
    legacy["TREE_SOURCE"] = "FIA_WEIGHTED_DIRECT"
    legacy["DONOR_STAND_ID"] = ""
    legacy["NEAR_DIST"] = ""
    return legacy[LEGACY_TREE_COLUMNS].reset_index(drop=True)


def _legacy_impute_tree_rows(direct_trees, nearest_pairs):
    """Independent transcription of LETO's nearest-table donor-copy loop."""
    imputed_groups = []
    for pair in nearest_pairs.itertuples(index=False):
        donor_stand_id = f"MU_{pair.DONOR_MU_ID}"
        donor_trees = direct_trees.loc[
            direct_trees["STAND_ID"] == donor_stand_id
        ].copy()
        donor_trees["DONOR_STAND_ID"] = donor_stand_id
        donor_trees["TREE_SOURCE"] = "IMPUTED_NEAREST"
        donor_trees["NEAR_DIST"] = pair.NEAR_DIST
        donor_trees["STAND_ID"] = f"MU_{pair.MISSING_MU_ID}"
        donor_trees["MU_ID"] = pair.MISSING_MU_ID
        donor_trees["TREE_ID"] = range(1, len(donor_trees) + 1)
        imputed_groups.append(donor_trees)
    return pd.concat([direct_trees, *imputed_groups], ignore_index=True)


@pytest.fixture
def leto_spatial_fixture(tmp_path: Path):
    treemap_path = tmp_path / "treemap.tif"
    values = np.array([[10, 10, 20, 20], [10, 30, 20, 20]], dtype="int32")

    with rasterio.open(
        treemap_path,
        "w",
        driver="GTiff",
        height=2,
        width=4,
        count=1,
        dtype="int32",
        crs="EPSG:5070",
        transform=from_origin(0, 60, 30, 30),
        nodata=-9999,
    ) as destination:
        destination.write(values, 1)

    management_units = gpd.GeoDataFrame(
        {"MU_ID": [1, 2]},
        geometry=[box(0, 0, 60, 60), box(60, 0, 120, 60)],
        crs="EPSG:5070",
    )
    lookup = pd.DataFrame(
        {
            "VALUE": [10, 20, 30],
            "PLT_CN": ["10000000000001", "10000000000002", "10000000000003"],
        }
    )
    return management_units, treemap_path, lookup


def test_plot_weights_match_leto_assign_plt_cn(leto_spatial_fixture):
    """Match LETO's groupby cell counts and CELL_COUNT / TOTAL_CELLS weights."""
    management_units, treemap_path, lookup = leto_spatial_fixture

    actual = build_plot_weights(management_units, treemap_path, lookup)

    expected = pd.DataFrame(
        {
            "MU_ID": pd.Series(["1", "1", "2"], dtype="string"),
            "TM_VALUE": [10, 30, 20],
            "CELL_COUNT": [3, 1, 4],
            "TOTAL_CELLS": [4, 4, 4],
            "WEIGHT": [0.75, 0.25, 1.0],
            "PLT_CN": pd.Series(
                ["10000000000001", "10000000000003", "10000000000002"],
                dtype="string",
            ),
        }
    )
    pd.testing.assert_frame_equal(actual, expected)


def test_plot_weights_exclude_unmapped_cells_from_leto_denominator(tmp_path):
    """LETO drops cells without PLT_CN before TOTAL_CELLS and WEIGHT."""
    treemap_path = tmp_path / "treemap_unmapped.tif"
    with rasterio.open(
        treemap_path,
        "w",
        driver="GTiff",
        height=1,
        width=2,
        count=1,
        dtype="int32",
        crs="EPSG:5070",
        transform=from_origin(0, 30, 30, 30),
        nodata=-9999,
    ) as destination:
        destination.write(np.array([[10, 99]], dtype="int32"), 1)
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1"]}, geometry=[box(0, 0, 60, 30)], crs="EPSG:5070"
    )
    lookup = pd.DataFrame({"VALUE": [10], "PLT_CN": ["900"]})

    actual = build_plot_weights(units, treemap_path, lookup)

    assert actual.loc[0, "TOTAL_CELLS"] == 1
    assert actual.loc[0, "WEIGHT"] == pytest.approx(1.0)


def test_majority_plot_tie_preserves_leto_tm_value_order():
    """LETO's stable count sort retains ascending TM_VALUE order for ties."""
    units = pd.DataFrame(
        {
            "MU_ID": ["1"],
            "Acres": [10.0],
            "OWN_CODE": [4],
            "OWN_TYPE": ["family"],
            "SMZ_Pct": [0.0],
        }
    )
    weights = pd.DataFrame(
        {
            "MU_ID": ["1", "1"],
            "TM_VALUE": [10, 20],
            "CELL_COUNT": [1, 1],
            "TOTAL_CELLS": [2, 2],
            "WEIGHT": [0.5, 0.5],
            "PLT_CN": ["900", "100"],
        }
    )

    crosswalk = build_management_unit_crosswalk(units, weights)

    assert crosswalk.loc[0, "PLT_CN"] == "900"


def test_weight_filter_matches_leto_group_normalization():
    """Compare the new transform with LETO's filter/group-transform equations."""
    weights = pd.DataFrame(
        {
            "MU_ID": ["1", "1", "2", "2"],
            "PLT_CN": ["101", "102", "201", "202"],
            "WEIGHT": [0.8, 0.2, 0.96, 0.04],
        }
    )
    crosswalk = pd.DataFrame(
        {
            "MU_ID": ["1", "2"],
            "Stand_ID": ["1", "2"],
            "Acres": [10.0, 20.0],
            "OWN_CODE": [4, 5],
            "OWN_TYPE": ["family", "corporate"],
            "SMZ_Pct": [0.0, 5.0],
        }
    )
    legacy = weights.loc[weights["WEIGHT"] >= 0.05].copy()
    legacy["WEIGHT"] = legacy["WEIGHT"] / legacy.groupby("MU_ID")["WEIGHT"].transform(
        "sum"
    )
    legacy = legacy.merge(crosswalk, on="MU_ID", how="left")

    actual = filter_and_normalize_weights(weights, crosswalk)

    pd.testing.assert_frame_equal(actual, legacy, check_dtype=False)


def test_tree_preparation_matches_leto_csv_pipeline():
    """Match LETO's live-tree, species, height, and weighted-TPA expressions."""
    normalized_weights = pd.DataFrame(
        {
            "MU_ID": ["1", "1"],
            "PLT_CN": ["101", "102"],
            "WEIGHT": [0.8, 0.2],
            "Stand_ID": ["1", "1"],
        }
    )
    fia_trees = pd.DataFrame(
        {
            "CN": ["t1", "t2", "t3", "t4"],
            "PLT_CN": ["101", "101", "102", "102"],
            "STATUSCD": ["1", "2", "1", "1"],
            "INVYR": ["2020", "2020", "2021", "2021"],
            "SPCD": ["131", "131", "999", "131"],
            "DIA": ["10", "11", "12", "8"],
            "HT": [None, "60", "45", "40"],
            "ACTUALHT": ["55", "60", "45", None],
            "CR": ["50", "60", "45", "40"],
            "TPA_UNADJ": ["5", "6", "7", "10"],
        }
    )

    actual = prepare_direct_tree_rows(normalized_weights, fia_trees, {"131": "LP"})
    legacy = _legacy_prepare_tree_rows(normalized_weights, fia_trees, {"131": "LP"})

    pd.testing.assert_frame_equal(actual, legacy, check_dtype=False)


def test_nearest_imputation_matches_leto_generate_near_table():
    """Match LETO's closest polygon donor and tree-list copy semantics."""
    management_units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2", "3"]},
        geometry=[
            box(0, 0, 10, 10),
            box(100, 0, 110, 10),
            box(500, 0, 510, 10),
        ],
        crs="EPSG:5070",
    )
    crosswalk = pd.DataFrame({"MU_ID": ["1", "2", "3"]})
    direct_trees = pd.DataFrame(
        {
            "STAND_ID": ["MU_1", "MU_1", "MU_3"],
            "TREE_ID": ["a", "b", "c"],
            "MU_ID": ["1", "1", "3"],
            "PLT_CN": ["101", "101", "301"],
            "TREE_SOURCE": ["FIA_WEIGHTED_DIRECT"] * 3,
            "DONOR_STAND_ID": [""] * 3,
            "NEAR_DIST": [""] * 3,
        }
    )

    result = impute_missing_tree_rows(management_units, crosswalk, direct_trees)
    nearest_pairs = pd.DataFrame(
        {"MISSING_MU_ID": ["2"], "DONOR_MU_ID": ["1"], "NEAR_DIST": [90.0]}
    )
    legacy = _legacy_impute_tree_rows(direct_trees, nearest_pairs)

    pd.testing.assert_frame_equal(result, legacy, check_dtype=False)
    imputed = result.loc[result["STAND_ID"] == "MU_2"]
    assert imputed["DONOR_STAND_ID"].unique().tolist() == ["MU_1"]
    assert imputed["TREE_SOURCE"].unique().tolist() == ["IMPUTED_NEAREST"]
    assert imputed["TREE_ID"].tolist() == [1, 2]
    assert imputed["NEAR_DIST"].unique().tolist() == pytest.approx([90.0])
    assert imputed["PLT_CN"].tolist() == ["101", "101"]
