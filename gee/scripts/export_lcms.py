"""
GEE export — LCMS v2024.10 for Florida

Exports two stacks clipped to Florida in EPSG:5070 snapped to TreeMap grid:

  lcms_land_cover_fl_YYYY.tif   Land_Cover band, one file per year 1985-2024
  lcms_change_fl_YYYY.tif       Change band, one file per year 1985-2024

LCMS Land_Cover classes (v2024.10):
  1  Trees
  2  Tall Shrubs & Trees Mix (Montane only)
  3  Shrubs & Trees Mix
  4  Grass/Forb/Herb & Trees Mix
  5  Barren & Trees Mix
  6  Tall Shrubs
  7  Shrubs & Grass/Forb/Herb Mix
  8  Grass/Forb/Herb
  9  Barren
  10 Exotics
  11 Developed
  12 Agriculture
  13 Water

LCMS Change classes (v2024.10):
  1  Stable
  2  High Probability Slow Loss
  3  Moderate Probability Slow Loss
  4  Slight Probability Slow Loss
  5  High Probability Fast Loss
  6  Moderate Probability Fast Loss
  7  Slight Probability Fast Loss
  8  Stable (No data)
  9  High Probability Gain
  10 Moderate Probability Gain
  11 Slight Probability Gain

For the harvest model, "Tree Removal" pixels are those where:
  - Land_Cover transitions FROM class 1 (Trees)
  - Change class is 2, 3, 4, 5, 6, or 7 (any loss probability)
The exact threshold (high vs moderate vs slight) is a modeling decision;
export all years and filter during harvest model fitting in Step 3c.

Usage
-----
    uv run python gee/scripts/export_lcms.py
    uv run python gee/scripts/export_lcms.py --start-year 2015 --end-year 2024
    uv run python gee/scripts/export_lcms.py --folder my_drive_folder
"""

import click
from gee_utils import export_to_drive, get_florida_geometry, init_ee, start_and_report

LCMS_ASSET = "projects/lcms-292214/assets/CONUS/LCMS_CONUS_v2024-10"


@click.command()
@click.option("--start-year", default=1985, show_default=True)
@click.option("--end-year",   default=2024, show_default=True)
@click.option("--folder",     default="forest_projection_fl", show_default=True)
@click.option("--project",    default=None, help="GEE project ID")
def main(start_year, end_year, folder, project):
    """Export LCMS Land_Cover and Change bands for Florida, one file per year."""
    import ee

    init_ee(project)
    florida = get_florida_geometry()
    lcms    = ee.ImageCollection(LCMS_ASSET)

    print(f"\nExporting LCMS {start_year}–{end_year} → Drive/{folder}/")
    print("─" * 60)

    for year in range(start_year, end_year + 1):
        img = lcms.filter(ee.Filter.eq("year", year)).first()

        # Land cover (uint8, classes 1–13)
        lc_task = export_to_drive(
            image=img.select("Land_Cover").toUint8(),
            description=f"lcms_land_cover_fl_{year}",
            folder=folder,
            region=florida,
        )
        start_and_report(lc_task, f"LCMS Land_Cover {year}")

        # Change (uint8, classes 1–11)
        ch_task = export_to_drive(
            image=img.select("Change").toUint8(),
            description=f"lcms_change_fl_{year}",
            folder=folder,
            region=florida,
        )
        start_and_report(ch_task, f"LCMS Change     {year}")

    print(f"\n{(end_year - start_year + 1) * 2} tasks submitted.")
    print("Monitor all tasks: https://code.earthengine.google.com/tasks")


if __name__ == "__main__":
    main()
