"""Tests for draft Florida management-unit helpers."""

from pathlib import Path
import sys

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.sketch_management_units import (
    classify_stream_fcode,
    classify_unit_size,
    clean_geometries,
    feet_to_meters,
    split_large_geometry,
    target_grid_cell_size_m,
)


def test_feet_to_meters_converts_florida_bmp_width():
    assert feet_to_meters(50) == pytest.approx(15.24)


@pytest.mark.parametrize(
    ("fcode", "expected"),
    [
        (46000, "ephemeral_intermittent"),
        (46003, "ephemeral_intermittent"),
        (46007, "ephemeral_intermittent"),
        (46006, "perennial_small"),
        (55800, None),
        (None, None),
    ],
)
def test_classify_stream_fcode_uses_documented_florida_mapping(fcode, expected):
    assert classify_stream_fcode(fcode) == expected


@pytest.mark.parametrize(
    ("area_ha", "expected"),
    [(1.99, "sliver_lt_min"), (2.0, "candidate"), (40.0, "candidate"), (40.01, "large_gt_target")],
)
def test_classify_unit_size_uses_min_and_target_thresholds(area_ha, expected):
    assert classify_unit_size(area_ha, min_area_ha=2.0, target_max_area_ha=40.0) == expected


def test_target_grid_cell_size_matches_target_area():
    side_m = target_grid_cell_size_m(40.0)
    assert side_m == pytest.approx((40.0 * 10_000) ** 0.5)


def test_clean_geometries_preserves_line_features_for_buffer_inputs():
    gdf = gpd.GeoDataFrame({"name": ["road"]}, geometry=[LineString([(0, 0), (1, 1)])], crs="EPSG:5070")

    cleaned = clean_geometries(gdf)

    assert len(cleaned) == 1
    assert cleaned.geom_type.iloc[0] == "LineString"



def test_split_large_geometry_keeps_parts_at_or_below_target_area():
    # 1,000 m x 1,000 m = 100 ha, so a 40 ha target should split it.
    geometry = box(0, 0, 1_000, 1_000)

    parts = split_large_geometry(geometry, target_max_area_ha=40.0)

    assert len(parts) > 1
    assert sum(part.area for part in parts) == pytest.approx(geometry.area)
    assert max(part.area for part in parts) <= 40.0 * 10_000 + 1e-6
