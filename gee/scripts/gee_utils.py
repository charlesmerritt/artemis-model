"""
Shared GEE utilities for all export scripts.

All exports use the project CRS and the TreeMap snap grid, both declared in
`config/projection.yaml` and read through `pipeline/spatial_ref.py` — the same single
source of truth the local pipeline uses, so a GEE export and a local raster cannot end up
on two different grids.

  CRS:          the project CRS (EPSG:5070, NAD83 / Conus Albers)
  crsTransform: the exact TreeMap 2022 affine
  Region:       Florida state boundary from TIGER

Never use scale= in exports — always use crsTransform=. `scale=` lets GEE pick its own
origin, and TreeMap's origin is half a pixel off the round 5070 grid, so the result is a
silent 15 m misalignment rather than an error.
"""

import sys
from pathlib import Path

import ee

# These scripts are run as files (`uv run python gee/scripts/export_*.py`), so the repo
# root is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.spatial_ref import project_crs, snap_transform  # noqa: E402

# ── Snap grid ─────────────────────────────────────────────────────────────────
# The transform is TreeMap2022_CONUS.tif's affine, read via rasterio, in GDAL order
# [xScale, xShearing, xTranslation, yShearing, yScale, yTranslation].
TREEMAP_CRS           = project_crs()
TREEMAP_CRS_TRANSFORM = snap_transform()


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
      - CRS: the project CRS (config/projection.yaml)
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
