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
