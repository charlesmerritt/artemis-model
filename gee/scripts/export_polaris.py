"""
GEE export — POLARIS soils for Florida

POLARIS (Chaney et al. 2019) — 30m probabilistic soil maps for CONUS.
GEE community catalog asset: projects/sat-io/open-datasets/POLARIS/

Exports depth-weighted mean for five properties over 0–100 cm:

  polaris_awc_fl.tif       Available water capacity (theta_s − theta_r)
  polaris_clay_fl.tif      Clay content (%)
  polaris_sand_fl.tif      Sand content (%)
  polaris_ph_fl.tif        Soil pH (water)
  polaris_bd_fl.tif        Bulk density (g/cm³)

Depth layers and weights (thickness-weighted to 100 cm):
  0–5 cm    →  5/100 = 0.05
  5–15 cm   → 10/100 = 0.10
  15–30 cm  → 15/100 = 0.15
  30–60 cm  → 30/100 = 0.30
  60–100 cm → 40/100 = 0.40

Note: POLARIS community catalog assets can update without versioning.
Record the export date in versions.lock (gee_export_date field).

Usage
-----
    uv run python gee/scripts/export_polaris.py
"""

import click
from gee_utils import export_to_drive, get_florida_geometry, init_ee, start_and_report

# POLARIS asset root in GEE community catalog
POLARIS_ROOT = "projects/sat-io/open-datasets/POLARIS"

# Depth bands and thickness weights (sum = 1.0 over 0–100 cm)
DEPTH_LAYERS = [
    ("p0_5",    0.05),
    ("p5_15",   0.10),
    ("p15_30",  0.15),
    ("p30_60",  0.30),
    ("p60_100", 0.40),
]

# Properties to export
# AWC is derived: theta_s − theta_r (saturated minus residual water content)
PROPERTIES = ["theta_s", "theta_r", "clay", "sand", "ph", "bd"]


def depth_weighted_mean(prop: str) -> "ee.Image":
    """Load all depth layers for a property and compute thickness-weighted mean."""
    import ee

    weighted_sum = None
    for depth, weight in DEPTH_LAYERS:
        asset_id = f"{POLARIS_ROOT}/{prop}/{depth}"
        layer = ee.Image(asset_id).multiply(weight)
        weighted_sum = layer if weighted_sum is None else weighted_sum.add(layer)
    return weighted_sum


@click.command()
@click.option("--folder",  default="forest_projection_fl", show_default=True)
@click.option("--project", default=None, help="GEE project ID")
def main(folder, project):
    """Export POLARIS depth-weighted soil properties for Florida."""
    import ee

    init_ee(project)
    florida = get_florida_geometry()

    print(f"\nExporting POLARIS soils (depth-weighted 0–100 cm) → Drive/{folder}/")
    print("─" * 60)

    # Load all properties first
    prop_images = {}
    for prop in PROPERTIES:
        prop_images[prop] = depth_weighted_mean(prop)

    # Derived AWC = theta_s − theta_r
    awc = prop_images["theta_s"].subtract(prop_images["theta_r"])

    exports = [
        (awc,                    "polaris_awc_fl",  "AWC (theta_s − theta_r)"),
        (prop_images["clay"],    "polaris_clay_fl", "Clay (%)"),
        (prop_images["sand"],    "polaris_sand_fl", "Sand (%)"),
        (prop_images["ph"],      "polaris_ph_fl",   "pH (water)"),
        (prop_images["bd"],      "polaris_bd_fl",   "Bulk density (g/cm³)"),
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
    print("\nIMPORTANT: Record today's date in versions.lock → datasets.polaris.gee_export_date")


if __name__ == "__main__":
    main()
