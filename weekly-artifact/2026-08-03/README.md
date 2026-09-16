# Weekly artifact — 2026-08-03

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
| `map.png` | Two-panel render of the two GeoTIFFs (shared 2–98 pct YlGn color scale) for quick viewing. Rendered from the GeoTIFFs above — no data was hand-authored. |

**Why this artifact:** `README.md` / `PLAN.md` frame ARTEMIS as spatially
explicit forest projection; the painted FVS raster is the single most important
concrete output the committed code produces. The top-level `Artemis` entry point
(`main.py`) still raises `NotImplementedError`, and the management-unit sketcher
(`pipeline/s3_management/`) is pilot/QA stage, so the painter remains the flagship.
It also has the cleanest reproducibility story: deterministic, fully tested
(`tests/test_s4_paint_fvs_to_raster.py`, 4 passed), and self-selecting on data
vintage.

**Result summary (matches `notes/fvs-to-raster-painting.md`):**
- FVS trajectory: 9,259 rows, 693 stands, calendar years 1997–2076.
- Selected pairing `treemap2022`: 693/693 stands covered (100%),
  5,413,921/5,414,572 raster pixels matched (100%). The losing `treemap2020`
  pairing covers 679/693 stands (98.0%), so the script correctly self-selects 2022.
- Painted 5,413,921 pixels (100% of valid). Mean basal area rises 83.2 → 188.9
  sq ft/ac from year 0 to 2076 — the expected no-management accumulation signal.

## R2 inputs pulled

Only the minimal files the painter reads were downloaded from the Cloudflare R2
bucket `r2:artemis-r2` (bucket `data/` maps to the repo's `/mnt/d`), via rclone:

| R2 key | Downloaded to | Size |
|---|---|---|
| `data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv` | `data/interim/no_management_fl5co_fvs_output/fvs_trajectory.csv` | 2.9 MB |
| `data/TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv` | `/mnt/d/TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv` | 64 KB |
| `data/TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif` | `/mnt/d/TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif` | 7.2 MB |
| `data/TreeMap_Chaz/output2020/FL_5county_TreeMap_TMIDs.csv` | `/mnt/d/TreeMap_Chaz/output2020/FL_5county_TreeMap_TMIDs.csv` | 64 KB |
| `data/TreeMap_Chaz/output2020/clipped_TreeMap_2020.tif` | `/mnt/d/TreeMap_Chaz/output2020/clipped_TreeMap_2020.tif` | 7.5 MB |

The two 2020-vintage files are the *losing* candidate pairing — pulled only so
the script's auto-selection coverage comparison runs authentically (it correctly
picks `treemap2022`). Total download ≈ 18 MB. **None of this input data is
committed** — it lives under `/mnt/d` and gitignored `data/`.

## Exact command that produced the artifact

```bash
.venv/bin/python -m pipeline.s4_fvs.paint_fvs_to_raster
```

The painter writes `basal_area_yr0_initial.tif` and `basal_area_2076_final.tif`
to `data/processed/no_management_fl5co_rasters/`; those two GeoTIFFs were copied
into this folder unchanged.

## Dependencies / environment

Ran in the repository's committed `.venv` (Python 3.14, provisioned by an earlier
`uv sync`). The painter uses only `pathlib`, `numpy`, `pandas`, and `rasterio`;
`matplotlib` renders `map.png`. Versions in this environment: numpy 2.4.6,
pandas 3.0.3, rasterio 1.5.0, matplotlib 3.10.9. Unit tests
(`tests/test_s4_paint_fvs_to_raster.py`) pass here (4 passed).

## How to regenerate

1. Fetch the five input files above from R2 into the paths shown (the three
   2022-pairing files are the minimum; the two 2020 files only affect the
   printed coverage comparison).
2. Create the environment (`uv sync`, Python 3.14).
3. Run `.venv/bin/python -m pipeline.s4_fvs.paint_fvs_to_raster` (or
   `uv run python -m pipeline.s4_fvs.paint_fvs_to_raster`).
4. Copy the two GeoTIFFs from `data/processed/no_management_fl5co_rasters/`.
   `map.png` is a two-panel matplotlib render of those GeoTIFFs (shared 2–98
   percentile color scale, YlGn).
