"""
Draft Florida management-unit delineation via naive boundary intersection.

This script creates candidate forest management units by intersecting parcels with
LANDFIRE EVT forest mask, then erasing Florida BMP stream buffers, NHD waterbodies,
and a small road-artifact buffer. Outputs are per-county GeoPackages + CSV summaries.

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
from pyogrio import list_layers
from rasterio.features import shapes
from rasterio.mask import mask as rio_mask
from shapely.geometry import Point, box, mapping
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

# Five-county pilot
PILOT_COUNTIES = ["003", "023", "047", "089", "125"]  # Baker, Columbia, Hamilton, Nassau, Suwannee, Union


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
    Clean geometries: buffer(0) to fix self-intersections, drop invalid/empty.
    Preserves all geometry types (Point, LineString, Polygon, etc.).

    For LineString and Point geometries, buffer(0) returns an empty polygon,
    so we skip the buffer operation for non-polygon types.
    """
    gdf = gdf.copy()

    # Apply buffer(0) only to Polygons/MultiPolygons to fix topology issues
    # LineStrings and Points should not be buffered
    polygon_mask = gdf.geom_type.isin(["Polygon", "MultiPolygon"])

    if polygon_mask.any():
        gdf.loc[polygon_mask, "geometry"] = gdf.loc[polygon_mask, "geometry"].buffer(0)

    # Drop invalid or empty geometries
    valid_mask = gdf["geometry"].is_valid & ~gdf["geometry"].is_empty
    n_dropped = (~valid_mask).sum()
    if n_dropped > 0:
        logger.warning(f"Dropped {n_dropped} invalid/empty geometries")

    return gdf[valid_mask].reset_index(drop=True)


def split_large_geometry(geometry, target_max_area_ha: float = TARGET_MAX_AREA_HA):
    """
    Split a large polygon into grid cells at or below target area.

    Returns a list of polygon parts. Uses a fishnet overlay approach:
    creates a regular grid over the bounding box, then intersects with the input geometry.
    """
    from shapely.geometry import Polygon

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
                # Handle multi-part results
                if part.geom_type == "MultiPolygon":
                    parts.extend(list(part.geoms))
                elif part.geom_type == "Polygon":
                    parts.append(part)

    return parts if parts else [geometry]


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
    5. Build and erase BMP stream buffers
    6. Erase waterbodies
    7. Erase small road buffer
    8. Classify by size
    9. Optionally split large polygons
    10. Save outputs

    Returns:
        Summary statistics dict, or None if dry_run=True
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
        "125": "UNION",
    }

    if county_fips in county_name_map:
        county_name = county_name_map[county_fips]
        parcels = parcels[parcels["CNTYNAME"] == county_name].copy()

    if len(parcels) == 0:
        logger.error(f"No parcels found for county {county_fips}")
        return None

    logger.info(f"Loaded {len(parcels)} parcels")

    # Get AOI bounds for clipping
    aoi_bounds = parcels.total_bounds
    aoi_geom = box(*aoi_bounds)

    # 2. Load and clip other inputs
    logger.info("Loading roads...")
    roads = gpd.read_file(roads_path, layer="SE_rds100k", mask=aoi_geom)
    roads = roads.to_crs(PROJECT_CRS)
    logger.info(f"Loaded {len(roads)} roads")

    logger.info("Loading streams...")
    streams = gpd.read_file(streams_path, layer="nhdflowline_fl", mask=aoi_geom)
    streams = streams.to_crs(PROJECT_CRS)
    logger.info(f"Loaded {len(streams)} streams")

    logger.info("Loading waterbodies...")
    waterbodies = gpd.read_file(waterbodies_path, layer="NHDWaterbody_DissolveBoundaries1", mask=aoi_geom)
    waterbodies = waterbodies.to_crs(PROJECT_CRS)
    logger.info(f"Loaded {len(waterbodies)} waterbodies")

    # 3. Create forest mask (simplified - in production use EVT VAT properly)
    logger.info("Creating forest mask from EVT...")
    # For now, create a simple forest mask from raster
    # This is a placeholder - the real implementation would use rasterio properly
    from shapely.geometry import shape

    with rasterio.open(evt_path) as src:
        # Clip to AOI
        aoi_geom_dict = [mapping(aoi_geom)]
        out_image, out_transform = rio_mask(src, aoi_geom_dict, crop=True, all_touched=False)

        # Tree-dominated classes: approximate as 1000-2999
        forest_mask = (out_image[0] >= 1000) & (out_image[0] < 3000)

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

    # 5. Build BMP stream buffers
    logger.info("Building BMP stream buffers...")
    streams["buffer_class"] = streams["fcode"].apply(classify_stream_fcode)

    # Map buffer class to width in meters
    buffer_widths = {
        "ephemeral_intermittent": feet_to_meters(fl_buffers["ephemeral_intermittent"]["width_ft"]),
        "perennial_small": feet_to_meters(fl_buffers["perennial_small"]["width_ft"]),
        "perennial_large": feet_to_meters(fl_buffers["perennial_large"]["width_ft"]),
    }

    stream_buffers = []
    for buffer_class, width_m in buffer_widths.items():
        class_streams = streams[streams["buffer_class"] == buffer_class]
        if len(class_streams) > 0:
            buffered = class_streams.buffer(width_m)
            stream_buffers.append(buffered.unary_union)

    if stream_buffers:
        all_stream_buffers = unary_union(stream_buffers)
        stream_buffer_gdf = gpd.GeoDataFrame(geometry=[all_stream_buffers], crs=PROJECT_CRS)
    else:
        stream_buffer_gdf = gpd.GeoDataFrame(geometry=[], crs=PROJECT_CRS)

    # 6. Erase waterbodies
    logger.info("Preparing waterbody erase layer...")
    if len(waterbodies) > 0:
        waterbody_union = waterbodies.unary_union
        waterbody_gdf = gpd.GeoDataFrame(geometry=[waterbody_union], crs=PROJECT_CRS)
    else:
        waterbody_gdf = gpd.GeoDataFrame(geometry=[], crs=PROJECT_CRS)

    # 7. Erase small road buffer
    logger.info("Preparing road buffer erase layer...")
    if len(roads) > 0:
        road_buffer = roads.buffer(SMALL_ROAD_BUFFER_M).unary_union
        road_buffer_gdf = gpd.GeoDataFrame(geometry=[road_buffer], crs=PROJECT_CRS)
    else:
        road_buffer_gdf = gpd.GeoDataFrame(geometry=[], crs=PROJECT_CRS)

    # Combine all erase layers
    logger.info("Erasing buffers and water...")
    erase_layers = []
    if len(stream_buffer_gdf) > 0:
        erase_layers.append(stream_buffer_gdf)
    if len(waterbody_gdf) > 0:
        erase_layers.append(waterbody_gdf)
    if len(road_buffer_gdf) > 0:
        erase_layers.append(road_buffer_gdf)

    if erase_layers:
        erase_union = pd.concat(erase_layers, ignore_index=True).unary_union
        erase_gdf = gpd.GeoDataFrame(geometry=[erase_union], crs=PROJECT_CRS)

        # Perform difference
        candidate_units = gpd.overlay(forested_parcels, erase_gdf, how="difference")
    else:
        candidate_units = forested_parcels.copy()

    logger.info(f"Candidate units after erase: {len(candidate_units)}")

    # 8. Calculate areas and classify
    candidate_units = clean_geometries(candidate_units)
    candidate_units["unit_area_ha"] = candidate_units.geometry.area / 10_000
    candidate_units["size_class"] = candidate_units["unit_area_ha"].apply(classify_unit_size)

    # 9. Optionally split large polygons
    if split_large:
        logger.info("Splitting large polygons...")
        large_mask = candidate_units["size_class"] == "large_gt_target"
        n_large = large_mask.sum()

        if n_large > 0:
            # Split large geometries
            split_rows = []
            for idx, row in candidate_units[large_mask].iterrows():
                parts = split_large_geometry(row.geometry, target_max_area_ha=TARGET_MAX_AREA_HA)
                for part in parts:
                    new_row = row.copy()
                    new_row["geometry"] = part
                    new_row["unit_area_ha"] = part.area / 10_000
                    new_row["size_class"] = classify_unit_size(new_row["unit_area_ha"])
                    split_rows.append(new_row)

            # Combine split and non-large units
            non_large = candidate_units[~large_mask]
            if split_rows:
                split_gdf = gpd.GeoDataFrame(split_rows, crs=PROJECT_CRS)
                candidate_units = pd.concat([non_large, split_gdf], ignore_index=True)
            else:
                candidate_units = non_large

            logger.info(f"Split {n_large} large units into {len(split_rows)} parts")

    # 10. Add metadata
    candidate_units["unit_id"] = [
        f"mu_{county_code}_{i:08d}" for i in range(len(candidate_units))
    ]
    candidate_units["county_fips"] = county_code
    candidate_units["county_name"] = county_name_map.get(county_fips, "Unknown")

    # Add source parcel area if ACRES field exists
    if "ACRES" in candidate_units.columns:
        candidate_units["source_parcel_area_ha"] = candidate_units["ACRES"] * 0.404686  # acres to ha

    # Reorder columns
    id_cols = ["unit_id", "county_fips", "county_name"]
    parcel_cols = [c for c in candidate_units.columns if c in ["CNTYNAME", "PARCELID", "NPARNO", "DORUC", "PARUSEDESC", "ACRES"]]
    area_cols = ["source_parcel_area_ha", "unit_area_ha", "size_class"] if "source_parcel_area_ha" in candidate_units.columns else ["unit_area_ha", "size_class"]
    other_cols = [c for c in candidate_units.columns if c not in id_cols + parcel_cols + area_cols + ["geometry"]]

    col_order = id_cols + parcel_cols + area_cols + other_cols + ["geometry"]
    col_order = [c for c in col_order if c in candidate_units.columns]
    candidate_units = candidate_units[col_order]

    # 11. Save outputs
    logger.info(f"Saving to {output_gpkg}")
    candidate_units.to_file(output_gpkg, driver="GPKG")

    # Save summary CSV
    summary = candidate_units.groupby("size_class").agg(
        polygon_count=("unit_id", "count"),
        total_area_ha=("unit_area_ha", "sum"),
        median_area_ha=("unit_area_ha", "median"),
    ).reset_index()

    summary_csv = county_output_dir / "summary.csv"
    summary.to_csv(summary_csv, index=False)
    logger.info(f"Summary:\n{summary}")

    # Save QA layers if requested
    if save_qa:
        qa_dir = county_output_dir / "qa"
        qa_dir.mkdir(exist_ok=True)

        if len(stream_buffer_gdf) > 0:
            stream_buffer_gdf.to_file(qa_dir / "stream_buffers.gpkg", driver="GPKG")
        if len(waterbody_gdf) > 0:
            waterbody_gdf.to_file(qa_dir / "waterbodies.gpkg", driver="GPKG")
        if len(road_buffer_gdf) > 0:
            road_buffer_gdf.to_file(qa_dir / "road_buffers.gpkg", driver="GPKG")
        if len(forest_mask_gdf) > 0:
            forest_mask_gdf.to_file(qa_dir / "forest_mask.gpkg", driver="GPKG")

    return summary.to_dict(orient="records")


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
                results.append({"county_fips": county_fips, "summary": result})
        except Exception as e:
            logger.error(f"Failed processing county {county_fips}: {e}", exc_info=True)

    logger.info("Processing complete")

    if results and not args.dry_run:
        logger.info(f"Processed {len(results)} counties successfully")


if __name__ == "__main__":
    main()
