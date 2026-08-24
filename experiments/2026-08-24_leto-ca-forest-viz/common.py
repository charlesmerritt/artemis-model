"""Shared paths and constants for the LETO-CA / ARTEMIS-FVS visualization experiment.

Data staged from the R2 bucket (see README.md in this directory for the exact
provenance of every input). All rasters live on the TreeMap 2022 30 m grid in
EPSG:5070, snapped to the TreeMap affine — the project's working grid.
"""

from __future__ import annotations

import os
from pathlib import Path

EXPERIMENT_DIR = Path(__file__).resolve().parent
WORK = EXPERIMENT_DIR / "work"
OUTPUTS = EXPERIMENT_DIR / "outputs"

# Staged input data. STAGE_ROOT is where 01_stage_aoi.py materializes R2 pulls;
# override with ARTEMIS_STAGE_ROOT when the default scratch location is elsewhere.
STAGE_ROOT = Path(os.environ.get("ARTEMIS_STAGE_ROOT", WORK / "staged"))

AOI_TREEMAP_TIF = STAGE_ROOT / "aoi_treemap2022.tif"
AOI_OWNERSHIP_TIF = STAGE_ROOT / "aoi_ownership.tif"
TREEMAP_VAT_DBF = STAGE_ROOT / "treemap_vat.dbf"
FIA_DB = STAGE_ROOT / "FIA_5county_consolidated.db"
STREAMS_SHP = STAGE_ROOT / "FL_5_Co_Streams" / "FL_5_Co_Streams.shp"

# AOI: an ~13.2 x 14.8 km window around White Springs, FL (Columbia County),
# spanning the west edge of Osceola NF (federal), Big Shoals State Forest
# (state), industrial timberland (corporate) and family forest, with the
# Suwannee River crossing it. Selected by scanning the Harris ownership raster
# across the five-county pilot for the window with all four owner classes and
# the most perennial stream length (01_stage_aoi.py --scan).
AOI_BOUNDS_4269 = (-82.7118, 30.2638, -82.5923, 30.3833)

TREEMAP_NODATA = 4294967295
OWNERSHIP_NODATA = 15
CELL_ACRES = 900 * 0.000247105381  # 30 m cell in acres

# Harris et al. (2025) RDS-2025-0045 raster legend.
HARRIS_CLASSES = {
    0: "Unknown Forest",
    1: "Non-Forest",
    2: "Water",
    3: "Family Forest",
    4: "Corporate Forest",
    5: "Tribal Forest",
    6: "Federal Forest",
    7: "State Forest",
    8: "Local Forest",
}

# Harris value -> ARTEMIS owner class (config/ownership_policy.yaml vocabulary).
HARRIS_TO_OWNER_CLASS = {
    0: "unknown",
    3: "private_family",
    4: "private_industrial",  # corporate default per ownership_policy.yaml
    5: "tribal",
    6: "federal",
    7: "state",
    8: "local",
}

# Riparian buffer rules by NHD FCode, from LETO 02_segment_treemap.py
# FLOWLINE_BUFFER_RULES (35 ft intermittent / 75 ft perennial first-pass
# assumptions). 55800 (artificial path = the mapped channel of the Suwannee
# River and other double-banked streams) is buffered at the perennial
# distance; it is not in LETO's rule table, which predates this layer.
FLOWLINE_BUFFER_RULES_FT = {
    46000: ("Unclassified Stream", 35.0),
    46003: ("Intermittent Stream", 35.0),
    46006: ("Perennial Stream", 75.0),
    46007: ("Ephemeral Stream", 0.0),
    55800: ("Artificial Path (river channel)", 75.0),
}
FT_TO_M = 0.3048

# Projection frame (config/projection.yaml vocabulary).
INV_YEAR = 2022
CYCLE_YEARS = 5
NUM_CYCLES = 10
HORIZON_YEARS = CYCLE_YEARS * NUM_CYCLES

SEED = 20260824
