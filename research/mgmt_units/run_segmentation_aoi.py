"""AOI-scoped runner for the segmentation delineation comparison.

The committed ``segmentation_delineation.py`` reads full-CONUS LANDFIRE and
statewide GDBs by local path (see ``process_segmentation_strategy``), which is
impractical outside the workstation. This runner reuses that module's
segmentation, vectorization, metric, and comparison functions but scopes them to
a single county using **pre-clipped, already-projected (EPSG:5070) inputs** — the
same interim layers the naive sketch pipeline emits under
``data/interim/management_units_smoke_union/<code>_<name>/``.

Two fixes over the committed ``process_segmentation_strategy`` code path, both
required to make it run and produce a fair comparison:

  1. **Forest mask.** ``create_forest_mask()`` keys on EVT codes 1000-2999, which
     selects ZERO pixels in the real LANDFIRE 2022 EVT for this AOI (its codes are
     7292+). We use the pipeline's authoritative pre-computed forest-mask raster
     (``landfire_evt_forest_mask_5070.tif``) instead of the code-range heuristic.
  2. **BMP erase.** Instead of re-deriving stream/road/water buffers from statewide
     GDBs, we erase with the exact buffers the naive pipeline already produced and
     saved to ``qa_layers.gpkg`` (``road_buffers`` / ``stream_buffers`` /
     ``waterbody_buffers``). Same erase geometry the naive units were cut with, so
     the strategies are compared on equal footing.

Usage:
    uv run python research/mgmt_units/run_segmentation_aoi.py \\
        --aoi-dir data/interim/management_units_smoke_union/12125_union \\
        --output-dir research/mgmt_units/outputs
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from shapely.ops import unary_union

import research.mgmt_units.segmentation_delineation as seg


def build_erase(aoi_dir: Path, crs) -> gpd.GeoDataFrame | None:
    """Union the naive pipeline's own BMP/road/water buffers into one erase layer."""
    parts = []
    for lyr in ("road_buffers", "stream_buffers", "waterbody_buffers"):
        try:
            g = gpd.read_file(aoi_dir / "qa_layers.gpkg", layer=lyr).to_crs(crs)
            if len(g):
                parts.append(unary_union(g.geometry.values))
        except Exception as exc:  # noqa: BLE001 - a missing QA layer is non-fatal
            print(f"  (skip {lyr}: {exc})")
    if not parts:
        return None
    return gpd.GeoDataFrame(geometry=[unary_union(parts)], crs=crs)


def run_strategy(name: str, seg_array: np.ndarray, transform, crs, erase_gdf) -> gpd.GeoDataFrame:
    gdf = seg.vectorize_segments(seg_array, transform, crs).to_crs(crs)
    if erase_gdf is not None:
        gdf = gpd.overlay(gdf, erase_gdf, how="difference")
    gdf = gdf.explode(index_parts=False, ignore_index=True)  # singlepart, like the naive layer
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    return seg.compute_metrics(gdf)


def main() -> None:
    ap = argparse.ArgumentParser(description="AOI-scoped segmentation strategy comparison")
    ap.add_argument("--aoi-dir", type=Path,
                    default=Path("data/interim/management_units_smoke_union/12125_union"),
                    help="Directory of pre-clipped EPSG:5070 inputs for one county")
    ap.add_argument("--output-dir", type=Path, default=Path("research/mgmt_units/outputs"))
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(args.aoi_dir / "landfire_evt_5070.tif") as s:
        evt = s.read(1).astype(float)
        transform, crs = s.transform, s.crs
    with rasterio.open(args.aoi_dir / "landfire_evt_forest_mask_5070.tif") as s:
        forest_mask = s.read(1) == 1
    print(f"raster {evt.shape}, forest pixels {int(forest_mask.sum()):,}")

    evt_max = float(evt[forest_mask].max()) if forest_mask.any() else 1.0
    evt_norm = np.where(forest_mask, evt / evt_max, 0.0)
    stack = np.stack([evt_norm, forest_mask.astype(float), np.full_like(evt_norm, 0.5)], axis=0)

    erase_gdf = build_erase(args.aoi_dir, crs)
    print(f"erase layers combined: {0 if erase_gdf is None else 1}")

    t0 = time.time()
    felz_seg = seg.felzenszwalb_segmentation(stack, forest_mask, scale=100, sigma=0.5, min_size=50)
    felz = run_strategy("felzenszwalb", felz_seg, transform, crs, erase_gdf)
    print(f"Felzenszwalb: {len(felz):,} units ({time.time() - t0:.1f}s)")

    t0 = time.time()
    slic_seg = seg.slic_segmentation(stack, forest_mask, n_segments=1000, compactness=10.0, sigma=1.0)
    slic = run_strategy("slic", slic_seg, transform, crs, erase_gdf)
    print(f"SLIC: {len(slic):,} units ({time.time() - t0:.1f}s)")

    naive = seg.compute_metrics(gpd.read_file(args.aoi_dir / "candidate_management_units.gpkg").to_crs(crs))
    print(f"Naive: {len(naive):,} units")

    summary = seg.compare_strategies(naive.copy(), felz.copy(), slic.copy(), args.output_dir)
    print("\n=== strategy comparison ===")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
