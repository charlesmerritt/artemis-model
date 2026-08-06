"""
Draft Florida management-unit delineation via naive boundary intersection.

This script creates candidate forest units by intersecting parcels with a LANDFIRE EVT
forest mask, then partitioning the result into two unit classes:

- ``unit_class = "managed"``  — forest available for a harvest regime.
- ``unit_class = "riparian"`` — forest inside a Florida BMP stream buffer. These grow
  freely and are never harvested, but they stay in the landscape as unique polygons with
  their own ``unit_id`` and their own rows in the summaries.

Only NHD waterbodies and the small road-artifact buffer are erased outright: water is
non-forest and the road buffer exists solely to absorb road/parcel alignment error.
Those acres leave the modelled landscape, so they are reported as their own accounting
line rather than silently dropped. The two unit classes partition what remains:

    Σ managed + Σ riparian == (forest mask ∩ parcels) − (waterbodies ∪ road buffer)

Outputs are per-county GeoPackages plus ``summary.csv`` and ``area_accounting.csv``.

Usage:
    uv run python -m pipeline.s3_management.sketch_management_units --county-fips 125
    uv run python -m pipeline.s3_management.sketch_management_units --pilot-five-county
    uv run python -m pipeline.s3_management.sketch_management_units --all-florida
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import yaml
from rasterio.features import shapes
from rasterio.mask import mask as rio_mask
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Constants
PROJECT_CRS = "EPSG:5070"  # CONUS Albers Equal Area
FLORIDA_FIPS = "12"
FEET_TO_METERS_CONVERSION = 0.3048
MIN_UNIT_AREA_HA = 2.0
TARGET_MAX_AREA_HA = 40.0
SMALL_ROAD_BUFFER_M = 3.0  # Overcome alignment artifacts
SQ_M_PER_ACRE = 4046.8564224

# Most protective first. Where buffer classes overlap, the wider buffer wins, so the
# retained riparian rows stay disjoint and `buffer_class` is unambiguous.
RIPARIAN_BUFFER_PRIORITY = ["perennial_large", "perennial_small", "ephemeral_intermittent"]

# Unit classes emitted by this script.
UNIT_CLASS_MANAGED = "managed"
UNIT_CLASS_RIPARIAN = "riparian"

# Relative tolerance for the managed + riparian == eligible forest area check. Loose
# enough to absorb buffer(0) topology cleanup, tight enough to catch a dropped layer.
AREA_BALANCE_TOLERANCE = 1e-6

# Five-county pilot. These are the counties actually present in FL_5_Co_Parcels.gdb:
# Baker, Columbia, Hamilton, Suwannee, Union. (089/Nassau was listed here previously and
# has no parcels in the AOI, which dropped Suwannee from the pilot entirely.)
PILOT_COUNTIES = ["003", "023", "047", "121", "125"]  # Baker, Columbia, Hamilton, Suwannee, Union


def feet_to_meters(feet: float) -> float:
    """Convert feet to meters using standard conversion factor."""
    return feet * FEET_TO_METERS_CONVERSION


def classify_stream_fcode(fcode: Optional[int]) -> Optional[str]:
    """
    Classify NHD FCode into Florida BMP buffer class.

    Mapping per Florida Forest Service BMP Manual 2020 and NHD FCode definitions:
    - 46000, 46003, 46007: ephemeral/intermittent streams
    - 46006: perennial streams (defaulting to small for conservative buffer)

    Returns None for unrecognized or missing FCodes.
    """
    if fcode is None:
        return None

    ephemeral_intermittent = {46000, 46003, 46007}
    perennial = {46006}

    if fcode in ephemeral_intermittent:
        return "ephemeral_intermittent"
    elif fcode in perennial:
        return "perennial_small"
    else:
        return None


def classify_unit_size(area_ha: float, min_area_ha: float = MIN_UNIT_AREA_HA,
                       target_max_area_ha: float = TARGET_MAX_AREA_HA) -> str:
    """
    Classify management unit by area threshold.

    Returns:
        - "sliver_lt_min": < min_area_ha (default 2 ha)
        - "candidate": >= min_area_ha and <= target_max_area_ha (default 2-40 ha)
        - "large_gt_target": > target_max_area_ha (default >40 ha)
    """
    if area_ha < min_area_ha:
        return "sliver_lt_min"
    elif area_ha <= target_max_area_ha:
        return "candidate"
    else:
        return "large_gt_target"


def target_grid_cell_size_m(target_area_ha: float) -> float:
    """Calculate square grid cell side length (meters) for a target area in hectares."""
    return (target_area_ha * 10_000) ** 0.5


def clean_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Repair invalid polygon geometries and drop anything still invalid or empty.
    Preserves all geometry types (Point, LineString, Polygon, etc.).

    Repair uses `make_valid`, and only on rows that are actually invalid. The older
    unconditional `buffer(0)` silently destroyed area: `buffer(0)` is sensitive to ring
    orientation, which OGC validity does not constrain, so a *valid* MultiPolygon whose
    exterior rings happen to be wound clockwise can have a whole part reinterpreted as a
    hole and erased. That cost the Union County managed layer 0.22 ha in one polygon --
    exactly the kind of invisible forest loss the area-accounting check exists to catch.
    """
    gdf = gdf.copy()

    # Repair only what is broken, and only for Polygons/MultiPolygons.
    # LineStrings and Points are passed through untouched.
    polygon_mask = gdf.geom_type.isin(["Polygon", "MultiPolygon"])
    repair_mask = polygon_mask & ~gdf["geometry"].is_valid

    n_repaired = int(repair_mask.sum())
    if n_repaired > 0:
        logger.info(f"Repairing {n_repaired} invalid polygon geometries with make_valid")
        gdf.loc[repair_mask, "geometry"] = gdf.loc[repair_mask, "geometry"].make_valid()

    # Drop invalid or empty geometries
    valid_mask = gdf["geometry"].is_valid & ~gdf["geometry"].is_empty
    n_dropped = (~valid_mask).sum()
    if n_dropped > 0:
        logger.warning(f"Dropped {n_dropped} invalid/empty geometries")

    return gdf[valid_mask].reset_index(drop=True)


def polygon_parts(geometry) -> list:
    """
    Every Polygon inside `geometry`, recursing through multipart geometries.

    Clipping a multipart polygon can return a GeometryCollection: a cell that fully
    contains one part while merely *touching* a detached part returns that polygon plus a
    stray Point or LineString. Matching only on "Polygon"/"MultiPolygon" drops such a
    result whole -- polygon and all -- which silently deleted 23 ha of Columbia County
    forest. Lower-dimensional debris carries no area and is discarded here explicitly.
    """
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [p for g in geometry.geoms for p in polygon_parts(g)]
    return []


def split_large_geometry(geometry, target_max_area_ha: float = TARGET_MAX_AREA_HA):
    """
    Split a large polygon into grid cells at or below target area.

    Returns a list of polygon parts. Uses a fishnet overlay approach:
    creates a regular grid over the bounding box, then intersects with the input geometry.
    """

    target_area_m2 = target_max_area_ha * 10_000

    # If already below threshold, return as-is
    if geometry.area <= target_area_m2:
        return [geometry]

    # Calculate grid cell size
    cell_size = target_grid_cell_size_m(target_max_area_ha)

    # Get bounding box
    minx, miny, maxx, maxy = geometry.bounds

    # Create fishnet grid
    cols = int(np.ceil((maxx - minx) / cell_size))
    rows = int(np.ceil((maxy - miny) / cell_size))

    grid_cells = []
    for i in range(cols):
        for j in range(rows):
            x0 = minx + i * cell_size
            y0 = miny + j * cell_size
            x1 = x0 + cell_size
            y1 = y0 + cell_size
            cell = box(x0, y0, x1, y1)
            grid_cells.append(cell)

    # Intersect grid with geometry
    parts = []
    for cell in grid_cells:
        if geometry.intersects(cell):
            part = geometry.intersection(cell)
            if not part.is_empty and part.area > 0:
                parts.extend(polygon_parts(part))

    return parts if parts else [geometry]


def load_evt_tree_values(vat_path: Path, lifeform: str = "Tree") -> np.ndarray:
    """
    Return the LANDFIRE EVT raster values whose VAT lifeform is `lifeform`.

    The forest mask has to come from the VAT, not from a hardcoded value range: LF2022
    EVT codes tree classes across a non-contiguous 4402-9722 span interleaved with
    herbaceous, developed, and agricultural classes, so any range test both misses forest
    and admits non-forest.
    """
    vat = pd.read_csv(vat_path, usecols=["VALUE", "EVT_LF"])
    values = vat.loc[vat["EVT_LF"] == lifeform, "VALUE"].to_numpy()
    if len(values) == 0:
        raise ValueError(f"No EVT_LF == {lifeform!r} rows in {vat_path}")
    logger.info(f"EVT VAT: {len(values)} {lifeform!r} classes")
    return values


def build_riparian_buffer_layer(
    streams: gpd.GeoDataFrame,
    buffer_widths: dict,
    crs: str = PROJECT_CRS,
) -> gpd.GeoDataFrame:
    """
    Build Florida BMP stream buffers as a *retained* layer, one row per buffer class.

    Buffer classes overlap on the ground (a 35 ft ephemeral buffer can sit inside a 75 ft
    perennial one). Rows are made mutually disjoint by subtracting every more-protective
    class already assigned, so the returned geometries partition the buffered area and
    `buffer_class` is unambiguous anywhere inside it. Buffer polygons are never dissolved
    across classes — carrying the class as an attribute is what lets summaries be cut by
    class without merging the polygons themselves.
    """
    streams = streams.copy()
    streams["buffer_class"] = streams["fcode"].apply(classify_stream_fcode)

    rows = []
    claimed = None  # union of the more-protective classes already assigned
    for buffer_class in RIPARIAN_BUFFER_PRIORITY:
        width_m = buffer_widths.get(buffer_class)
        class_streams = streams[streams["buffer_class"] == buffer_class]
        if width_m is None or len(class_streams) == 0:
            continue

        geom = class_streams.buffer(width_m).union_all()
        if claimed is not None:
            geom = geom.difference(claimed)
        if geom.is_empty:
            continue

        claimed = geom if claimed is None else claimed.union(geom)
        rows.append({"buffer_class": buffer_class, "geometry": geom})

    if not rows:
        return gpd.GeoDataFrame({"buffer_class": pd.Series(dtype="object")}, geometry=[], crs=crs)

    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def build_exclusion_layer(
    waterbodies: gpd.GeoDataFrame,
    roads: gpd.GeoDataFrame,
    road_buffer_m: float = SMALL_ROAD_BUFFER_M,
    crs: str = PROJECT_CRS,
) -> gpd.GeoDataFrame:
    """
    Build the erase-only layer: NHD waterbodies plus the small road-artifact buffer,
    tagged by `exclusion_class`.

    Neither is a stand. Water is non-forest, and the road buffer exists only to absorb
    road/parcel alignment error. These acres leave the modelled landscape entirely, which
    is why they are reported as their own accounting lines instead of being folded into a
    unit class.

    Rows are left **undissolved**, and the two classes may overlap. Dissolving is both
    unnecessary -- `erase` differences per row through a spatial index -- and actively
    dangerous: a county-wide `union_all` over NHD swamp polygons segfaults GEOS on
    swamp-heavy counties such as Baker. The exclusion areas are therefore measured by
    telescoping differences in `process_county`, not read off these rows.
    """
    layers = []

    for exclusion_class, geometry in (
        ("waterbody", waterbodies.geometry if len(waterbodies) > 0 else None),
        ("road_buffer", roads.buffer(road_buffer_m) if len(roads) > 0 else None),
    ):
        if geometry is None:
            continue
        layer = gpd.GeoDataFrame(geometry=geometry.reset_index(drop=True), crs=crs)
        layer["exclusion_class"] = exclusion_class
        layers.append(layer)

    if not layers:
        return gpd.GeoDataFrame({"exclusion_class": pd.Series(dtype="object")}, geometry=[], crs=crs)

    return gpd.GeoDataFrame(pd.concat(layers, ignore_index=True), geometry="geometry", crs=crs)


def erase(gdf: gpd.GeoDataFrame, erase_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Difference `erase_gdf` out of `gdf`, passing `gdf` through unchanged if there is
    nothing to erase.

    `erase_gdf` is used as-is: geopandas differences each row of `gdf` against only the
    `erase_gdf` rows a spatial-index query says it touches, so the erase layer must not
    be pre-dissolved. Overlapping erase rows are handled correctly -- they are applied
    successively -- which is why the caller can pass raw NHD waterbodies.
    """
    if len(erase_gdf) == 0:
        return gdf.copy()
    return gpd.overlay(gdf, erase_gdf[["geometry"]], how="difference")


def area_accounting_table(
    forest_in_parcels_m2: float,
    waterbody_excluded_m2: float,
    road_excluded_m2: float,
    managed_m2: float,
    riparian_m2: float,
) -> pd.DataFrame:
    """
    Build the area-balance table for one county.

    The identity being checked is stated against the *post-exclusion* area:

        Σ managed + Σ riparian == (forest mask ∩ parcels) − (waterbodies ∪ road buffer)

    The raw forested AOI is the wrong right-hand side — roads run through forest, so the
    road-artifact buffer routinely overlaps forested pixels and an equality against the
    pre-exclusion area would fail by construction. The permanently excluded acres get
    their own lines so that drop stays visible instead of absorbing a real bug.
    """
    excluded_m2 = waterbody_excluded_m2 + road_excluded_m2
    eligible_m2 = forest_in_parcels_m2 - excluded_m2
    residual_m2 = eligible_m2 - (managed_m2 + riparian_m2)

    lines = [
        ("forest_in_parcels", forest_in_parcels_m2),
        ("excluded_waterbody", waterbody_excluded_m2),
        ("excluded_road_buffer", road_excluded_m2),
        ("excluded_total", excluded_m2),
        ("eligible_forest", eligible_m2),
        ("managed", managed_m2),
        ("riparian", riparian_m2),
        ("balance_residual", residual_m2),
    ]

    return pd.DataFrame(
        {
            "line": [name for name, _ in lines],
            "area_ha": [value / 10_000 for _, value in lines],
            "area_acres": [value / SQ_M_PER_ACRE for _, value in lines],
        }
    )


def load_config(config_path: Path = Path("config/bmp_rules.yaml")) -> dict:
    """Load BMP buffer rules from YAML config."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_florida_counties() -> gpd.GeoDataFrame:
    """Load Florida county boundaries from parcels or a county layer."""
    # For this implementation, we'll derive counties from parcels
    # In production, you might use a dedicated county boundary layer
    pass


def create_forest_mask_from_evt(evt_path: Path, aoi_bounds) -> gpd.GeoDataFrame:
    """
    Create forest mask from LANDFIRE EVT raster.

    Uses EVT_LF == "Tree" or EVT_ORDER == "Tree-dominated" to identify forest pixels.
    Vectorizes the result and returns as GeoDataFrame.
    """
    logger.info("Creating forest mask from LANDFIRE EVT")

    with rasterio.open(evt_path) as src:
        # Mask to AOI bounds
        aoi_geom = [mapping(box(*aoi_bounds))]
        out_image, out_transform = rio_mask(src, aoi_geom, crop=True, all_touched=True)

        # For LANDFIRE EVT, forest classes are typically values < 3000 and >= 3000
        # Tree-dominated values: 1000-2999 typically represent forest/woodland
        # This is a simplified approach; production code should use the VAT
        forest_mask = (out_image[0] >= 1000) & (out_image[0] < 3000)

        # Vectorize
        forest_shapes = []
        for geom, value in shapes(forest_mask.astype(np.uint8), transform=out_transform):
            if value == 1:  # forest pixel
                forest_shapes.append(geom)

        if not forest_shapes:
            logger.warning("No forest pixels found in AOI")
            return gpd.GeoDataFrame(geometry=[], crs=src.crs)

        # Convert to GeoDataFrame and dissolve
        forest_gdf = gpd.GeoDataFrame(
            geometry=[shape(g) for g in forest_shapes],
            crs=src.crs
        )

        # Dissolve to single multipolygon
        forest_dissolved = forest_gdf.dissolve()

        return forest_dissolved


def process_county(
    county_fips: str,
    output_dir: Path,
    data_root: Path = Path("data/raw"),
    split_large: bool = True,
    save_qa: bool = False,
    overwrite: bool = False,
    dry_run: bool = False,
) -> Optional[dict]:
    """
    Process a single Florida county to generate candidate management units.

    Steps:
    1. Load and clip parcels to county
    2. Load and clip roads, streams, waterbodies, LANDFIRE EVT
    3. Create forest mask from EVT
    4. Intersect parcels with forest mask
    5. Erase the permanent exclusions (waterbodies, road buffer) -> eligible forest
    6. Build BMP stream buffers and split eligible forest into managed + riparian units
    7. Classify by size
    8. Optionally split large managed polygons
    9. Check the area balance and save outputs

    Returns:
        {"summary": summary rows, "balance_ok": bool, "balance_residual_ha": float},
        or None if dry_run=True or the county could not be processed.
    """
    logger.info(f"Processing county FIPS {FLORIDA_FIPS}{county_fips}")

    if dry_run:
        logger.info("DRY RUN - would process county but not saving outputs")
        return None

    # Setup paths
    county_code = f"{FLORIDA_FIPS}{county_fips}"
    county_output_dir = output_dir / county_code
    county_output_dir.mkdir(parents=True, exist_ok=True)

    output_gpkg = county_output_dir / "candidate_management_units.gpkg"
    accounting_csv = county_output_dir / "area_accounting.csv"
    if output_gpkg.exists() and not overwrite:
        logger.warning(f"Output exists and overwrite=False: {output_gpkg}")
        return None

    # Load config
    config = load_config(Path("config/bmp_rules.yaml"))
    fl_buffers = config["states"][FLORIDA_FIPS]["buffers"]

    # Data paths
    parcels_path = data_root / "FL_5_Co_Parcels.gdb"
    roads_path = data_root / "SE_rds100k" / "SE_rds100k.gdb"
    streams_path = data_root / "US SE Streams - FINAL" / "US SE Streams - FINAL" / "Streams By State" / "nhdplus_epasnapshot2022_fl.gdb"
    waterbodies_path = data_root / "US SE Waterbodies Final" / "US SE Streams 10.20.2023" / "US SE Streams" / "US SE Streams.gdb"
    evt_path = data_root / "LF2022_EVT_CONUS" / "LF2022_EVT_CONUS" / "Tif" / "LF2022_EVT_CONUS.tif"
    evt_vat_path = data_root / "LF2022_EVT_CONUS" / "LF2022_EVT_CONUS" / "CSV_Data" / "LF2022_EVT.csv"

    # 1. Load parcels for county
    logger.info("Loading parcels...")
    parcels = gpd.read_file(parcels_path, layer="FL_5_Co_Parcels")
    parcels = parcels.to_crs(PROJECT_CRS)

    # Filter to county (assuming CNTYNAME or similar field; adjust as needed)
    # For Union County, the BRIEF shows CNTYNAME = "UNION"
    county_name_map = {
        "003": "BAKER",
        "023": "COLUMBIA",
        "047": "HAMILTON",
        "089": "NASSAU",
        "091": "OKALOOSA",  # Not in pilot but included for reference
        "121": "SUWANNEE",
        "125": "UNION",
    }

    if county_fips not in county_name_map:
        # Without a name there is no filter, so every county's parcels would fall through
        # and be attributed to this one. Fail loudly instead.
        logger.error(f"No county name mapping for FIPS {county_fips}; refusing to run unfiltered")
        return None

    county_name = county_name_map[county_fips]
    parcels = parcels[parcels["CNTYNAME"] == county_name].copy()

    if len(parcels) == 0:
        logger.error(f"No parcels found for county {county_fips}")
        return None

    logger.info(f"Loaded {len(parcels)} parcels")

    # Get AOI bounds for clipping. The vector reads need a *CRS-aware* mask: pyogrio
    # interprets a bare shapely geometry in the target layer's own CRS, and the source
    # layers are in EPSG:4326/4269, so a raw EPSG:5070 box silently matches nothing.
    aoi_bounds = parcels.total_bounds
    aoi_geom = box(*aoi_bounds)
    aoi_mask = gpd.GeoSeries([aoi_geom], crs=PROJECT_CRS)

    # 2. Load and clip other inputs
    logger.info("Loading roads...")
    roads = gpd.read_file(roads_path, layer="SE_rds100k", mask=aoi_mask)
    roads = roads.to_crs(PROJECT_CRS)
    logger.info(f"Loaded {len(roads)} roads")

    logger.info("Loading streams...")
    streams = gpd.read_file(streams_path, layer="nhdflowline_fl", mask=aoi_mask)
    streams = streams.to_crs(PROJECT_CRS)
    logger.info(f"Loaded {len(streams)} streams")

    logger.info("Loading waterbodies...")
    waterbodies = gpd.read_file(waterbodies_path, layer="NHDWaterbody_DissolveBoundaries1", mask=aoi_mask)
    waterbodies = waterbodies.to_crs(PROJECT_CRS)
    logger.info(f"Loaded {len(waterbodies)} waterbodies")

    # 3. Create forest mask from the EVT VAT
    logger.info("Creating forest mask from EVT...")
    from shapely.geometry import shape

    tree_values = load_evt_tree_values(evt_vat_path)

    with rasterio.open(evt_path) as src:
        # Clip to AOI. The EVT raster is already EPSG:5070, so the bare box is correct here.
        aoi_geom_dict = [mapping(aoi_geom)]
        out_image, out_transform = rio_mask(src, aoi_geom_dict, crop=True, all_touched=False)

        forest_mask = np.isin(out_image[0], tree_values)

        # Vectorize
        forest_shapes_list = []
        for geom, value in shapes(forest_mask.astype(np.uint8), transform=out_transform):
            if value == 1:
                forest_shapes_list.append(shape(geom))

        if forest_shapes_list:
            forest_union = unary_union(forest_shapes_list)
            forest_mask_gdf = gpd.GeoDataFrame(
                geometry=[forest_union],
                crs=src.crs
            ).to_crs(PROJECT_CRS)
        else:
            logger.warning("No forest pixels found")
            forest_mask_gdf = gpd.GeoDataFrame(geometry=[], crs=PROJECT_CRS)

    # 4. Intersect parcels with forest mask
    logger.info("Intersecting parcels with forest...")
    if len(forest_mask_gdf) > 0:
        forested_parcels = gpd.overlay(parcels, forest_mask_gdf, how="intersection")
    else:
        logger.warning("Empty forest mask - no intersection possible")
        return None

    logger.info(f"Forested parcel fragments: {len(forested_parcels)}")
    forest_in_parcels_m2 = float(forested_parcels.geometry.area.sum())

    # 5. Erase the permanent exclusions: waterbodies and the small road-artifact buffer.
    #    These are not stands and never become units, so their acres leave the landscape
    #    here and are reported on their own accounting lines below.
    #
    #    Erased one class at a time so the excluded areas *telescope*: each class is
    #    credited with exactly the area it was the first to remove, the two lines sum to
    #    the total drop by construction, and no county-wide union is ever built.
    logger.info("Building exclusion layer (waterbodies + road buffer)...")
    exclusion_gdf = build_exclusion_layer(waterbodies, roads, road_buffer_m=SMALL_ROAD_BUFFER_M)

    excluded_m2 = {"waterbody": 0.0, "road_buffer": 0.0}
    eligible = forested_parcels
    for exclusion_class in ("waterbody", "road_buffer"):
        layer = exclusion_gdf[exclusion_gdf["exclusion_class"] == exclusion_class]
        before_m2 = float(eligible.geometry.area.sum())
        eligible = erase(eligible, layer)
        excluded_m2[exclusion_class] = before_m2 - float(eligible.geometry.area.sum())
        logger.info(f"  erased {exclusion_class}: {excluded_m2[exclusion_class] / 10_000:,.1f} ha")

    eligible = clean_geometries(eligible)
    logger.info(f"Eligible forest fragments after exclusions: {len(eligible)}")

    # 6. Build BMP stream buffers and split the eligible forest into managed + riparian.
    #    Buffers are retained, not erased: they grow freely, are never harvested, and are
    #    reported as unique polygons with their own IDs.
    logger.info("Building BMP stream buffers...")
    buffer_widths = {
        "ephemeral_intermittent": feet_to_meters(fl_buffers["ephemeral_intermittent"]["width_ft"]),
        "perennial_small": feet_to_meters(fl_buffers["perennial_small"]["width_ft"]),
        "perennial_large": feet_to_meters(fl_buffers["perennial_large"]["width_ft"]),
    }
    riparian_buffer_gdf = build_riparian_buffer_layer(streams, buffer_widths)
    logger.info(f"Riparian buffer classes present: {list(riparian_buffer_gdf['buffer_class'])}")

    logger.info("Partitioning eligible forest into managed and riparian units...")
    if len(riparian_buffer_gdf) > 0:
        riparian_units = gpd.overlay(eligible, riparian_buffer_gdf, how="intersection")
        managed_units = erase(eligible, riparian_buffer_gdf)
    else:
        riparian_units = eligible.iloc[0:0].copy()
        riparian_units["buffer_class"] = pd.Series(dtype="object")
        managed_units = eligible.copy()

    managed_units["buffer_class"] = None
    managed_units["unit_class"] = UNIT_CLASS_MANAGED
    riparian_units["unit_class"] = UNIT_CLASS_RIPARIAN

    managed_units = clean_geometries(managed_units)
    riparian_units = clean_geometries(riparian_units)
    logger.info(f"Managed fragments: {len(managed_units)}, riparian fragments: {len(riparian_units)}")

    # 7. Calculate areas and classify
    for units in (managed_units, riparian_units):
        units["unit_area_ha"] = units.geometry.area / 10_000
        units["size_class"] = units["unit_area_ha"].apply(classify_unit_size)

    # 8. Optionally split large polygons. Managed units only: the 40 ha target bounds an
    #    operational harvest unit, and riparian buffers are never entered, so splitting
    #    them would invent geometry with no management meaning.
    if split_large:
        logger.info("Splitting large managed polygons (riparian units are left intact)...")
        large_mask = managed_units["size_class"] == "large_gt_target"
        n_large = large_mask.sum()

        if n_large > 0:
            # Split large geometries
            split_rows = []
            for idx, row in managed_units[large_mask].iterrows():
                parts = split_large_geometry(row.geometry, target_max_area_ha=TARGET_MAX_AREA_HA)
                for part in parts:
                    new_row = row.copy()
                    new_row["geometry"] = part
                    new_row["unit_area_ha"] = part.area / 10_000
                    new_row["size_class"] = classify_unit_size(new_row["unit_area_ha"])
                    split_rows.append(new_row)

            # Combine split and non-large units
            non_large = managed_units[~large_mask]
            if split_rows:
                split_gdf = gpd.GeoDataFrame(split_rows, crs=PROJECT_CRS)
                managed_units = pd.concat([non_large, split_gdf], ignore_index=True)
            else:
                managed_units = non_large

            logger.info(f"Split {n_large} large units into {len(split_rows)} parts")

    # 9. Add metadata. Managed and riparian units get distinct ID prefixes so a buffer
    #    polygon stays addressable in every downstream summary.
    managed_units["unit_id"] = [f"mu_{county_code}_{i:08d}" for i in range(len(managed_units))]
    riparian_units["unit_id"] = [f"rb_{county_code}_{i:08d}" for i in range(len(riparian_units))]

    candidate_units = pd.concat([managed_units, riparian_units], ignore_index=True)
    candidate_units = gpd.GeoDataFrame(candidate_units, geometry="geometry", crs=PROJECT_CRS)
    candidate_units["county_fips"] = county_code
    candidate_units["county_name"] = county_name_map.get(county_fips, "Unknown")

    # Add source parcel area if ACRES field exists
    if "ACRES" in candidate_units.columns:
        candidate_units["source_parcel_area_ha"] = candidate_units["ACRES"] * 0.404686  # acres to ha

    # Reorder columns
    id_cols = ["unit_id", "unit_class", "buffer_class", "county_fips", "county_name"]
    parcel_cols = [c for c in candidate_units.columns if c in ["CNTYNAME", "PARCELID", "NPARNO", "DORUC", "PARUSEDESC", "ACRES"]]
    area_cols = ["source_parcel_area_ha", "unit_area_ha", "size_class"] if "source_parcel_area_ha" in candidate_units.columns else ["unit_area_ha", "size_class"]
    other_cols = [c for c in candidate_units.columns if c not in id_cols + parcel_cols + area_cols + ["geometry"]]

    col_order = id_cols + parcel_cols + area_cols + other_cols + ["geometry"]
    col_order = [c for c in col_order if c in candidate_units.columns]
    candidate_units = candidate_units[col_order]

    # 10. Area accounting. Managed + riparian must partition the eligible forest; the
    #     permanently excluded acres get their own lines so the drop stays visible.
    is_riparian = candidate_units["unit_class"] == UNIT_CLASS_RIPARIAN
    accounting = area_accounting_table(
        forest_in_parcels_m2=forest_in_parcels_m2,
        waterbody_excluded_m2=excluded_m2["waterbody"],
        road_excluded_m2=excluded_m2["road_buffer"],
        managed_m2=float(candidate_units.loc[~is_riparian, "unit_area_ha"].sum() * 10_000),
        riparian_m2=float(candidate_units.loc[is_riparian, "unit_area_ha"].sum() * 10_000),
    )

    eligible_ha = float(accounting.loc[accounting["line"] == "eligible_forest", "area_ha"].iloc[0])
    residual_ha = float(accounting.loc[accounting["line"] == "balance_residual", "area_ha"].iloc[0])
    balance_ok = eligible_ha <= 0 or abs(residual_ha) / eligible_ha <= AREA_BALANCE_TOLERANCE
    if balance_ok:
        logger.info(f"Area balance check passed (residual {residual_ha:.6f} ha of {eligible_ha:,.1f} ha)")
    else:
        logger.error(
            "Area balance check FAILED: managed + riparian is off eligible forest by "
            f"{residual_ha:.4f} ha ({abs(residual_ha) / eligible_ha:.2e} relative), "
            f"tolerance {AREA_BALANCE_TOLERANCE:.0e}. Forest area has been lost between the "
            "exclusion step and the written units -- treat this county's outputs as wrong."
        )

    # 11. Save outputs
    logger.info(f"Saving to {output_gpkg}")
    candidate_units.to_file(output_gpkg, driver="GPKG")

    accounting.to_csv(accounting_csv, index=False)
    logger.info(f"Area accounting:\n{accounting.to_string(index=False)}")

    # Save summary CSV. Riparian units keep their own rows, cut by buffer class.
    summary = candidate_units.groupby(["unit_class", "buffer_class", "size_class"], dropna=False).agg(
        polygon_count=("unit_id", "count"),
        total_area_ha=("unit_area_ha", "sum"),
        total_area_acres=("unit_area_ha", lambda s: s.sum() * 10_000 / SQ_M_PER_ACRE),
        median_area_ha=("unit_area_ha", "median"),
    ).reset_index()

    summary_csv = county_output_dir / "summary.csv"
    summary.to_csv(summary_csv, index=False)
    logger.info(f"Summary:\n{summary.to_string(index=False)}")

    # Save QA layers if requested
    if save_qa:
        qa_dir = county_output_dir / "qa"
        qa_dir.mkdir(exist_ok=True)

        if len(riparian_buffer_gdf) > 0:
            riparian_buffer_gdf.to_file(qa_dir / "riparian_buffers.gpkg", driver="GPKG")
        if len(exclusion_gdf) > 0:
            exclusion_gdf.to_file(qa_dir / "exclusions.gpkg", driver="GPKG")
        if len(forest_mask_gdf) > 0:
            forest_mask_gdf.to_file(qa_dir / "forest_mask.gpkg", driver="GPKG")

    return {
        "summary": summary.to_dict(orient="records"),
        "balance_ok": balance_ok,
        "balance_residual_ha": residual_ha,
    }


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate draft Florida management units via naive boundary intersection"
    )

    parser.add_argument("--county-fips", type=str, help="Three-digit county FIPS code (e.g., 125 for Union)")
    parser.add_argument("--pilot-five-county", action="store_true", help="Process all five pilot counties")
    parser.add_argument("--all-florida", action="store_true", help="Process all Florida counties")
    parser.add_argument("--output-dir", type=Path, default=Path("data/interim/management_units"),
                       help="Output directory (default: data/interim/management_units)")
    parser.add_argument("--no-split-large", action="store_true", help="Skip splitting large polygons")
    parser.add_argument("--save-qa", action="store_true", help="Save QA layers (buffers, masks)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without saving")

    args = parser.parse_args()

    # Determine which counties to process
    counties_to_process = []

    if args.county_fips:
        counties_to_process = [args.county_fips]
    elif args.pilot_five_county:
        counties_to_process = PILOT_COUNTIES
    elif args.all_florida:
        # For production, you'd enumerate all FL counties
        logger.error("--all-florida not yet implemented; use --pilot-five-county or --county-fips")
        sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

    # Process each county
    results = []
    failed = []
    for county_fips in counties_to_process:
        try:
            result = process_county(
                county_fips=county_fips,
                output_dir=args.output_dir,
                split_large=not args.no_split_large,
                save_qa=args.save_qa,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
            if result:
                results.append({"county_fips": county_fips, "summary": result["summary"]})
                if not result["balance_ok"]:
                    failed.append((county_fips, result["balance_residual_ha"]))
        except Exception as e:
            logger.error(f"Failed processing county {county_fips}: {e}", exc_info=True)
            failed.append((county_fips, float("nan")))

    logger.info("Processing complete")

    if results and not args.dry_run:
        logger.info(f"Processed {len(results)} counties successfully")

    # A county whose area balance does not close has lost forest somewhere. Exit non-zero
    # so a batch run cannot report success while shipping wrong acreage.
    if failed:
        for county_fips, residual_ha in failed:
            logger.error(f"County {FLORIDA_FIPS}{county_fips}: area balance off by {residual_ha:.4f} ha")
        sys.exit(1)


if __name__ == "__main__":
    main()
