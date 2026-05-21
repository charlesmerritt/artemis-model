"""
GEE export — Terrain derivatives for Florida (3DEP / SRTM)

Exports four rasters clipped to Florida, EPSG:5070, snapped to TreeMap grid:

  terrain_elevation_fl.tif    Elevation (m)
  terrain_slope_fl.tif        Slope (degrees)
  terrain_aspect_fl.tif       Aspect (degrees, 0=flat, 1=N … 360=N)
  terrain_tpi_fl.tif          Topographic Position Index (optional, 300m kernel)

DEM source priority:
  1. USGS/3DEP/1_3_arc_second  (10m native, ~0.3" resolution) — preferred
  2. USGS/SRTMGL1_003           (30m native, global SRTM 1") — fallback

Florida is essentially flat (<100m range outside Panhandle).
Slope and aspect will be near-zero for most of the peninsula; document
in methods that terrain covariates contribute minimally to the FL site
index model and will be more informative at expansion to Appalachian states.

Usage
-----
    uv run python gee/scripts/export_terrain.py
    uv run python gee/scripts/export_terrain.py --dem-source srtm
"""

import click
from gee_utils import export_to_drive, get_florida_geometry, init_ee, start_and_report


@click.command()
@click.option(
    "--dem-source",
    default="3dep",
    type=click.Choice(["3dep", "srtm"]),
    show_default=True,
    help="DEM source: 3dep (preferred, 10m) or srtm (fallback, 30m)",
)
@click.option("--export-tpi", is_flag=True, default=False,
              help="Also export Topographic Position Index")
@click.option("--folder",  default="forest_projection_fl", show_default=True)
@click.option("--project", default=None, help="GEE project ID")
def main(dem_source, export_tpi, folder, project):
    """Export elevation, slope, aspect (and optionally TPI) for Florida."""
    import ee

    init_ee(project)
    florida = get_florida_geometry()

    # ── Load DEM ──────────────────────────────────────────────────────────
    if dem_source == "3dep":
        # 3DEP 1/3 arc-second (~10m). GEE resamples to 30m via crsTransform.
        # Verify asset ID in GEE catalog: search "3DEP" at developers.google.com/earth-engine/datasets
        try:
            dem = ee.Image("USGS/3DEP/1_3_arc_second").select("elevation")
            print("DEM source: USGS/3DEP/1_3_arc_second (10m → resampled to 30m)")
        except Exception:
            print("WARNING: 3DEP asset unavailable, falling back to SRTM")
            dem_source = "srtm"

    if dem_source == "srtm":
        dem = ee.Image("USGS/SRTMGL1_003").select("elevation")
        print("DEM source: USGS/SRTMGL1_003 (SRTM 30m)")

    # ── Derivatives ───────────────────────────────────────────────────────
    terrain  = ee.Terrain.products(dem)
    slope    = terrain.select("slope")     # degrees
    aspect   = terrain.select("aspect")    # degrees (0/360=N, 90=E, etc.)

    print(f"\nExporting terrain → Drive/{folder}/")
    print("─" * 60)

    tasks = [
        (dem.toFloat(),    "terrain_elevation_fl", "Elevation (m)"),
        (slope.toFloat(),  "terrain_slope_fl",     "Slope (degrees)"),
        (aspect.toFloat(), "terrain_aspect_fl",    "Aspect (degrees)"),
    ]

    if export_tpi:
        # TPI: elevation minus mean elevation in a 300m neighborhood
        kernel = ee.Kernel.circle(radius=300, units="meters")
        mean_elev = dem.reduceNeighborhood(
            reducer=ee.Reducer.mean(), kernel=kernel
        )
        tpi = dem.subtract(mean_elev).toFloat()
        tasks.append((tpi, "terrain_tpi_fl", "Topographic Position Index"))

    for image, description, label in tasks:
        task = export_to_drive(
            image=image,
            description=description,
            folder=folder,
            region=florida,
        )
        start_and_report(task, label)

    print(f"\n{len(tasks)} tasks submitted.")


if __name__ == "__main__":
    main()
