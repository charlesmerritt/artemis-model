# TreeMap COG County Summary Notebook

## Artifact

- Notebook: `notebooks/TreeMap_COG_County_Summary.ipynb`
- Dependency added: `rasterio>=1.5.0` in `pyproject.toml` / `uv.lock`

## Purpose

The notebook opens a direct COG URL or STAC Item URL, optionally clips it to an arbitrary vector footprint, and summarizes raster values by polygon. The default vector source is Census 2023 generalized counties filtered to Southeast state FIPS codes.

## Important behavior

- The user-provided example URL opens as a tiled `float32` EPSG:5070 GeoTIFF with `nodata=3.4028234663852886e+38`, so the notebook defaults to `SUMMARY_MODE = "continuous"`.
- For integer class rasters or USFS TreeMap `TM_ID`/`VALUE` rasters, switch to `SUMMARY_MODE = "categorical_counts"`.
- Outputs are written under `data/interim/treemap_county_summary/` whether Jupyter starts from the repo root or `notebooks/`.
- Remote COG reads are windowed one polygon at a time, avoiding full-raster download.

## Verification run

- Parsed all notebook code cells with `ast.parse`.
- Smoke-tested continuous summaries, categorical counts, and vector clipping against a synthetic 4x4 EPSG:5070 raster.
- Opened the provided remote COG and inspected metadata successfully.
