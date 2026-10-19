"""Paths for the FL5 harvest-scheduling deck: LETO CA stands on the improved TreeMap and NWOS.

Shared R2-staged inputs (TreeMap VAT, the five-county FIA database, NHD streams) are the ones
``experiments/2026-08-24_leto-ca-forest-viz/01_stage_aoi.py`` stages; constants come from
that experiment's ``common.py`` so the CA, riparian rules and owner legend stay identical.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

DECK_DIR = Path(__file__).resolve().parent
REPO = DECK_DIR.parents[1]
WORK = DECK_DIR / "work"
OUTPUTS = DECK_DIR / "outputs"

IMPROVED = REPO / "data/processed/improved-rasters-evt2022-gate-v2/aoi_5county"
TREEMAP_TIF = IMPROVED / "treemap2022_improved.tif"
TREEMAP_PROVENANCE_TIF = IMPROVED / "treemap2022_provenance.tif"
NWOS_TIF = IMPROVED / "nwos2022_improved.tif"
PROVENANCE_ADDED_BACK_YOUNG = 4

ATTRIBUTES_NPZ = WORK / "attributes.npz"
VAT_CSV = WORK / "vat_extended.csv"
YOUNG_MATCHES_CSV = OUTPUTS / "young_plot_matches.csv"
SEGMENTATION_NPZ = WORK / "segmentation.npz"
MU_SUMMARY_CSV = WORK / "mu_summary.csv"
MU_DONOR_WEIGHTS_CSV = WORK / "mu_donor_weights.csv"
SEGMENTATION_QA_JSON = OUTPUTS / "segmentation_qa.json"

AGE_SEED = 20261005


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VIZ_DIR = REPO / "experiments/2026-08-24_leto-ca-forest-viz"
sys.path.insert(0, str(REPO))
viz = _load("viz_common", VIZ_DIR / "common.py")
viz_attributes = _load("viz_attributes", VIZ_DIR / "02_build_attributes.py")
viz_segment = _load("viz_segment", VIZ_DIR / "03_ca_segment.py")
young_plots = _load("young_plots", DECK_DIR / "young_plots.py")
