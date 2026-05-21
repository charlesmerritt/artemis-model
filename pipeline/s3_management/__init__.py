"""
Step 3 — Management and ownership layers
Responsibilities:
  3a. Stand boundary delineation (segmentation on stacked rasters)
  3b. Variable-width riparian buffers from NHD + state BMP rules
  3c. Ownership assignment (Harris et al. 2025) + harvest behavior model
        Phase 1: ownership raster only (needed for growth-first FVS runs)
        Phase 2: harvest model fitting (after growth validation passes)
  3d. Hindcast validation of harvest model against LCMS holdout
"""
