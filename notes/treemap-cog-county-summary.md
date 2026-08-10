# TreeMap COG County Summary Notebook

## Artifact

- Notebook: `notebooks/TreeMap_COG_County_Summary.ipynb`
- Dependency added: `rasterio>=1.5.0` in `pyproject.toml` / `uv.lock`

## Purpose

The notebook opens a direct COG URL or STAC Item URL, optionally clips it to an arbitrary vector footprint, and summarizes raster values by polygon. The default vector source is Census 2023 generalized counties filtered to Southeast state FIPS codes.

## Important behavior

- The user-provided example URL opens as a tiled `float32` EPSG:5070 GeoTIFF with `nodata=3.4028234663852886e+38`, so the notebook defaults to `SUMMARY_MODE = "continuous"`.
- For integer class rasters or USFS TreeMap `TM_ID`/`VALUE` rasters, switch to `SUMMARY_MODE = "categorical_counts"`.
- In categorical mode the raster value is a **join key**, not a measurement — the notebook's closing note tells you to join the output CSV back to the VAT to attach `PLT_CN`, forest type, basal area and carbon. A float band broke that join twice: `treemap_value` was written as `2623.0` where the VAT holds `2623`, and above each float type's exact-integer limit (`2**24` for float32, `2**53` for float64) distinct TM_IDs collapse onto the same number. `_exact_category_values()` now casts genuinely integral bands to int64 and raises on anything it cannot represent exactly. Note the example URL is `float32`, so this path is live, not hypothetical. See [Identifier precision](identifier-precision.md).
- Outputs are written under `data/interim/treemap_county_summary/` whether Jupyter starts from the repo root or `notebooks/`.
- Remote COG reads are windowed one polygon at a time, avoiding full-raster download.

## Verification run

- Parsed all notebook code cells with `ast.parse`.
- Smoke-tested continuous summaries, categorical counts, and vector clipping against a synthetic 4x4 EPSG:5070 raster.
- Opened the provided remote COG and inspected metadata successfully.
- Re-verified 2026-07-14: remote COG still opens over the network (`GTiff 154179×97279 float32 EPSG:5070`). This is the only notebook in `notebooks/` that needs neither the `/mnt/d` drive nor Earth Engine, so it is the most runnable today.
