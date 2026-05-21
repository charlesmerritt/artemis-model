"""
Step 5 — Validation
All validation must pass before any results are reported externally.

Phase 1 (growth only):
  - Hindcast against FIA remeasurements (2010-2014 → 2018-2022): BA, TPA, QMD, volume
  - BIGMAP year-0 cross-check: projected biomass vs BIGMAP 2014-2018
  - EVALIDator spatial pattern check: county-level aggregates

Phase 2 (with harvest):
  - Harvest hindcast against LCMS Tree Removal holdout (2015-2024)
  - Spatial agreement: Cohen's kappa + AUROC at pixel level
  - Area harvested per year, per state, per ownership class

Phase 3 (sensitivity):
  - Site index ±10% perturbation; document output sensitivity
"""
