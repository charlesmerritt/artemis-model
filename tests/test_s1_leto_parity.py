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
    impute_missing_tree_rows,
    prepare_direct_tree_rows,
)


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

    actual = prepare_direct_tree_rows(
        normalized_weights, fia_trees, {"131": "LP"}
    )

    assert actual["TREE_ID"].tolist() == ["t1", "t4"]
    assert actual["STAND_ID"].tolist() == ["MU_1", "MU_1"]
    assert actual["SPECIES"].tolist() == ["LP", "LP"]
    assert actual["TREE_COUNT"].tolist() == pytest.approx([4.0, 2.0])
    assert actual["HT"].tolist() == pytest.approx([55.0, 40.0])
    assert actual["TREE_SOURCE"].tolist() == [
        "FIA_WEIGHTED_DIRECT",
        "FIA_WEIGHTED_DIRECT",
    ]
    assert actual["DONOR_STAND_ID"].tolist() == ["", ""]


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

    imputed = result.loc[result["STAND_ID"] == "MU_2"]
    assert imputed["DONOR_STAND_ID"].unique().tolist() == ["MU_1"]
    assert imputed["TREE_SOURCE"].unique().tolist() == ["IMPUTED_NEAREST"]
    assert imputed["TREE_ID"].tolist() == [1, 2]
    assert imputed["NEAR_DIST"].unique().tolist() == pytest.approx([90.0])
    assert imputed["PLT_CN"].tolist() == ["101", "101"]
