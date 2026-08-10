"""
Vector-layer helpers shared by the s5 imagery and embedding tools.

Two distinct vector layers drive this stage, and keeping them separate is
deliberate:

  imagery extent   The footprint that imagery must *completely* cover. NAIP is
                   delivered as quarter-quad tiles, so any mosaic that covers a
                   study area necessarily spills past it.

  area of interest The actual features on the ground under study. Embeddings are
                   generated across the whole extent, then split by this layer so
                   inside-AOI and outside-AOI clustering can be compared. The
                   spill is the comparison, not an artifact to be trimmed away.

When only an AOI is supplied the extent is derived from it (see ``derive_extent``)
so a single-layer workflow still works.

All geometry leaves this module in EPSG:4326 because that is what Earth Engine and
the web viewer both expect. Area and buffer math routes through EPSG:5070, the
project's equal-area CRS (config/projection.yaml), because degrees are not a unit
of area.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# Earth Engine and the viewer both speak lon/lat.
WGS84 = "EPSG:4326"

# CONUS Albers Equal Area — the project CRS (config/projection.yaml). Used only
# for area and buffer math, never for output geometry.
EQUAL_AREA_CRS = "EPSG:5070"

# Modes for turning an AOI into an imagery extent.
EXTENT_MODES = ("bbox", "buffer", "hull")

SQ_M_PER_HA = 10_000.0


def slugify(text: str) -> str:
    """
    Turn a name into a filesystem- and URL-safe slug.

    Used for output directory and export-description naming, so it must never
    produce an empty string (Earth Engine rejects empty task descriptions).
    """
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return cleaned or "aoi"


def load_layer(path: str | Path, layer: str | None = None) -> gpd.GeoDataFrame:
    """
    Read a vector layer and return it in EPSG:4326.

    Accepts anything pyogrio/GDAL reads: GeoJSON, GeoPackage, shapefile, FlatGeobuf.
    ``layer`` selects one layer from a multi-layer source such as a GeoPackage.

    Raises ValueError when the source is empty or has no CRS, because silently
    assuming a CRS would put the AOI in the wrong hemisphere without complaint.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Vector layer not found: {path}")

    gdf = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)

    if gdf.empty:
        raise ValueError(f"Vector layer has no features: {path}")
    if gdf.crs is None:
        raise ValueError(
            f"Vector layer has no CRS: {path}. Assign one before use — an unknown "
            "CRS cannot be reprojected to EPSG:4326."
        )

    if gdf.crs.to_string() != WGS84:
        gdf = gdf.to_crs(WGS84)
    return gdf


def layer_geometry(gdf: gpd.GeoDataFrame) -> BaseGeometry:
    """
    Dissolve a layer to a single geometry.

    Invalid rings are repaired with a zero-width buffer first; hand-digitized AOIs
    frequently self-intersect, and unary_union propagates that failure otherwise.
    """
    geoms = [g for g in gdf.geometry if g is not None and not g.is_empty]
    if not geoms:
        raise ValueError("Vector layer contains no usable geometry")

    repaired = [g if g.is_valid else g.buffer(0) for g in geoms]
    dissolved = unary_union(repaired)
    if dissolved.is_empty:
        raise ValueError("Dissolved geometry is empty")
    return dissolved


def _to_equal_area(geom: BaseGeometry) -> BaseGeometry:
    return gpd.GeoSeries([geom], crs=WGS84).to_crs(EQUAL_AREA_CRS).iloc[0]


def _to_wgs84(geom: BaseGeometry) -> BaseGeometry:
    return gpd.GeoSeries([geom], crs=EQUAL_AREA_CRS).to_crs(WGS84).iloc[0]


def area_ha(geom: BaseGeometry) -> float:
    """Area in hectares, measured in the equal-area CRS."""
    return float(_to_equal_area(geom).area / SQ_M_PER_HA)


def derive_extent(
    aoi_geom: BaseGeometry,
    mode: str = "bbox",
    buffer_m: float = 0.0,
) -> BaseGeometry:
    """
    Derive an imagery extent from an AOI.

    mode="bbox"   Bounding box of the AOI. The default: it guarantees generous
                  outside-AOI area for the inside/outside comparison.
    mode="hull"   Convex hull. Tighter than a bbox for elongated AOIs.
    mode="buffer" The AOI itself, buffered. Tightest; use when outside-AOI
                  context should hug the boundary.

    ``buffer_m`` is applied after the mode, in meters, in the equal-area CRS. It is
    required for mode="buffer" (a zero buffer there would return the AOI itself,
    leaving no outside pixels to compare against).
    """
    if mode not in EXTENT_MODES:
        raise ValueError(f"Unknown extent mode {mode!r}; expected one of {EXTENT_MODES}")
    if mode == "buffer" and buffer_m <= 0:
        raise ValueError(
            "mode='buffer' needs --buffer-m > 0, otherwise the extent equals the AOI "
            "and there are no outside-AOI pixels to cluster against."
        )
    if buffer_m < 0:
        raise ValueError("buffer_m must be >= 0")

    projected = _to_equal_area(aoi_geom)

    if mode == "bbox":
        base = shape(mapping(projected.envelope))
    elif mode == "hull":
        base = projected.convex_hull
    else:
        base = projected

    if buffer_m > 0:
        base = base.buffer(buffer_m)

    return _to_wgs84(base)


def containment_fraction(inner: BaseGeometry, outer: BaseGeometry) -> float:
    """
    Fraction of ``inner``'s area that falls inside ``outer``.

    1.0 means fully contained. Used to warn when an AOI pokes outside the imagery
    extent, which would silently drop part of the study area from every product.
    """
    inner_proj = _to_equal_area(inner)
    if inner_proj.area <= 0:
        return 0.0
    outer_proj = _to_equal_area(outer)
    return float(inner_proj.intersection(outer_proj).area / inner_proj.area)


def bounds_list(geom: BaseGeometry) -> list[float]:
    """Bounds as [minx, miny, maxx, maxy] — the order MapLibre and TiTiler use."""
    minx, miny, maxx, maxy = geom.bounds
    return [float(minx), float(miny), float(maxx), float(maxy)]


def estimate_pixel_count(geom: BaseGeometry, scale_m: float) -> int:
    """
    Approximate pixel count for a geometry rasterized at ``scale_m``.

    Uses the bounding box, not the geometry, because exports are rectangular. This
    is the guard that catches "1 m NAIP over a whole county" before Earth Engine
    accepts a task that will never finish.
    """
    if scale_m <= 0:
        raise ValueError("scale_m must be > 0")
    envelope_m2 = _to_equal_area(geom).envelope.area
    return int(envelope_m2 / (scale_m * scale_m))


def feature_collection(
    geom: BaseGeometry, properties: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Wrap a geometry as a one-feature GeoJSON FeatureCollection for the viewer."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": dict(properties or {}),
                "geometry": mapping(geom),
            }
        ],
    }


def to_ee_geometry(geom: BaseGeometry):
    """
    Convert a shapely geometry to ee.Geometry.

    Imported lazily so the pure helpers above stay usable (and testable) without
    Earth Engine credentials.
    """
    import ee

    return ee.Geometry(mapping(geom), proj=WGS84, geodesic=False)
