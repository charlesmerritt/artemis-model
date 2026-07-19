# Weekly artifact — 2026-07-19

## Artifact

ARTEMIS's flagship deliverable: **FVS-projected basal-area rasters for the
five-county north-Florida pilot**, produced by the repository's
`pipeline/s4_fvs/paint_fvs_to_raster.py`. This is the headline forest-projection
product the whole pipeline builds toward — TreeMap 2022 pixels reclassified so
each forested pixel carries the Forest Vegetation Simulator (Southern variant)
projection for the stand it belongs to.

Files in this folder:

| File | What it is |
|---|---|
| `basal_area_yr0_initial.tif` | Initial condition (year 0) basal area, sq ft/ac. GeoTIFF, EPSG:5070, 30 m, Float32, nodata −9999. |
| `basal_area_2076_final.tif` | End-of-projection (calendar year 2076, ~50 yr, no management) basal area, same grid/format. |
| `map.png` | Two-panel render of the two GeoTIFFs (shared color scale) for quick viewing. Rendered from the GeoTIFFs above — no data was hand-authored. |

**Why this artifact:** `README.md` / `PLAN.md` frame ARTEMIS as spatially
explicit forest projection; the painted FVS raster is the single most important
concrete output the committed code produces (the management-unit sketcher is the
only other implemented slice, and is still pilot/QA stage). The painter also has
the cleanest reproducibility story: deterministic, fully tested
(`tests/test_s4_paint_fvs_to_raster.py`, 4 passed), and self-selecting on data
vintage.

**Result summary (matches `notes/fvs-to-raster-painting.md` exactly):**
- FVS trajectory: 9,259 rows, 693 stands, calendar years 1997–2076.
- Selected pairing `treemap2022`: 693/693 stands covered (100%),
  5,413,921/5,414,572 raster pixels matched (100%).
- Painted 5,413,921 pixels (100% of valid). Mean basal area rises 83 → 189
  sq ft/ac from year 0 to 2076 — the expected no-management accumulation signal.

## R2 inputs pulled

Only the minimal files the painter reads were downloaded from the Cloudflare R2
bucket `r2:artemis-r2` (bucket `data/` maps to the repo's `/mnt/d`), via the
S3-compatible API (rclone.org and github.com release downloads are blocked by
egress policy, so `boto3` with the preconfigured `RCLONE_CONFIG_R2_*`
credentials was used instead):

| R2 key | Downloaded to | Size |
|---|---|---|
| `data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv` | `data/interim/no_management_fl5co_fvs_output/fvs_trajectory.csv` | 2.97 MB |
| `data/TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv` | `/mnt/d/TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv` | 64 KB |
| `data/TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif` | `/mnt/d/TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif` | 7.51 MB |
| `data/TreeMap_Chaz/output2020/FL_5county_TreeMap_TMIDs.csv` | `/mnt/d/TreeMap_Chaz/output2020/FL_5county_TreeMap_TMIDs.csv` | 64 KB |
| `data/TreeMap_Chaz/output2020/clipped_TreeMap_2020.tif` | `/mnt/d/TreeMap_Chaz/output2020/clipped_TreeMap_2020.tif` | 7.85 MB |

The two 2020-vintage files are the *losing* candidate pairing — pulled only so
the script's auto-selection coverage comparison runs authentically (it correctly
picks `treemap2022`). Total download ≈ 18 MB. **None of this input data is
committed** — it lives under `/mnt/d` and gitignored `data/`.

## Exact command that produced the artifact

```bash
uv run python -m pipeline.s4_fvs.paint_fvs_to_raster
```

The painter writes `basal_area_yr0_initial.tif` and `basal_area_2076_final.tif`
to `data/processed/no_management_fl5co_rasters/`; those two GeoTIFFs were copied
into this folder unchanged.

## Dependencies / environment

The repo pins `requires-python = ">=3.14"`, but Python 3.14 could not be
installed in this sandbox (its build is fetched from GitHub, which egress policy
blocks). The painter uses only `pathlib`, `numpy`, `pandas`, and `rasterio` —
nothing 3.14-specific — so it was run in an isolated **Python 3.13** venv:

```bash
uv venv --python 3.13 .venv && source .venv/bin/activate
uv pip install numpy pandas rasterio matplotlib   # matplotlib only for map.png
```

Versions used: numpy 2.5.1, pandas 3.0.3, rasterio 1.5.0.
Unit tests (`tests/test_s4_paint_fvs_to_raster.py`) pass in this environment.

## How to regenerate

1. Fetch the five input files above from R2 into the paths shown (the three
   2022-pairing files are the minimum; the two 2020 files only affect the
   printed coverage comparison).
2. Create the environment (Python 3.14 via `uv sync` where available, or the
   3.13 fallback above).
3. Run `uv run python -m pipeline.s4_fvs.paint_fvs_to_raster`.
4. Copy the two GeoTIFFs from `data/processed/no_management_fl5co_rasters/`.
   `map.png` is a two-panel matplotlib render of those GeoTIFFs (shared 2–98
   percentile color scale, YlGn).
