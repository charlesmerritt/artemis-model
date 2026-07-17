"""
Shared GEE utilities for all export scripts.

All exports use:
  CRS:          EPSG:5070 (CONUS Albers Equal Area)
  crsTransform: [30, 0, -2361585, 0, -30, 3177435]  ← exact TreeMap 2022 snap grid
  Region:       Florida state boundary from TIGER

Never use scale= in exports — always use crsTransform= to guarantee
pixel alignment with TreeMap 2022.
"""

import ee

# ── Snap grid ─────────────────────────────────────────────────────────────────
# Derived from TreeMap2022_CONUS.tif affine transform (confirmed via rasterio).
# [xScale, xShearing, xTranslation, yShearing, yScale, yTranslation]
TREEMAP_CRS           = "EPSG:5070"
TREEMAP_CRS_TRANSFORM = [30, 0, -2361585, 0, -30, 3177435]


def init_ee(project: str | None = None) -> None:
    """Initialize Earth Engine. Pass project= if needed for your account."""
    if project:
        ee.Initialize(project=project)
    else:
        ee.Initialize()


def get_florida_geometry() -> ee.Geometry:
    """
    Return the Florida state boundary as an ee.Geometry from TIGER 2018.
    STATEFP == '12' is Florida.
    """
    return (
        ee.FeatureCollection("TIGER/2018/States")
        .filter(ee.Filter.eq("STATEFP", "12"))
        .geometry()
    )


def export_to_drive(
    image: ee.Image,
    description: str,
    folder: str = "forest_projection_fl",
    region: ee.Geometry | None = None,
    max_pixels: int = 1e10,
) -> ee.batch.Task:
    """
    Submit a GEE Drive export task with the standard snap grid.

    All exports:
      - CRS: EPSG:5070
      - crsTransform: TreeMap snap grid
      - folder: forest_projection_fl (default)
      - fileFormat: GeoTIFF

    Returns the task object (call .start() then monitor with task.status()).
    """
    if region is None:
        region = get_florida_geometry()

    task = ee.batch.Export.image.toDrive(
        image=image,
        description=description,
        folder=folder,
        fileNamePrefix=description,
        region=region,
        crs=TREEMAP_CRS,
        crsTransform=TREEMAP_CRS_TRANSFORM,
        maxPixels=max_pixels,
        fileFormat="GeoTIFF",
    )
    return task


def start_and_report(task: ee.batch.Task, description: str) -> None:
    """Start a task and print its ID for monitoring."""
    task.start()
    print(f"  ✓  {description}")
    print(f"     Task ID: {task.id}")
    print("     Monitor: https://code.earthengine.google.com/tasks")
