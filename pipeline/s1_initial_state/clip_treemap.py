"""
Step 1a — Clip TreeMap 2022 CONUS to Florida

Downloads the authoritative Florida state boundary from Census TIGER 2022,
reprojects to EPSG:5070, replaces the bounding-box placeholder in
config/extent.geojson, then clips the TreeMap CONUS raster using a windowed
read (memory-safe on the 57 GB CONUS file).

Outputs
-------
data/interim/florida_boundary_5070.gpkg   Florida polygon in EPSG:5070
data/interim/treemap_2022_fl.tif          TreeMap clipped to Florida, EPSG:5070

Usage
-----
    uv run python -m pipeline.s1_initial_state.clip_treemap
"""

import json
from pathlib import Path

import click
import geopandas as gpd
import numpy as np
import rasterio
import rasterio.features
import rasterio.mask
from rasterio.windows import from_bounds
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR   = PROJECT_ROOT / "config"
INTERIM_DIR  = PROJECT_ROOT / "data" / "interim"

TIGER_URL = (
    "https://www2.census.gov/geo/tiger/TIGER2022/STATE/tl_2022_us_state.zip"
)
FLORIDA_FIPS = "12"


def get_florida_boundary(cache_path: Path) -> gpd.GeoDataFrame:
    """
    Return Florida state boundary in EPSG:5070.

    Downloads Census TIGER 2022 state shapefile on first call;
    reads from cache_path on subsequent calls.
    """
    if cache_path.exists():
        console.log(f"[green]Using cached boundary:[/green] {cache_path}")
        return gpd.read_file(cache_path)

    console.log("[yellow]Downloading Census TIGER 2022 state boundaries...[/yellow]")
    states = gpd.read_file(TIGER_URL)
    florida_wgs84 = states[states["STATEFP"] == FLORIDA_FIPS].copy()
    if florida_wgs84.empty:
        raise RuntimeError(f"FIPS {FLORIDA_FIPS} not found in TIGER download")

    florida_5070 = florida_wgs84.to_crs("EPSG:5070")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    florida_5070.to_file(cache_path, driver="GPKG")
    console.log(f"[green]Cached boundary to:[/green] {cache_path}")
    return florida_5070


def update_extent_geojson(florida_gdf: gpd.GeoDataFrame) -> None:
    """
    Replace the bounding-box placeholder in config/extent.geojson with the
    authoritative Census TIGER polygon (reprojected back to WGS84 for GeoJSON).
    """
    florida_wgs84 = florida_gdf.to_crs("EPSG:4326")
    geom = florida_wgs84.geometry.iloc[0]
    props = florida_gdf.iloc[0].drop("geometry").to_dict()

    feature = {
        "type": "Feature",
        "properties": {
            "name": "Florida",
            "fips": FLORIDA_FIPS,
            "fvs_variant": "SN",
            "source": "Census TIGER/Line 2022 tl_2022_us_state.zip",
            "note": "Authoritative state boundary. Replaced bounding-box placeholder.",
            **{k: v for k, v in props.items() if k not in ("geometry",)},
        },
        "geometry": geom.__geo_interface__,
    }
    geojson = {"type": "FeatureCollection", "features": [feature]}
    out = CONFIG_DIR / "extent.geojson"
    with open(out, "w") as f:
        json.dump(geojson, f, indent=2)
    console.log(f"[green]Updated extent.geojson[/green] with TIGER polygon")


def clip_treemap(
    src_path: Path,
    florida_gdf: gpd.GeoDataFrame,
    out_path: Path,
) -> None:
    """
    Clip the CONUS TreeMap raster to Florida using a windowed read.

    Strategy:
      1. Compute the bounding-box window in raster pixel space (avoids
         reading the full 57 GB CONUS file into memory).
      2. Read just that window.
      3. Apply the exact Florida polygon mask.
      4. Write with LZW compression (uint32, nodata=4294967295).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shapes = [florida_gdf.geometry.iloc[0].__geo_interface__]

    with rasterio.open(src_path) as src:
        nodata = int(src.nodata)

        # ── 1. Bounding-box window ────────────────────────────────────────
        minx, miny, maxx, maxy = florida_gdf.total_bounds
        window = from_bounds(minx, miny, maxx, maxy, src.transform)
        window_transform = src.window_transform(window)

        console.log(
            f"Florida window: {int(window.height)} rows × {int(window.width)} cols"
            f" ({int(window.height) * int(window.width) / 1e6:.1f}M pixels)"
        )

        # ── 2. Read the window ────────────────────────────────────────────
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Reading TreeMap window...", total=None)
            data = src.read(1, window=window)
            progress.update(task, description="Reading TreeMap window... done")

        # ── 3. Apply Florida mask ─────────────────────────────────────────
        # geometry_mask: True = outside all geometries (pixels to null out)
        outside_mask = rasterio.features.geometry_mask(
            shapes,
            out_shape=data.shape,
            transform=window_transform,
            invert=False,   # True = outside
        )
        data[outside_mask] = nodata

        out_profile = src.profile.copy()
        out_profile.update(
            height=data.shape[0],
            width=data.shape[1],
            transform=window_transform,
            compress="lzw",
            tiled=True,
            blockxsize=512,
            blockysize=512,
            driver="GTiff",
        )

    # ── 4. Write output ───────────────────────────────────────────────────

    with rasterio.open(out_path, "w", **out_profile) as dst:
        dst.write(data, 1)

    valid_pixels = int(np.sum(data != nodata))
    console.log(
        f"[green]Written:[/green] {out_path}\n"
        f"  Valid (forest) pixels: {valid_pixels:,}\n"
        f"  Nodata pixels:         {data.size - valid_pixels:,}"
    )


@click.command()
@click.option(
    "--treemap-tif",
    default=None,
    help="Override path to TreeMap2022_CONUS.tif (default: reads config/data_paths.yaml)",
)
@click.option(
    "--out-dir",
    default=str(INTERIM_DIR),
    show_default=True,
    help="Output directory for clipped raster",
)
@click.option(
    "--skip-update-extent",
    is_flag=True,
    default=False,
    help="Skip updating config/extent.geojson with TIGER polygon",
)
def main(treemap_tif, out_dir, skip_update_extent):
    """Clip TreeMap 2022 CONUS raster to Florida state boundary."""
    import yaml

    if treemap_tif is None:
        paths_file = CONFIG_DIR / "data_paths.yaml"
        with open(paths_file) as f:
            paths = yaml.safe_load(f)
        treemap_tif = paths["raw"]["treemap_2022"]["tif"]

    treemap_path = Path(treemap_tif)
    out_path     = Path(out_dir) / "treemap_2022_fl.tif"
    cache_path   = INTERIM_DIR / "florida_boundary_5070.gpkg"

    console.rule("[bold blue]Step 1a — Clip TreeMap to Florida")

    florida_gdf = get_florida_boundary(cache_path)
    console.log(f"Florida CRS: {florida_gdf.crs}")
    console.log(f"Florida bounds (EPSG:5070): {florida_gdf.total_bounds}")

    if not skip_update_extent:
        update_extent_geojson(florida_gdf)

    clip_treemap(treemap_path, florida_gdf, out_path)

    console.rule("[bold green]Step 1a complete")


if __name__ == "__main__":
    main()
