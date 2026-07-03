"""
Raster segmentation approach for management-unit delineation.

Uses scikit-image segmentation algorithms (Felzenszwalb and SLIC) on a stacked
raster (LANDFIRE EVT, TreeMap plot-ID, ownership) to create operationally-sized
forest management units. Compares against the naive boundary-intersection approach.

Usage:
    uv run python research/mgmt_units/segmentation_delineation.py --county-fips 125
"""

import argparse
import logging
from pathlib import Path
from typing import Tuple

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import seaborn as sns
import yaml
from rasterio.features import shapes
from rasterio.mask import mask as rio_mask
from rasterio.windows import from_bounds
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union
from skimage.segmentation import felzenszwalb, slic
from skimage.util import img_as_float

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Constants
PROJECT_CRS = "EPSG:5070"
FLORIDA_FIPS = "12"


def load_raster_stack(
    evt_path: Path,
    treemap_path: Path,
    ownership_path: Path,
    aoi_bounds: Tuple[float, float, float, float],
    target_resolution: int = 30,
) -> Tuple[np.ndarray, rasterio.transform.Affine, rasterio.crs.CRS]:
    """
    Load and stack raster inputs for segmentation.

    Returns:
        stacked_array: (n_bands, height, width) array
        transform: affine transform
        crs: coordinate reference system
    """
    logger.info("Loading raster stack for segmentation")

    aoi_geom = [mapping(box(*aoi_bounds))]

    # Load LANDFIRE EVT (forest type proxy)
    with rasterio.open(evt_path) as src:
        evt_data, evt_transform = rio_mask(src, aoi_geom, crop=True, all_touched=False)
        evt_crs = src.crs
        evt_band = evt_data[0]

    # Load TreeMap (plot ID - proxy for stand structure)
    with rasterio.open(treemap_path) as src:
        treemap_data, _ = rio_mask(src, aoi_geom, crop=True, all_touched=False)
        treemap_band = treemap_data[0]

    # Load ownership
    with rasterio.open(ownership_path) as src:
        ownership_data, _ = rio_mask(src, aoi_geom, crop=True, all_touched=False)
        ownership_band = ownership_data[0]

    # Stack bands (normalize to 0-1 for segmentation)
    # EVT: normalize by max value
    evt_normalized = np.where(evt_band > 0, evt_band / np.nanmax(evt_band), 0)

    # TreeMap: normalize plot IDs (many will be 0/nodata)
    treemap_normalized = np.where(treemap_band > 0, treemap_band / np.nanmax(treemap_band), 0)

    # Ownership: categorical, normalize to 0-1 range
    ownership_normalized = ownership_band / 8.0  # max ownership class = 8

    # Stack
    stacked = np.stack([evt_normalized, treemap_normalized, ownership_normalized], axis=0)

    logger.info(f"Stacked raster shape: {stacked.shape}")

    return stacked, evt_transform, evt_crs


def create_forest_mask(evt_array: np.ndarray) -> np.ndarray:
    """
    Create binary forest mask from LANDFIRE EVT.

    Tree-dominated classes: 1000-2999 (approximate).
    """
    return (evt_array >= 1000) & (evt_array < 3000)


def felzenszwalb_segmentation(
    raster_stack: np.ndarray,
    forest_mask: np.ndarray,
    scale: int = 100,
    sigma: float = 0.5,
    min_size: int = 50,
) -> np.ndarray:
    """
    Perform Felzenszwalb segmentation on raster stack.

    Args:
        raster_stack: (n_bands, height, width) normalized raster
        forest_mask: (height, width) boolean mask
        scale: Free parameter. Higher means larger clusters.
        sigma: Width of Gaussian smoothing kernel.
        min_size: Minimum component size (pixels).

    Returns:
        segment_ids: (height, width) array of segment labels
    """
    logger.info(f"Running Felzenszwalb segmentation (scale={scale}, sigma={sigma}, min_size={min_size})")

    # Transpose to (height, width, n_bands) for skimage
    image = np.transpose(raster_stack, (1, 2, 0))

    # Run segmentation
    segments = felzenszwalb(
        image,
        scale=scale,
        sigma=sigma,
        min_size=min_size,
    )

    # Mask out non-forest pixels
    segments_masked = np.where(forest_mask, segments, 0)

    n_segments = len(np.unique(segments_masked)) - 1  # subtract nodata
    logger.info(f"Generated {n_segments} segments")

    return segments_masked


def slic_segmentation(
    raster_stack: np.ndarray,
    forest_mask: np.ndarray,
    n_segments: int = 1000,
    compactness: float = 10.0,
    sigma: float = 1.0,
) -> np.ndarray:
    """
    Perform SLIC (Simple Linear Iterative Clustering) segmentation.

    Args:
        raster_stack: (n_bands, height, width) normalized raster
        forest_mask: (height, width) boolean mask
        n_segments: Approximate number of segments to generate.
        compactness: Balances color vs space proximity. Higher = more compact.
        sigma: Width of Gaussian smoothing kernel.

    Returns:
        segment_ids: (height, width) array of segment labels
    """
    logger.info(f"Running SLIC segmentation (n_segments={n_segments}, compactness={compactness})")

    # Transpose to (height, width, n_bands)
    image = np.transpose(raster_stack, (1, 2, 0))

    # Run segmentation
    segments = slic(
        image,
        n_segments=n_segments,
        compactness=compactness,
        sigma=sigma,
        start_label=1,
    )

    # Mask out non-forest
    segments_masked = np.where(forest_mask, segments, 0)

    n_segments_actual = len(np.unique(segments_masked)) - 1
    logger.info(f"Generated {n_segments_actual} segments")

    return segments_masked


def vectorize_segments(
    segment_array: np.ndarray,
    transform: rasterio.transform.Affine,
    crs: rasterio.crs.CRS,
) -> gpd.GeoDataFrame:
    """
    Vectorize raster segments to polygons.

    Returns:
        GeoDataFrame with segment_id and geometry.
    """
    logger.info("Vectorizing segments")

    results = []
    for geom, value in shapes(segment_array.astype(np.int32), transform=transform):
        if value != 0:  # skip nodata
            results.append({"segment_id": int(value), "geometry": shape(geom)})

    gdf = gpd.GeoDataFrame(results, crs=crs)
    logger.info(f"Vectorized {len(gdf)} segments")

    return gdf


def apply_bmp_erase(
    segments_gdf: gpd.GeoDataFrame,
    streams_gdf: gpd.GeoDataFrame,
    waterbodies_gdf: gpd.GeoDataFrame,
    roads_gdf: gpd.GeoDataFrame,
    bmp_config: dict,
    small_road_buffer_m: float = 3.0,
) -> gpd.GeoDataFrame:
    """
    Erase BMP stream buffers, waterbodies, and road buffers from segments.

    This ensures fair comparison with the naive approach.
    """
    logger.info("Applying BMP/water/road erase to segments")

    from pipeline.s3_management.sketch_management_units import (
        classify_stream_fcode,
        feet_to_meters,
    )

    # Build stream buffers
    fl_buffers = bmp_config["states"][FLORIDA_FIPS]["buffers"]
    streams_gdf["buffer_class"] = streams_gdf["fcode"].apply(classify_stream_fcode)

    buffer_widths = {
        "ephemeral_intermittent": feet_to_meters(fl_buffers["ephemeral_intermittent"]["width_ft"]),
        "perennial_small": feet_to_meters(fl_buffers["perennial_small"]["width_ft"]),
        "perennial_large": feet_to_meters(fl_buffers["perennial_large"]["width_ft"]),
    }

    stream_buffers = []
    for buffer_class, width_m in buffer_widths.items():
        class_streams = streams_gdf[streams_gdf["buffer_class"] == buffer_class]
        if len(class_streams) > 0:
            buffered = class_streams.buffer(width_m)
            stream_buffers.append(buffered.unary_union)

    # Combine erase layers
    erase_parts = []
    if stream_buffers:
        erase_parts.append(unary_union(stream_buffers))
    if len(waterbodies_gdf) > 0:
        erase_parts.append(waterbodies_gdf.unary_union)
    if len(roads_gdf) > 0:
        erase_parts.append(roads_gdf.buffer(small_road_buffer_m).unary_union)

    if erase_parts:
        erase_union = unary_union(erase_parts)
        erase_gdf = gpd.GeoDataFrame(geometry=[erase_union], crs=segments_gdf.crs)
        result = gpd.overlay(segments_gdf, erase_gdf, how="difference")
    else:
        result = segments_gdf.copy()

    logger.info(f"Segments after erase: {len(result)}")
    return result


def compute_metrics(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Compute area, size class, and shape compactness (Polsby-Popper) for each unit.

    Polsby-Popper = 4 * pi * area / perimeter^2
    Ranges from 0 (very elongated) to 1 (perfect circle).
    """
    from pipeline.s3_management.sketch_management_units import classify_unit_size

    gdf = gdf.copy()

    gdf["unit_area_ha"] = gdf.geometry.area / 10_000
    gdf["size_class"] = gdf["unit_area_ha"].apply(classify_unit_size)

    # Polsby-Popper compactness
    perimeter = gdf.geometry.length
    area = gdf.geometry.area
    gdf["compactness"] = (4 * np.pi * area) / (perimeter ** 2 + 1e-10)  # avoid division by zero

    return gdf


def process_segmentation_strategy(
    county_fips: str,
    strategy: str,
    output_dir: Path,
    data_root: Path = Path("data/raw"),
    **seg_params,
) -> gpd.GeoDataFrame:
    """
    Process a single segmentation strategy for a county.

    Args:
        county_fips: Three-digit county FIPS code
        strategy: "felzenszwalb" or "slic"
        output_dir: Where to save outputs
        seg_params: Strategy-specific parameters

    Returns:
        GeoDataFrame of final management units with metrics
    """
    logger.info(f"Processing {strategy} strategy for county {county_fips}")

    # Setup paths
    county_code = f"{FLORIDA_FIPS}{county_fips}"
    strategy_output_dir = output_dir / f"{county_code}_{strategy}"
    strategy_output_dir.mkdir(parents=True, exist_ok=True)

    # Data paths
    parcels_path = data_root / "FL_5_Co_Parcels.gdb"
    roads_path = data_root / "SE_rds100k" / "SE_rds100k.gdb"
    streams_path = (
        data_root / "US SE Streams - FINAL" / "US SE Streams - FINAL" /
        "Streams By State" / "nhdplus_epasnapshot2022_fl.gdb"
    )
    waterbodies_path = (
        data_root / "US SE Waterbodies Final" / "US SE Streams 10.20.2023" /
        "US SE Streams" / "US SE Streams.gdb"
    )
    evt_path = data_root / "LF2022_EVT_CONUS" / "LF2022_EVT_CONUS" / "Tif" / "LF2022_EVT_CONUS.tif"
    treemap_path = data_root / "TreeMap-2022" / "Data" / "TreeMap2022_CONUS.tif"
    ownership_path = data_root / "RDS-2025-0045" / "Data" / "US_forest_ownership.tif"

    # County name mapping
    county_name_map = {
        "003": "BAKER",
        "023": "COLUMBIA",
        "047": "HAMILTON",
        "089": "NASSAU",
        "125": "UNION",
    }

    # Load parcels to get AOI
    logger.info("Loading parcels for AOI...")
    parcels = gpd.read_file(parcels_path, layer="FL_5_Co_Parcels")
    parcels = parcels.to_crs(PROJECT_CRS)

    county_name = county_name_map.get(county_fips, "Unknown")
    parcels = parcels[parcels["CNTYNAME"] == county_name].copy()

    if len(parcels) == 0:
        raise ValueError(f"No parcels found for county {county_fips}")

    aoi_bounds = parcels.total_bounds
    aoi_geom = box(*aoi_bounds)

    # Load vector layers for erase
    logger.info("Loading vector layers...")
    roads = gpd.read_file(roads_path, layer="SE_rds100k", mask=aoi_geom).to_crs(PROJECT_CRS)
    streams = gpd.read_file(streams_path, layer="nhdflowline_fl", mask=aoi_geom).to_crs(PROJECT_CRS)
    waterbodies = gpd.read_file(
        waterbodies_path, layer="NHDWaterbody_DissolveBoundaries1", mask=aoi_geom
    ).to_crs(PROJECT_CRS)

    # Load BMP config
    bmp_config = yaml.safe_load(open("config/bmp_rules.yaml"))

    # Load raster stack
    logger.info("Loading raster stack...")
    with rasterio.open(evt_path) as src:
        aoi_geom_dict = [mapping(aoi_geom)]
        evt_data, evt_transform = rio_mask(src, aoi_geom_dict, crop=True, all_touched=False)
        evt_crs = src.crs
        evt_band = evt_data[0]

    # Create forest mask
    forest_mask = create_forest_mask(evt_band)
    n_forest_pixels = forest_mask.sum()
    logger.info(f"Forest pixels: {n_forest_pixels}")

    if n_forest_pixels == 0:
        raise ValueError("No forest pixels in AOI")

    # For now, use EVT as the primary band; in production we'd add TreeMap and ownership
    # but those require more complex preprocessing
    # We'll use a 3-band stack with EVT, EVT-derived texture, and a constant band
    evt_normalized = np.where(evt_band > 0, evt_band / np.nanmax(evt_band), 0)

    # Create a simple multi-band image for segmentation
    # Band 1: EVT normalized
    # Band 2: Binary forest mask (provides sharp boundaries)
    # Band 3: Constant (could be ownership or other)
    raster_stack = np.stack([
        evt_normalized,
        forest_mask.astype(float),
        np.ones_like(evt_normalized) * 0.5,
    ], axis=0)

    # Run segmentation
    if strategy == "felzenszwalb":
        segments = felzenszwalb_segmentation(raster_stack, forest_mask, **seg_params)
    elif strategy == "slic":
        segments = slic_segmentation(raster_stack, forest_mask, **seg_params)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    # Vectorize
    segments_gdf = vectorize_segments(segments, evt_transform, evt_crs)
    segments_gdf = segments_gdf.to_crs(PROJECT_CRS)

    # Apply BMP erase
    units_gdf = apply_bmp_erase(segments_gdf, streams, waterbodies, roads, bmp_config)

    # Compute metrics
    units_gdf = compute_metrics(units_gdf)

    # Add metadata
    units_gdf["unit_id"] = [
        f"mu_{county_code}_{strategy[:4]}_{i:08d}" for i in range(len(units_gdf))
    ]
    units_gdf["county_fips"] = county_code
    units_gdf["county_name"] = county_name
    units_gdf["strategy"] = strategy

    # Save
    output_gpkg = strategy_output_dir / "management_units.gpkg"
    units_gdf.to_file(output_gpkg, driver="GPKG")
    logger.info(f"Saved to {output_gpkg}")

    return units_gdf


def compare_strategies(
    naive_gdf: gpd.GeoDataFrame,
    felz_gdf: gpd.GeoDataFrame,
    slic_gdf: gpd.GeoDataFrame,
    output_dir: Path,
) -> pd.DataFrame:
    """
    Quantitatively compare strategies and generate comparison figures.

    Returns:
        DataFrame with summary statistics by strategy.
    """
    logger.info("Comparing strategies")

    # Add strategy labels
    naive_gdf["strategy"] = "Naive"
    felz_gdf["strategy"] = "Felzenszwalb"
    slic_gdf["strategy"] = "SLIC"

    # Combine
    all_units = pd.concat([naive_gdf, felz_gdf, slic_gdf], ignore_index=True)

    # Compute summary statistics
    summary_rows = []
    for strategy in ["Naive", "Felzenszwalb", "SLIC"]:
        strategy_units = all_units[all_units["strategy"] == strategy]

        summary_rows.append({
            "strategy": strategy,
            "n_units": len(strategy_units),
            "total_forest_ha": strategy_units["unit_area_ha"].sum(),
            "sliver_count": (strategy_units["size_class"] == "sliver_lt_min").sum(),
            "sliver_fraction": (strategy_units["size_class"] == "sliver_lt_min").sum() / len(strategy_units),
            "candidate_count": (strategy_units["size_class"] == "candidate").sum(),
            "large_count": (strategy_units["size_class"] == "large_gt_target").sum(),
            "median_area_ha": strategy_units["unit_area_ha"].median(),
            "mean_area_ha": strategy_units["unit_area_ha"].mean(),
            "median_compactness": strategy_units["compactness"].median(),
            "mean_compactness": strategy_units["compactness"].mean(),
        })

    summary = pd.DataFrame(summary_rows)

    # Save summary
    summary_csv = output_dir / "strategy_comparison.csv"
    summary.to_csv(summary_csv, index=False)
    logger.info(f"\nComparison summary:\n{summary}")

    # Generate comparison figures
    plot_strategy_comparison(all_units, output_dir)

    return summary


def plot_strategy_comparison(all_units: gpd.GeoDataFrame, output_dir: Path):
    """Generate comparison plots across strategies."""
    logger.info("Generating comparison plots")

    sns.set_style("whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # 1. Area distribution (ECDF)
    for strategy in ["Naive", "Felzenszwalb", "SLIC"]:
        strategy_units = all_units[all_units["strategy"] == strategy]
        areas_sorted = np.sort(strategy_units["unit_area_ha"])
        ecdf = np.arange(1, len(areas_sorted) + 1) / len(areas_sorted)
        axes[0, 0].plot(areas_sorted, ecdf, label=strategy, linewidth=2)

    axes[0, 0].set_xlabel("Unit Area (ha)")
    axes[0, 0].set_ylabel("ECDF")
    axes[0, 0].set_title("Unit Area Distribution (ECDF)")
    axes[0, 0].set_xscale("log")
    axes[0, 0].axvline(2, color="red", linestyle="--", alpha=0.5, label="Min threshold (2 ha)")
    axes[0, 0].axvline(40, color="orange", linestyle="--", alpha=0.5, label="Target max (40 ha)")
    axes[0, 0].legend()
    axes[0, 0].grid(True, alpha=0.3)

    # 2. Compactness distribution
    for strategy in ["Naive", "Felzenszwalb", "SLIC"]:
        strategy_units = all_units[all_units["strategy"] == strategy]
        axes[0, 1].hist(
            strategy_units["compactness"],
            bins=50,
            alpha=0.5,
            label=strategy,
            density=True,
        )

    axes[0, 1].set_xlabel("Polsby-Popper Compactness")
    axes[0, 1].set_ylabel("Density")
    axes[0, 1].set_title("Shape Compactness Distribution")
    axes[0, 1].legend()
    axes[0, 1].grid(True, alpha=0.3)

    # 3. Size class breakdown
    size_class_counts = all_units.groupby(["strategy", "size_class"]).size().unstack(fill_value=0)
    size_class_counts.plot(kind="bar", stacked=True, ax=axes[1, 0], color=["red", "green", "orange"])
    axes[1, 0].set_xlabel("Strategy")
    axes[1, 0].set_ylabel("Count")
    axes[1, 0].set_title("Size Class Breakdown by Strategy")
    axes[1, 0].legend(title="Size Class")
    axes[1, 0].set_xticklabels(axes[1, 0].get_xticklabels(), rotation=0)

    # 4. Area histogram (log scale)
    all_units_subset = all_units[all_units["unit_area_ha"] > 0.01]  # filter tiny slivers for visualization
    for strategy in ["Naive", "Felzenszwalb", "SLIC"]:
        strategy_units = all_units_subset[all_units_subset["strategy"] == strategy]
        axes[1, 1].hist(
            strategy_units["unit_area_ha"],
            bins=np.logspace(-1, 2.5, 50),
            alpha=0.5,
            label=strategy,
        )

    axes[1, 1].set_xlabel("Unit Area (ha)")
    axes[1, 1].set_ylabel("Count")
    axes[1, 1].set_title("Unit Area Histogram (log scale)")
    axes[1, 1].set_xscale("log")
    axes[1, 1].legend()
    axes[1, 1].grid(True, alpha=0.3)

    plt.tight_layout()
    fig_path = output_dir / "strategy_comparison.png"
    plt.savefig(fig_path, dpi=300, bbox_inches="tight")
    logger.info(f"Saved comparison figure to {fig_path}")
    plt.close()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Compare management-unit delineation strategies"
    )
    parser.add_argument("--county-fips", type=str, default="125",
                       help="Three-digit county FIPS code (default: 125 for Union)")
    parser.add_argument("--output-dir", type=Path,
                       default=Path("research/mgmt_units/outputs"),
                       help="Output directory")

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    county_fips = args.county_fips

    # 1. Load naive strategy results (already exists)
    logger.info("Loading naive strategy results")
    naive_path = Path(f"data/interim/management_units_smoke_union/{FLORIDA_FIPS}{county_fips}_union/candidate_management_units.gpkg")

    if not naive_path.exists():
        logger.error(f"Naive results not found at {naive_path}")
        logger.info("Run: uv run python -m pipeline.s3_management.sketch_management_units --county-fips 125 --output-dir data/interim/management_units_smoke_union")
        return

    naive_gdf = gpd.read_file(naive_path)
    naive_gdf = compute_metrics(naive_gdf)

    # 2. Run Felzenszwalb segmentation
    felz_gdf = process_segmentation_strategy(
        county_fips=county_fips,
        strategy="felzenszwalb",
        output_dir=output_dir,
        scale=100,
        sigma=0.5,
        min_size=50,
    )

    # 3. Run SLIC segmentation
    slic_gdf = process_segmentation_strategy(
        county_fips=county_fips,
        strategy="slic",
        output_dir=output_dir,
        n_segments=1000,
        compactness=10.0,
        sigma=1.0,
    )

    # 4. Compare strategies
    summary = compare_strategies(naive_gdf, felz_gdf, slic_gdf, output_dir)

    logger.info("Analysis complete!")
    logger.info(f"Results saved to {output_dir}")


if __name__ == "__main__":
    main()
