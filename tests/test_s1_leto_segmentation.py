"""Tests for LETO's pure geometry subdivision primitives."""

from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.segmentation.leto import (
    LetoSegmentationConfig,
    SegmentationError,
    _polygon_parts,
    calculate_acres,
    sample_constrained_points,
    split_unit_thiessen,
    subdivide_large_units,
)

SQUARE_METERS_PER_ACRE = 4_046.8564224


def test_calculate_acres_uses_projected_crs_units_without_mutating_input():
    units = gpd.GeoDataFrame(
        {"name": ["one-acre"]},
        geometry=[box(0, 0, 63.614907234075, 63.614907234075)],
        crs="EPSG:5070",
    )

    result = calculate_acres(units)

    assert "Acres" not in units.columns
    assert result.loc[0, "Acres"] == pytest.approx(1.0)
    assert result.loc[0, "name"] == "one-acre"


@pytest.mark.parametrize(
    ("geometry", "record_id"),
    [(None, "null-unit"), (Polygon(), "empty-unit")],
)
def test_calculate_acres_rejects_null_and_empty_geometry(geometry, record_id):
    units = gpd.GeoDataFrame(
        geometry=[geometry],
        index=[record_id],
        crs="EPSG:5070",
    )

    with pytest.raises(SegmentationError, match=rf"record '{record_id}'"):
        calculate_acres(units)


def test_calculate_acres_rejects_non_polygon_geometry():
    units = gpd.GeoDataFrame(
        geometry=[Point(0, 0)],
        index=["point-unit"],
        crs="EPSG:5070",
    )

    with pytest.raises(SegmentationError, match="record 'point-unit'"):
        calculate_acres(units)


def test_calculate_acres_rejects_invalid_polygon_topology():
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    units = gpd.GeoDataFrame(
        geometry=[bowtie],
        index=["invalid-unit"],
        crs="EPSG:5070",
    )

    with pytest.raises(SegmentationError, match="record 'invalid-unit'"):
        calculate_acres(units)


@pytest.mark.parametrize(
    ("geometry", "record_id"),
    [
        (box(0, 0, 1e-200, 1e-200), "zero-area-unit"),
        (box(0, 0, 1e200, 1e200), "infinite-area-unit"),
    ],
)
def test_calculate_acres_rejects_non_positive_and_non_finite_area(geometry, record_id):
    units = gpd.GeoDataFrame(
        geometry=[geometry],
        index=[record_id],
        crs="EPSG:5070",
    )

    with pytest.raises(SegmentationError, match=rf"record '{record_id}'"):
        calculate_acres(units)


def test_calculate_acres_accepts_multipolygon_geometry():
    units = gpd.GeoDataFrame(
        geometry=[MultiPolygon([box(0, 0, 10, 10), box(20, 0, 30, 10)])],
        crs="EPSG:5070",
    )

    assert calculate_acres(units).loc[0, "Acres"] > 0


def test_sample_constrained_points_stays_inside_and_respects_separation():
    geometry = box(0, 0, 100, 100)

    points = sample_constrained_points(
        geometry,
        count=4,
        min_distance=20,
        rng=np.random.default_rng(7),
    )

    assert len(points) == 4
    assert all(geometry.contains(point) for point in points)
    distances = [
        first.distance(second)
        for index, first in enumerate(points)
        for second in points[index + 1 :]
    ]
    assert min(distances) >= 20


def test_sample_constrained_points_returns_empty_list_for_zero_count():
    points = sample_constrained_points(
        box(0, 0, 1, 1),
        count=0,
        min_distance=10,
        rng=np.random.default_rng(2),
    )

    assert points == []


def test_constrained_points_fail_instead_of_looping_forever():
    with pytest.raises(SegmentationError, match="minimum separation"):
        sample_constrained_points(
            box(0, 0, 1, 1),
            count=3,
            min_distance=10,
            rng=np.random.default_rng(1),
        )


def test_split_unit_thiessen_returns_polygonal_coverage():
    parent = MultiPolygon([box(0, 0, 100, 100), box(200, 0, 300, 100)])

    children = split_unit_thiessen(
        parent,
        point_count=4,
        min_distance=10,
        rng=np.random.default_rng(8),
    )

    assert len(children) >= 2
    assert all(child.geom_type == "Polygon" and child.area > 0 for child in children)
    assert unary_union(children).symmetric_difference(parent).area == pytest.approx(0)


def test_polygon_parts_ignore_non_polygon_collection_members():
    polygon = box(0, 0, 1, 1)
    geometry = GeometryCollection([polygon, LineString([(0, 0), (1, 1)]), Point(0, 0)])

    assert _polygon_parts(geometry) == [polygon]


def test_subdivide_large_units_keeps_units_at_threshold():
    side = np.sqrt(200 * SQUARE_METERS_PER_ACRE)
    units = gpd.GeoDataFrame(
        {"source": ["threshold"]},
        geometry=[box(0, 0, side, side)],
        crs="EPSG:5070",
    )

    result = subdivide_large_units(
        units,
        LetoSegmentationConfig(max_acres=200, min_distance_feet=100),
    )

    assert len(result) == 1
    assert result.loc[0, "source"] == "threshold"
    assert result.geometry.iloc[0].equals_exact(units.geometry.iloc[0], tolerance=0)
    assert result.loc[0, "Acres"] == pytest.approx(200)
    assert result.loc[0, "Acres"] <= 200


def test_subdivide_large_units_is_repeatable_and_preserves_coverage():
    units = gpd.GeoDataFrame(
        {"source": ["large"]},
        geometry=[box(0, 0, 1_200, 1_200)],
        crs="EPSG:5070",
    )
    config = LetoSegmentationConfig(
        max_acres=200,
        acres_per_point=100,
        min_distance_feet=100,
        seed=42,
    )

    first = subdivide_large_units(units, config)
    second = subdivide_large_units(units, config)

    assert first.geometry.to_wkb().tolist() == second.geometry.to_wkb().tolist()
    assert first.geometry.union_all().symmetric_difference(
        units.geometry.iloc[0]
    ).area == pytest.approx(0)
    assert first["Acres"].max() <= 200
    assert set(first["source"]) == {"large"}
