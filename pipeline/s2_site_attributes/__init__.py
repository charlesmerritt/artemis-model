"""
Step 2 — Per-pixel site attributes
Responsibilities:
  2a. POLARIS soils (via GEE community catalog)
  2b. 3DEP terrain → slope, aspect, elevation (+ optional TPI, TWI)
  2c. PRISM climate normals → tmean, precip, growing season
  2d. Per-pixel site index (default: inherit from FIA SITETREE; better: regression model)
  2e. Stack and chunk → site_attributes.zarr
"""
