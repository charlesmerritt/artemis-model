"""
Step 4 — FVS execution pipeline
Responsibilities:
  4a. FVS binary wrapper (tree list + site attrs + keyword file → tidy dataframe)
  4b. Management regime keyword templates (regimes/*.key)
  4c. Trajectory library construction — unique (plot_id, regime, si_bin) combinations
  4d. Per-pixel painting → projection_cube.zarr

Build order:
  Run with "no_management" regime ONLY until growth validation (Step 5) passes.
  Do not enable harvest regimes before growth validation is documented.
"""
