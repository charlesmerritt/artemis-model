"""Tests for management-unit to TreeMap/FIA plot weights."""

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


def _write_treemap(path):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=1,
        width=1,
        count=1,
        dtype="int32",
        crs="EPSG:5070",
        transform=from_origin(0, 30, 30, 30),
        nodata=-9999,
    ) as destination:
        destination.write(np.array([[10]], dtype="int32"), 1)


@pytest.fixture
def treemap_inputs(tmp_path):
    treemap_path = tmp_path / "treemap.tif"
    _write_treemap(treemap_path)
    lookup = pd.DataFrame({"VALUE": [10], "PLT_CN": ["10000000000001"]})
    return treemap_path, lookup


def test_build_plot_weights_rejects_ambiguous_treemap_lookup(tmp_path):
    management_units = gpd.GeoDataFrame(
        {"MU_ID": ["1"]},
        geometry=[box(0, 0, 1, 1)],
        crs="EPSG:5070",
    )
    lookup = pd.DataFrame(
        {"VALUE": [10, 10], "PLT_CN": ["10000000000001", "10000000000002"]}
    )

    with pytest.raises(ValueError, match="one raster value to multiple PLT_CNs"):
        build_plot_weights(management_units, tmp_path / "unused.tif", lookup)


def test_build_plot_weights_rejects_duplicate_mu_ids(treemap_inputs):
    treemap_path, lookup = treemap_inputs
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "1"]},
        geometry=[box(0, 0, 15, 30), box(15, 0, 30, 30)],
        crs="EPSG:5070",
    )

    with pytest.raises(ValueError, match="MU_ID values must be non-null and unique"):
        build_plot_weights(units, treemap_path, lookup)


def test_build_plot_weights_requires_management_unit_crs(treemap_inputs):
    treemap_path, lookup = treemap_inputs
    units = gpd.GeoDataFrame({"MU_ID": ["1"]}, geometry=[box(0, 0, 30, 30)])

    with pytest.raises(ValueError, match="Management units must define a CRS"):
        build_plot_weights(units, treemap_path, lookup)


def test_build_plot_weights_rejects_units_outside_treemap(treemap_inputs):
    treemap_path, lookup = treemap_inputs
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1"]}, geometry=[box(100, 100, 130, 130)], crs="EPSG:5070"
    )

    with pytest.raises(ValueError, match="overlap no TreeMap cells"):
        build_plot_weights(units, treemap_path, lookup)


def test_build_plot_weights_reports_missing_lookup_columns(treemap_inputs):
    treemap_path, _ = treemap_inputs
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1"]}, geometry=[box(0, 0, 30, 30)], crs="EPSG:5070"
    )

    with pytest.raises(
        ValueError, match=r"TreeMap lookup missing columns: \['PLT_CN'\]"
    ):
        build_plot_weights(units, treemap_path, pd.DataFrame({"VALUE": [10]}))


def test_build_plot_weights_reports_missing_mu_id(treemap_inputs):
    treemap_path, lookup = treemap_inputs
    units = gpd.GeoDataFrame(geometry=[box(0, 0, 30, 30)], crs="EPSG:5070")

    with pytest.raises(ValueError, match="Management units missing column: MU_ID"):
        build_plot_weights(units, treemap_path, lookup)
