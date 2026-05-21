"""
Eastern US Forest Projection Pipeline
======================================
Deterministic, pixel-level (30 m) forward projection of forest stand dynamics.
FVS Southern variant | TreeMap 2022 | FIA CONUS SQLite | LCMS management calibration.

Build order (enforced by dependency structure):
  s1_initial_state → s2_site_attributes → s3_management →
  s4_fvs (growth only) → s5_validation (growth) →
  s3_management (harvest model) → s4_fvs (with harvest) → s5_validation (full) →
  s6_outputs
"""
