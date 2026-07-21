"""Pure-Python geometry primitives for LETO management-unit subdivision."""

import math
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
from shapely import union_all, voronoi_polygons
from shapely.geometry import MultiPoint, Point, Polygon
from shapely.geometry.base import BaseGeometry

SQUARE_METERS_PER_ACRE = 4_046.8564224
METERS_PER_FOOT = 0.3048
MAX_POINT_ATTEMPTS_PER_POINT = 1_000
MAX_SUBDIVISION_ROUNDS = 100


class SegmentationError(RuntimeError):
    """Raised when LETO geometry cannot satisfy its subdivision contract."""


@dataclass(frozen=True)
class LetoSegmentationConfig:
    """Thresholds used by LETO management-unit segmentation."""

    max_acres: float = 200.0
    acres_per_point: float = 100.0
    min_distance_feet: float = 1_000.0
    min_acres: float = 5.0
    smz_buffer_feet: float = 35.0
    seed: int = 0


def _meters_per_coordinate_unit(units: gpd.GeoDataFrame) -> float:
    if units.crs is None or not units.crs.is_projected:
        raise ValueError("LETO geometry calculations require a projected CRS")
    axis_info = units.crs.axis_info
    if not axis_info or axis_info[0].unit_conversion_factor is None:
        raise ValueError("Projected CRS must declare its linear units")
    return float(axis_info[0].unit_conversion_factor)


def calculate_acres(units: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy with polygon area expressed in acres."""
    result = units.copy()
    meters_per_unit = _meters_per_coordinate_unit(result)
    result["Acres"] = result.geometry.area * meters_per_unit**2
    result["Acres"] /= SQUARE_METERS_PER_ACRE
    return result


def sample_constrained_points(
    geometry: BaseGeometry,
    count: int,
    min_distance: float,
    rng: np.random.Generator,
) -> list[Point]:
    """Sample interior points with bounded rejection for minimum separation."""
    if count < 0:
        raise ValueError("Point count must be non-negative")
    if min_distance < 0:
        raise ValueError("Minimum separation must be non-negative")
    if geometry.is_empty:
        raise SegmentationError("Cannot sample points from empty geometry")

    min_x, min_y, max_x, max_y = geometry.bounds
    points: list[Point] = []
    max_attempts = max(1, count * MAX_POINT_ATTEMPTS_PER_POINT)
    for _ in range(max_attempts):
        candidate = Point(rng.uniform(min_x, max_x), rng.uniform(min_y, max_y))
        if not geometry.contains(candidate):
            continue
        if any(candidate.distance(point) < min_distance for point in points):
            continue
        points.append(candidate)
        if len(points) == count:
            return points

    raise SegmentationError(
        f"Could not place {count} points with minimum separation {min_distance}"
    )


def _polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    parts = []
    for part in geometry.geoms:
        parts.extend(_polygon_parts(part))
    return parts


def split_unit_thiessen(
    geometry: BaseGeometry,
    point_count: int,
    min_distance: float,
    rng: np.random.Generator,
) -> list[Polygon]:
    """Split a polygon with constrained random-point Thiessen cells."""
    if point_count < 2:
        raise ValueError("Thiessen subdivision requires at least two points")
    points = sample_constrained_points(geometry, point_count, min_distance, rng)
    cells = voronoi_polygons(MultiPoint(points), extend_to=geometry.envelope)
    children = [
        polygon
        for cell in cells.geoms
        for polygon in _polygon_parts(cell.intersection(geometry))
        if polygon.area > 0
    ]
    if not children:
        raise SegmentationError("Thiessen subdivision produced no polygonal children")

    tolerance = max(1e-6, geometry.area * 1e-9)
    coverage_error = union_all(children).symmetric_difference(geometry).area
    if coverage_error > tolerance:
        raise SegmentationError(
            f"Thiessen subdivision changed parent coverage by {coverage_error}"
        )
    return children


def _validate_config(config: LetoSegmentationConfig) -> None:
    if config.max_acres <= 0 or config.acres_per_point <= 0:
        raise ValueError("Acre thresholds must be positive")
    if config.min_distance_feet < 0:
        raise ValueError("Minimum separation must be non-negative")


def _is_oversized(acres: float, max_acres: float) -> bool:
    tolerance = max(1e-9, max_acres * 1e-12)
    return acres - max_acres > tolerance


def subdivide_large_units(
    units: gpd.GeoDataFrame,
    config: LetoSegmentationConfig,
) -> gpd.GeoDataFrame:
    """Repeatedly split units until none exceeds LETO's acreage threshold."""
    _validate_config(config)
    meters_per_unit = _meters_per_coordinate_unit(units)
    square_units_per_acre = SQUARE_METERS_PER_ACRE / meters_per_unit**2
    min_distance = config.min_distance_feet * METERS_PER_FOOT / meters_per_unit
    geometry_column = units.geometry.name
    records = calculate_acres(units).to_dict("records")
    split_index = 0

    for _ in range(MAX_SUBDIVISION_ROUNDS):
        if not any(
            _is_oversized(record["Acres"], config.max_acres) for record in records
        ):
            for record in records:
                if record["Acres"] > config.max_acres:
                    record["Acres"] = config.max_acres
            return gpd.GeoDataFrame(records, geometry=geometry_column, crs=units.crs)

        next_records = []
        for record in records:
            if not _is_oversized(record["Acres"], config.max_acres):
                next_records.append(record)
                continue
            point_count = max(2, math.ceil(record["Acres"] / config.acres_per_point))
            rng = np.random.default_rng(
                np.random.SeedSequence([config.seed, split_index])
            )
            children = split_unit_thiessen(
                record[geometry_column], point_count, min_distance, rng
            )
            split_index += 1
            parent_area = record[geometry_column].area
            tolerance = max(1e-6, parent_area * 1e-9)
            if len(children) < 2 or max(child.area for child in children) >= (
                parent_area - tolerance
            ):
                raise SegmentationError("Thiessen subdivision did not reduce unit size")
            for child in children:
                child_record = record.copy()
                child_record[geometry_column] = child
                child_record["Acres"] = child.area / square_units_per_acre
                next_records.append(child_record)
        records = next_records

    raise SegmentationError(
        f"Subdivision did not reach {config.max_acres} acres within "
        f"{MAX_SUBDIVISION_ROUNDS} rounds"
    )
