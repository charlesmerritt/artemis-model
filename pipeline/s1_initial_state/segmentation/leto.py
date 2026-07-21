"""Pure-Python LETO management-unit segmentation and attribution."""

import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.errors import WindowError
from rasterio.features import geometry_mask, geometry_window, shapes
from shapely import union_all, voronoi_polygons
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry

from pipeline.s1_initial_state.weights import build_plot_weights

SQUARE_METERS_PER_ACRE = 4_046.872609874251
METERS_PER_FOOT = 0.3048
MAX_POINT_ATTEMPTS_PER_POINT = 1_000
MAX_SUBDIVISION_ROUNDS = 100
OWNERSHIP_LOOKUP = {
    0: "Unknown Forest",
    1: "Non-Forest",
    2: "Water",
    3: "Family Forest",
    4: "Corporate/Other Private Forest",
    5: "Tribal Forest",
    6: "Federal Forest",
    7: "State Forest",
    8: "Local Forest",
}


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


def _validate_geometries(units: gpd.GeoDataFrame) -> None:
    for record_id, geometry in units.geometry.items():
        if geometry is None:
            raise SegmentationError(f"Geometry for record {record_id!r} is null")
        if geometry.is_empty:
            raise SegmentationError(f"Geometry for record {record_id!r} is empty")
        if not isinstance(geometry, (Polygon, MultiPolygon)):
            raise SegmentationError(
                f"Geometry for record {record_id!r} must be Polygon or MultiPolygon"
            )
        if not geometry.is_valid:
            raise SegmentationError(
                f"Geometry for record {record_id!r} has invalid topology"
            )
        with np.errstate(over="ignore", invalid="ignore"):
            area = geometry.area
        if not math.isfinite(area) or area <= 0:
            raise SegmentationError(
                f"Geometry for record {record_id!r} must have finite positive area"
            )


def calculate_acres(units: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy with polygon area expressed in acres."""
    result = units.copy()
    _validate_geometries(result)
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
    if count == 0:
        return []
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
    if not isinstance(geometry, BaseMultipartGeometry):
        return []
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


def _require_crs(features: gpd.GeoDataFrame, label: str) -> None:
    if features.crs is None:
        raise ValueError(f"{label} must define a CRS")


def _polygonal_geometry(geometry: BaseGeometry) -> BaseGeometry:
    parts = _polygon_parts(geometry)
    if not parts:
        return Polygon()
    if len(parts) == 1:
        return parts[0]
    return MultiPolygon(parts)


def build_treemap_domain(
    treemap_path: Path,
    clip_features: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Polygonize valid TreeMap cells inside the parcel area of interest."""
    _require_crs(clip_features, "Clip features")
    if clip_features.empty:
        raise ValueError("Clip features must contain at least one geometry")

    with rasterio.open(treemap_path) as source:
        if source.crs is None:
            raise ValueError("TreeMap raster must define a CRS")
        domain_crs = source.crs
        clips = clip_features.to_crs(source.crs)
        clip_area = clips.geometry.union_all()
        if clip_area.is_empty:
            raise ValueError("Clip features contain no polygonal area")
        try:
            window = geometry_window(source, [clip_area])
        except WindowError as error:
            raise ValueError("Clip features overlap no TreeMap cells") from error
        treemap = source.read(1, window=window, masked=True)
        valid = ~np.ma.getmaskarray(treemap)
        if not valid.any():
            raise ValueError("Clip features overlap no valid TreeMap cells")
        transform = source.window_transform(window)
        valid_cells = [
            shape(geometry)
            for geometry, value in shapes(
                valid.astype("uint8"), mask=valid, transform=transform
            )
            if value == 1
        ]

    domain = _polygonal_geometry(union_all(valid_cells).intersection(clip_area))
    if domain.is_empty:
        raise ValueError("Clip features overlap no valid TreeMap cells")
    return gpd.GeoDataFrame(geometry=[domain], crs=domain_crs)


def cleanup_and_clip_units(
    units: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame,
    min_acres: float,
) -> gpd.GeoDataFrame:
    """Apply LETO's singlepart/minimum cleanup, then its final parcel clip."""
    if min_acres < 0:
        raise ValueError("Minimum acreage must be non-negative")
    _require_crs(units, "Management units")
    _require_crs(parcels, "Parcels")
    if parcels.empty:
        raise ValueError("Parcels must contain at least one geometry")

    singlepart = units.explode(index_parts=False).reset_index(drop=True)
    singlepart = calculate_acres(singlepart)
    retained = singlepart.loc[singlepart["Acres"] >= min_acres].copy()
    if retained.empty:
        return retained.reset_index(drop=True)

    parcel_area = parcels.to_crs(units.crs).geometry.union_all()
    retained[retained.geometry.name] = retained.geometry.intersection(parcel_area)
    retained[retained.geometry.name] = retained.geometry.map(_polygonal_geometry)
    retained = retained.loc[~retained.geometry.is_empty].reset_index(drop=True)
    if retained.empty:
        return retained
    return calculate_acres(retained)


def _unit_majority_code(source, geometry: BaseGeometry) -> int | None:
    try:
        window = geometry_window(source, [geometry])
    except WindowError:
        return None
    values = source.read(1, window=window, masked=True)
    inside = geometry_mask(
        [geometry],
        out_shape=values.shape,
        transform=source.window_transform(window),
        all_touched=False,
        invert=True,
    )
    valid = inside & ~np.ma.getmaskarray(values)
    if not valid.any():
        return None
    codes, counts = np.unique(values.data[valid], return_counts=True)
    largest_count = counts.max()
    return int(codes[counts == largest_count].min())


def assign_majority_ownership(
    units: gpd.GeoDataFrame,
    ownership_path: Path,
) -> gpd.GeoDataFrame:
    """Assign each unit's pixel-center majority ownership with stable ties."""
    _require_crs(units, "Management units")
    result = units.copy()
    with rasterio.open(ownership_path) as source:
        if source.crs is None:
            raise ValueError("Ownership raster must define a CRS")
        raster_units = units.to_crs(source.crs)
        codes = [
            _unit_majority_code(source, geometry)
            for geometry in raster_units.geometry
        ]
    result["OWN_CODE"] = pd.array(codes, dtype="Int64")
    result["OWN_TYPE"] = [
        None if code is None else OWNERSHIP_LOOKUP.get(code, "Unknown")
        for code in codes
    ]
    return result


def assign_smz_percent(
    units: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    buffer_feet: float,
) -> gpd.GeoDataFrame:
    """Calculate unit acreage and percent area inside the dissolved SMZ buffer."""
    if buffer_feet < 0:
        raise ValueError("SMZ buffer must be non-negative")
    _require_crs(units, "Management units")
    _require_crs(streams, "Streams")
    result = calculate_acres(units)
    meters_per_unit = _meters_per_coordinate_unit(result)
    buffer_distance = buffer_feet * METERS_PER_FOOT / meters_per_unit
    stream_geometry = streams.to_crs(result.crs).geometry
    stream_geometry = stream_geometry.loc[
        stream_geometry.notna() & ~stream_geometry.is_empty
    ]
    if stream_geometry.empty:
        intersection_area = np.zeros(len(result), dtype="float64")
    else:
        smz = union_all(stream_geometry.buffer(buffer_distance))
        intersection_area = result.geometry.intersection(smz).area.to_numpy()
    square_meters = intersection_area * meters_per_unit**2
    result["SMZ_Acres"] = square_meters / SQUARE_METERS_PER_ACRE
    result["SMZ_Pct"] = np.divide(
        intersection_area,
        result.geometry.area.to_numpy(),
        out=np.zeros(len(result), dtype="float64"),
        where=result.geometry.area.to_numpy() != 0,
    )
    result["SMZ_Pct"] *= 100
    return result


def _assign_stable_mu_ids(units: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    result = units.copy()
    bounds = result.geometry.bounds
    sort_columns = []
    for name in ("minx", "miny", "maxx", "maxy"):
        column = f"__leto_{name}"
        result[column] = bounds[name]
        sort_columns.append(column)
    result["__leto_wkb"] = result.geometry.to_wkb(hex=True)
    result = result.sort_values([*sort_columns, "__leto_wkb"]).reset_index(drop=True)
    result = result.drop(columns=[*sort_columns, "__leto_wkb"])
    result["MU_ID"] = pd.array(
        [str(index) for index in range(1, len(result) + 1)], dtype="string"
    )
    result["SEGMENTATION_METHOD"] = "leto"
    return result


def build_leto_management_units(
    treemap_path: Path,
    treemap_lookup: pd.DataFrame,
    parcels: gpd.GeoDataFrame,
    ownership_path: Path,
    streams: gpd.GeoDataFrame,
    config: LetoSegmentationConfig,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Run LETO stages in their legacy order and return units plus plot weights."""
    domain = build_treemap_domain(treemap_path, parcels)
    subdivided = subdivide_large_units(domain, config)
    cleaned = cleanup_and_clip_units(subdivided, parcels, config.min_acres)
    units = _assign_stable_mu_ids(cleaned)

    weights = build_plot_weights(units, treemap_path, treemap_lookup)
    majority = (
        weights.sort_values(
            ["MU_ID", "CELL_COUNT", "TM_VALUE"],
            ascending=[True, False, True],
        )
        .drop_duplicates("MU_ID")[["MU_ID", "PLT_CN", "TM_VALUE"]]
        .copy()
    )
    units = units.merge(majority, on="MU_ID", how="left", validate="one_to_one")
    units = assign_majority_ownership(units, ownership_path)
    units = assign_smz_percent(units, streams, config.smz_buffer_feet)
    return units, weights
