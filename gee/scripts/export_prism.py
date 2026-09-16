"""
GEE export — PRISM 30-year climate normals (1991–2020) for Florida

GEE asset: OREGONSTATE/PRISM/Norm91m
Native resolution: ~800m. Resampled to 30m via bilinear during GEE export.
Document this scale mismatch in methods writeup.

Exports:
  prism_tmean_fl.tif     Mean annual temperature (°C) — annual mean of 12 monthly means
  prism_ppt_fl.tif       Mean annual precipitation (mm) — sum of 12 monthly means
  prism_tdmean_fl.tif    Mean dew point temperature (°C) — annual mean
  prism_vpdmax_fl.tif    Mean max vapour pressure deficit (hPa) — annual mean

PRISM Norm91m band naming: each band is "{variable}_{month:02d}" e.g. tmean_01.
Annual aggregation:
  tmean / tdmean / vpdmax: mean of 12 monthly bands
  ppt:                     sum of 12 monthly bands

Usage
-----
    uv run python gee/scripts/export_prism.py
"""

from typing import TYPE_CHECKING

import click
from gee_utils import export_to_drive, get_florida_geometry, init_ee, start_and_report

if TYPE_CHECKING:
    # earthengine-api is heavy and needs auth, so it is imported lazily inside
    # each function. This makes the "ee.Image" annotations resolvable to type
    # checkers and linters without importing it at module load.
    import ee

PRISM_ASSET = "OREGONSTATE/PRISM/Norm91m"

MONTHS = [f"{m:02d}" for m in range(1, 13)]


def annual_mean(img: "ee.Image", variable: str) -> "ee.Image":
    """Compute annual mean across 12 monthly bands for a given variable."""
    import ee
    bands = [f"{variable}_{m}" for m in MONTHS]
    return img.select(bands).reduce(ee.Reducer.mean()).rename(variable)


def annual_sum(img: "ee.Image", variable: str) -> "ee.Image":
    """Compute annual sum across 12 monthly bands (for precipitation)."""
    import ee
    bands = [f"{variable}_{m}" for m in MONTHS]
    return img.select(bands).reduce(ee.Reducer.sum()).rename(variable)


@click.command()
@click.option("--folder",  default="forest_projection_fl", show_default=True)
@click.option("--project", default=None, help="GEE project ID")
def main(folder, project):
    """Export PRISM 1991–2020 climate normals for Florida."""
    import ee

    init_ee(project)
    florida = get_florida_geometry()
    prism   = ee.Image(PRISM_ASSET)

    print(f"\nExporting PRISM 1991–2020 normals → Drive/{folder}/")
    print("Native resolution: ~800m → resampled to 30m (bilinear via crsTransform)")
    print("─" * 60)

    exports = [
        (annual_mean(prism, "tmean"),  "prism_tmean_fl",  "Mean annual temperature (°C)"),
        (annual_sum( prism, "ppt"),    "prism_ppt_fl",    "Mean annual precipitation (mm)"),
        (annual_mean(prism, "tdmean"), "prism_tdmean_fl", "Mean dew point temperature (°C)"),
        (annual_mean(prism, "vpdmax"), "prism_vpdmax_fl", "Mean max VPD (hPa)"),
    ]

    for image, description, label in exports:
        task = export_to_drive(
            image=image.toFloat(),
            description=description,
            folder=folder,
            region=florida,
        )
        start_and_report(task, label)

    print(f"\n{len(exports)} tasks submitted.")
    print("\nIMPORTANT: Record today's date in versions.lock → datasets.prism.gee_export_date")
    print("NOTE: Bilinear resampling 800m → 30m must be documented in methods writeup.")


if __name__ == "__main__":
    main()
