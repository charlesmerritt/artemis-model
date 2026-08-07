"""Tests for draft Florida management-unit helpers."""

from pathlib import Path
import sys

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.sketch_management_units import (
    BUFFER_CLASS_PRIORITY,
    build_buffer_polygons,
    classify_stream_fcode,
    classify_unit_size,
    clean_geometries,
    feet_to_meters,
    split_large_geometry,
    target_grid_cell_size_m,
)

CRS = "EPSG:5070"


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


# ---- riparian buffers as retained units ------------------------------------------------

WIDTHS = {"ephemeral_intermittent": 10.0, "perennial_small": 20.0, "perennial_large": 30.0}


def _streams(classes_and_lines):
    return gpd.GeoDataFrame(
        {"buffer_class": [c for c, _ in classes_and_lines]},
        geometry=[line for _, line in classes_and_lines],
        crs=CRS,
    )


def test_buffer_polygons_carry_their_class():
    streams = _streams([("perennial_small", LineString([(0, 0), (100, 0)]))])
    buffers = build_buffer_polygons(streams, WIDTHS)
    assert list(buffers["buffer_class"]) == ["perennial_small"]
    assert buffers.geometry.area.sum() > 0


def test_overlapping_buffer_classes_do_not_double_count_area():
    """Buffers must partition, so overlap has to resolve to exactly one class."""
    streams = _streams([
        ("perennial_large", LineString([(0, 0), (100, 0)])),
        ("ephemeral_intermittent", LineString([(0, 5), (100, 5)])),   # overlaps the above
    ])
    buffers = build_buffer_polygons(streams, WIDTHS)
    summed = buffers.geometry.area.sum()
    unioned = buffers.geometry.union_all().area
    assert summed == pytest.approx(unioned, rel=1e-9)


def test_the_widest_class_wins_contested_ground():
    """The conservative direction: more protection, not less, where buffers disagree."""
    streams = _streams([
        ("perennial_large", LineString([(0, 0), (100, 0)])),
        ("ephemeral_intermittent", LineString([(0, 5), (100, 5)])),
    ])
    buffers = build_buffer_polygons(streams, WIDTHS)
    by_class = buffers.groupby("buffer_class").apply(
        lambda g: g.geometry.area.sum(), include_groups=False
    )
    alone = build_buffer_polygons(
        _streams([("perennial_large", LineString([(0, 0), (100, 0)]))]), WIDTHS
    ).geometry.area.sum()
    assert by_class["perennial_large"] == pytest.approx(alone)   # kept whole
    assert by_class.get("ephemeral_intermittent", 0.0) < alone   # yielded the overlap


def test_buffer_class_priority_is_widest_first():
    assert BUFFER_CLASS_PRIORITY.index("perennial_large") < \
           BUFFER_CLASS_PRIORITY.index("perennial_small") < \
           BUFFER_CLASS_PRIORITY.index("ephemeral_intermittent")


def test_waterbody_buffer_is_the_ring_not_the_water():
    """Open water is non-forest; the SMZ around it is forest under a no-entry rule."""
    water = gpd.GeoDataFrame(geometry=[Polygon([(0, 0), (50, 0), (50, 50), (0, 50)])], crs=CRS)
    streams = _streams([])
    buffers = build_buffer_polygons(streams, WIDTHS, waterbodies=water, waterbody_width_m=10.0)
    assert list(buffers["buffer_class"]) == ["waterbody"]
    assert not buffers.geometry.iloc[0].intersection(water.geometry.iloc[0]).area > 1e-9


def test_no_buffers_yields_an_empty_layer_with_the_right_schema():
    buffers = build_buffer_polygons(_streams([]), WIDTHS)
    assert len(buffers) == 0
    assert "buffer_class" in buffers.columns


def test_buffer_builder_keeps_only_polygonal_parts_of_a_geometry_collection():
    """Differencing polygon sets that share boundaries can return a GeometryCollection.

    That type does not start with "Multi", so a naive multipart check passed it through
    whole — and a collection carrying a LineString is not a stand. (review, minor)
    """
    from shapely.geometry import GeometryCollection, Point

    from pipeline.s3_management.sketch_management_units import _polygonal_parts

    collection = GeometryCollection([
        box(0, 0, 10, 10),
        LineString([(20, 0), (30, 0)]),      # degenerate remnant
        Point(40, 40),
    ])
    parts = _polygonal_parts(collection)
    assert len(parts) == 1
    assert parts[0].geom_type == "Polygon"


def test_polygonal_parts_flattens_nested_multipolygons():
    from shapely.geometry import GeometryCollection, MultiPolygon

    from pipeline.s3_management.sketch_management_units import _polygonal_parts

    nested = GeometryCollection([MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)])])
    assert len(_polygonal_parts(nested)) == 2


def test_polygonal_parts_drops_zero_area_polygons():
    from shapely.geometry import Polygon

    from pipeline.s3_management.sketch_management_units import _polygonal_parts

    assert _polygonal_parts(Polygon()) == []
