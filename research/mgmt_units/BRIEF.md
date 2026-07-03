# Research Brief — Management-Unit Delineation for Regional FVS Simulation

**Shared context for the worker / critic / reviewer loop. Read this fully before acting.**

## 0. Operating environment (verified by orchestrator)

- **Working dir (cwd for all agents):** `/home/chazm/projects/artemis-model/.claude/worktrees/mgmt-units-research`
  This is a git worktree on branch `worktree-mgmt-units-research`. Do all work here. Write new code under `research/mgmt_units/` and (for production helpers) `pipeline/s3_management/`.
- **Python:** `uv run python ...`. Env is synced and verified: `geopandas 1.1.3`, `shapely 2.1.2`, `rasterio 1.5.0`, `pandas`, `pyogrio`, `xarray`. Use `pyogrio` (NOT `fiona`) for layer listing: `from pyogrio import list_layers`.
- **Data:** `data/` is symlinked so `data/raw -> /mnt/d` (external drive, mounted & verified). Treat `data/raw` as canonical. Persisted pilot outputs live under `data/interim/`.
- Run heavy/iterative experiment code under `research/mgmt_units/`. Persist all derived artifacts (GeoPackages, parquet, CSV, PNG figures) under `research/mgmt_units/outputs/` (gitignored-friendly; keep small CSV/PNG summaries, not giant rasters, in the repo).

## 1. Project context (ARTEMIS)

ARTEMIS is a deterministic, pixel-level (30 m) forward projection of SE-US forest stand
dynamics using **FVS Southern (SN) variant**, initialized from **TreeMap 2022 + FIA tree
lists**, management calibrated from **LCMS**. v1 extent: Florida (FIPS 12). CRS **EPSG:5070**,
50-yr horizon, 5-yr cycles. See repo `README.md`, `PLAN.md`, `notes/management_units.md`.

**Why management units matter:** FVS simulates *stands*, not pixels. To run FVS across a
region you must partition forested land into operationally meaningful **management units**
(roughly: timber stands) that share an initial state and a management regime. The quality,
size, and shape of these units directly controls (a) how many FVS runs are needed, (b) how
realistic the simulated silviculture is, and (c) the spatial resolution of outputs.

## 2. The verified key finding (your starting point — do NOT re-derive from scratch)

A pilot already ran the **naive boundary-intersection** approach for **Union County, FL**:
`parcels ∩ LANDFIRE-forest`, then erase (Florida BMP stream buffers + NHD waterbodies +
small road artifact buffer). Output: `data/interim/management_units_smoke_union/12125_union/candidate_management_units.gpkg`
(17,020 polygons, EPSG:5070, attributes incl. `unit_id, PARCELID, DORUC, ACRES, unit_area_ha, size_class`).

Size distribution (the headline phenomenon):

| size_class | polygons | area (ha) | median (ha) |
|---|---|---|---|
| candidate (2–40 ha) | 2,077 | 18,124 | 5.7 |
| large (>40 ha) | 91 | 5,728 | 55.8 |
| **sliver (<2 ha)** | **14,852** | 3,764 | **0.09** |

**The sliver explosion** — 87% of polygons are <2 ha fragments holding only 14% of the
forest area — is the core problem. Naive intersection shatters forest into operationally
meaningless slivers along every parcel/road/stream boundary. `PLAN.md §3a` already flags
**raster segmentation** (skimage `felzenszwalb`/`slic`, or GRASS `i.segment`) as the
intended "better" path.

## 3. Research question

> **How should operationally-realistic forest management units be delineated across a large
> region from open data, and how do unit area / shape / count distributions — and the
> downstream FVS workload and output resolution — vary across delineation strategies?**

This is a legitimate methods paper if executed with rigor. The contribution is a
**reproducible, open-data management-unit delineation method for regional process-based
forest simulation, with a quantitative comparison of strategies and their FVS implications.**

## 4. Available inputs (all verified accessible)

- Parcels (5-county pilot): `data/raw/FL_5_Co_Parcels.gdb` layer `FL_5_Co_Parcels` (99,914 feat, EPSG:26917). Statewide: `data/raw/All_FL_Parcels`.
- Roads: `data/raw/SE_rds100k/SE_rds100k.gdb` layer `SE_rds100k` (EPSG:4326; fields MTFCC, RTTYP).
- Streams: `.../nhdplus_epasnapshot2022_fl.gdb` layer `nhdflowline_fl` (EPSG:4269; field `fcode`).
- Waterbodies: `.../US SE Streams.gdb` layer `NHDWaterbody_DissolveBoundaries1`.
- LANDFIRE EVT 30 m: `data/raw/LF2022_EVT_CONUS/.../LF2022_EVT_CONUS.tif` (EPSG:5070; VAT has EVT_LF, EVT_ORDER, EVT_NAME).
- TreeMap 2022: `data/raw/TreeMap-2022/Data/TreeMap2022_CONUS.tif` + tree table CSV. Plot-ID raster → FIA linkage.
- FIA SQLite: `data/raw/SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db` (FVS_STANDINIT_PLOT = 1.88M rows; FVS-ready tables present). Gives plot age, forest type, site index.
- Ownership 30 m: `data/raw/RDS-2025-0045/Data/US_forest_ownership.tif` (9-class; see `config/projection.yaml`).
- Terrain DEM: `data/raw/USGS-13-arcs-DEM` (for terrain-break boundaries).
- Ecoregions: `data/raw/us_eco_l3`, `us_eco_l4`.
- BMP rules: `config/bmp_rules.yaml` (FL FFS 2020 buffer widths by FCode class).
- Config: `config/projection.yaml`, `config/bmp_rules.yaml`.

## 5. Known repo issues to fix as part of closing the loop

1. `pipeline/s3_management/sketch_management_units.py` was **deleted** (only a `.pyc` remains
   at `/home/chazm/projects/artemis-model/pipeline/s3_management/__pycache__/`). `tests/test_s3_sketch_management_units.py`
   imports it and currently **fails at collection**. The test pins these function signatures:
   `classify_stream_fcode, classify_unit_size, clean_geometries, feet_to_meters,
   split_large_geometry, target_grid_cell_size_m`. `notes/management_units.md §"Statewide
   script implementation"` describes its behavior. **Reconstruct it** so the test passes —
   this is the reproducible engine for the "naive" strategy.
2. `uv run pytest` also errors collecting the vendored `FVS/` subtree (needs `pydantic`).
   Scope your test runs to `tests/` (e.g. `uv run pytest tests/ -q`) so FVS vendoring noise
   doesn't block you.

## 6. Reviewer rubric — definition of "paper-worthy"

The reviewer gates the loop. The idea is paper-ready only when ALL of these hold:

1. **Question & contribution** are crisp, novel-enough, and situated against prior work
   (forest stand delineation / segmentation; operational unit creation; FVS at scale).
2. **Reproducible methods**: code runs end-to-end from `data/raw` with one documented command;
   parameters and seeds fixed; outputs regenerable.
3. **≥2 delineation strategies** quantitatively compared (at minimum: naive boundary-
   intersection vs. raster segmentation; ideally a hybrid/constrained variant). Compared on
   area distribution, **shape compactness** (e.g. Polsby-Popper or perimeter-area ratio),
   polygon count, **sliver fraction**, and forest-area retention.
4. **A defensible reference / validation or sensitivity axis**: e.g. compare unit-size
   distribution against an external expectation (FIA condition sizes, known SE stand sizes,
   or hand-digitized reference), OR a parameter-sensitivity analysis, OR a demonstrated
   downstream FVS-workload / output-resolution implication (e.g. # unique (unit × regime)
   FVS runs and how it changes by strategy).
5. **Honest limitations** and a clear path to statewide / regional scale.
6. **Figures + tables** that a reviewer could drop into a manuscript.
7. **Target venue** named with justification.

The reviewer must return either `VERDICT: PAPER-READY` or `VERDICT: NOT-READY` with a short,
specific, prioritized gap list that the worker can act on.

## 7. Constraints / guardrails

- Statistically sound, spatially high-resolution, data-rooted. No invented numbers, no fake
  citations. Mark anything unverified as `needs verification`.
- Surgical: don't refactor unrelated pipeline code. Keep new code under `research/mgmt_units/`
  and the one reconstructed `s3_management` script.
- Keep runs tractable: start with the 5-county pilot AOI (or a single county) — do NOT attempt
  statewide All_FL_Parcels in a tight loop. Document how it scales.
- Every claim in the writeup must trace to a number your code produced and saved.
