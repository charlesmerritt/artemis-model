"""LETO's riparian split: NHD buffers by FCode, then a vector split of the parent stands.

Ports aauslander480/Leto `LETO_v1_project/leto/stage2_segmentation.py`
(`create_riparian_stands`, `_classify_hydrography`, `_calculate_split_fields`):

1. Flowlines are buffered from their centreline; NHDArea and NHDWaterbody polygons from
   their shoreline (the buffer includes the polygon). Each feature takes its FCode's distance;
   unlisted or zero-distance FCodes are dropped. All buffers dissolve into one footprint
   (PLANAR buffers, as LETO runs them).
2. Each parent stand polygon is intersected with the footprint (riparian pieces) and erased
   by it (upland pieces). The pieces are exploded to single parts, and each gets geodesic
   acres. No minimum-area deletion follows: narrow riparian pieces are intended units.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Geod
from shapely import union_all
from shapely.geometry import box

FEET_TO_METERS = 0.3048
SQUARE_METERS_PER_ACRE = 4046.8564224
UPLAND, RIPARIAN = 0, 1


@dataclass(frozen=True)
class BufferRules:
    """FCode -> (class label, buffer distance in feet), per NHD layer."""

    flowline: dict[int, tuple[str, float]] = field(default_factory=dict)
    waterbody: dict[int, tuple[str, float]] = field(default_factory=dict)
    area: dict[int, tuple[str, float]] = field(default_factory=dict)


# LETO's Florida first-pass assumptions (legacy Leto/scripts/Cellular_automata/02_segment_treemap.py,
# FLOWLINE/WATERBODY/AREA_BUFFER_RULES); v1 keeps buffer rules in regional configuration.
FLORIDA_FIRST_PASS_RULES = BufferRules(
    flowline={46000: ("Unclassified Stream", 35.0), 46003: ("Intermittent Stream", 35.0),
              46006: ("Perennial Stream", 75.0), 46007: ("Ephemeral Stream", 0.0)},
    waterbody={39000: ("Unclassified Lake/Pond", 35.0), 39001: ("Intermittent Lake/Pond", 35.0),
               39005: ("Intermittent Lake/Pond", 35.0), 39006: ("Intermittent Lake/Pond", 35.0),
               39004: ("Perennial Lake/Pond", 75.0), 39009: ("Perennial Lake/Pond", 75.0),
               39010: ("Perennial Lake/Pond", 75.0), 39011: ("Perennial Lake/Pond", 75.0),
               39012: ("Perennial Lake/Pond", 75.0)},
    area={46000: ("Unclassified Stream/River Area", 35.0),
          46003: ("Intermittent Stream/River Area", 35.0),
          46006: ("Perennial Stream/River Area", 75.0),
          46007: ("Ephemeral Stream/River Area", 0.0),
          53700: ("Area of Complex Channels", 75.0)},
)

# LETO v1's verified Maine baseline (LETO_v1_project/configs/maine_test.py, "riparian"):
# perennial streams, lakes and complex channels at 45 ft; everything else unbuffered.
MAINE_V1_RULES = BufferRules(
    flowline={46000: ("Unclassified Stream", 0.0), 46003: ("Intermittent Stream", 0.0),
              46006: ("Perennial Stream", 45.0), 46007: ("Ephemeral Stream", 0.0)},
    waterbody={39000: ("Unclassified Lake/Pond", 0.0), 39001: ("Intermittent Lake/Pond", 0.0),
               39005: ("Intermittent Lake/Pond", 0.0), 39006: ("Intermittent Lake/Pond", 0.0),
               39004: ("Perennial Lake/Pond", 45.0), 39009: ("Perennial Lake/Pond", 45.0),
               39010: ("Perennial Lake/Pond", 45.0), 39011: ("Perennial Lake/Pond", 45.0),
               39012: ("Perennial Lake/Pond", 45.0)},
    area={46000: ("Unclassified Stream/River Area", 0.0),
          46003: ("Intermittent Stream/River Area", 0.0),
          46006: ("Perennial Stream/River Area", 45.0),
          46007: ("Ephemeral Stream/River Area", 0.0),
          53700: ("Area of Complex Channels", 45.0)},
)



def _buffers(features, rules: dict[int, tuple[str, float]]) -> list:
    if features is None or len(features) == 0:
        return []
    fcodes = pd.to_numeric(pd.Series(features["fcode"]), errors="coerce").to_numpy()
    distances = np.array([rules.get(int(c), ("", 0.0))[1] if np.isfinite(c) else 0.0
                          for c in fcodes])
    keep = distances > 0
    geoms = np.asarray(features["geometry"], dtype=object)[keep]
    return [g.buffer(d * FEET_TO_METERS) for g, d in zip(geoms, distances[keep])
            if g is not None and not g.is_empty]


def riparian_footprint(rules: BufferRules, *, flowlines=None, waterbodies=None, areas=None):
    """Dissolved riparian footprint (shapely geometry) in the inputs' projected CRS."""
    parts = (_buffers(flowlines, rules.flowline) + _buffers(waterbodies, rules.waterbody)
             + _buffers(areas, rules.area))
    if not parts:
        raise ValueError("No NHD feature has a positive buffer rule")
    return union_all(parts)


def geodesic_acres(frame: gpd.GeoDataFrame) -> pd.Series:
    """Ellipsoidal (WGS84) area in acres, like ArcGIS `getArea("GEODESIC", "ACRES")`."""
    geod = Geod(ellps="WGS84")
    lonlat = frame.to_crs(4326)
    return pd.Series([abs(geod.geometry_area_perimeter(g)[0]) / SQUARE_METERS_PER_ACRE
                      for g in lonlat.geometry], index=frame.index)


def _tiles(footprint, tile_m: float, crs) -> gpd.GeoDataFrame:
    """The footprint cut into ``tile_m`` squares, so overlays hit small polygons, not one network."""
    xmin, ymin, xmax, ymax = footprint.bounds
    cells = [box(x, y, x + tile_m, y + tile_m)
             for x in np.arange(xmin, xmax, tile_m) for y in np.arange(ymin, ymax, tile_m)]
    grid = gpd.GeoDataFrame(geometry=cells, crs=crs)
    parts = grid.intersection(footprint)
    return gpd.GeoDataFrame(geometry=parts[~parts.is_empty], crs=crs)


def split_stands(stands: gpd.GeoDataFrame, footprint, tile_m: float = 2000.0) -> gpd.GeoDataFrame:
    """Single-part riparian (1) and upland (0) pieces of each stand, with geodesic ``Acres``.

    The footprint is tiled only to keep the overlay fast; each stand's riparian part is
    re-dissolved before exploding, so tile seams never create pieces.
    """
    keyed = stands.reset_index(drop=True).assign(_STAND=lambda f: np.arange(len(f)))
    tiles = _tiles(footprint, tile_m, stands.crs)
    hits = gpd.overlay(keyed[["_STAND", "geometry"]], tiles, how="intersection",
                       keep_geom_type=True)
    riparian = hits.dissolve(by="_STAND").geometry.reindex(keyed["_STAND"])
    has = riparian.notna().to_numpy()
    inside = keyed.loc[has].copy()
    inside["geometry"] = riparian[has].to_numpy()
    outside = keyed.copy()
    outside.loc[has, "geometry"] = keyed.geometry[has].difference(
        gpd.GeoSeries(riparian[has].to_numpy(), index=keyed.index[has], crs=stands.crs))
    inside["RIPARIAN"], outside["RIPARIAN"] = RIPARIAN, UPLAND
    pieces = gpd.GeoDataFrame(pd.concat([inside, outside], ignore_index=True),
                              geometry="geometry", crs=stands.crs).explode(ignore_index=True)
    polygonal = pieces.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    pieces = pieces[polygonal & ~pieces.geometry.is_empty & (pieces.geometry.area > 0)]
    pieces = pieces.drop(columns="_STAND").reset_index(drop=True)
    pieces["Acres"] = geodesic_acres(pieces)
    return pieces
