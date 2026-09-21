# Southeast FVS × FIA × TreeMap ARTEMIS export package

Date: 2026-06-09

This is the top-level handoff package for using the scripts in `/home/chazm/projects/bahaa-scripts` with `/home/chazm/projects/artemis-model` and the FVS repos under `/home/chazm/projects/FVS`. The near-term goal is a tightly scoped, Florida-first ARTEMIS implementation that runs FVS with FIA and TreeMap, then expands across the Southeast.

## Executive summary

Use ARTEMIS as the orchestration repo and data-product boundary. Use the Bahaa scripts as source-specific preprocessing/reference workflows. Use the FVS repos as runtime and research infrastructure.

Recommended first production slice:

1. Florida only, 30 m, EPSG:5070, TreeMap 2022, FVS Southern (`SN`).
2. No-management growth-only trajectory library before any harvest/regime complexity.
3. Plot-level FVS input first: `TreeMap TM_ID -> PLT_CN -> FVS_STANDINIT_PLOT/FVS_TREEINIT_PLOT -> FVS outputs -> TM_ID -> pixels`.
4. Use the national FIA SQLite/FVS-ready tables directly for ARTEMIS. Do not require the PostgreSQL migration or cohort tables for the first FVS smoke test.
5. Preserve FIA IDs (`PLT_CN`, `CN`, `STAND_CN`, `StandID`) as strings/exact integers; never let them become floats.
6. Verify the matched FVS-ready stand/tree tables come from the same source. Do not mix raw FIA `TREE` tables with FVS `STANDINIT` rows unless the join has been proven.

## Repos and branches reviewed

I inspected local working trees and local remote refs without checking out branches, because several repos have uncommitted work or a stash. Branch notes below are therefore a snapshot of the refs already present locally.

### `bahaa-scripts` folders

| Folder | Branches inspected | Role in ARTEMIS | Recommendation |
|---|---|---|---|
| `FIASQLITE2PGSQL` | `origin/master`, `origin/duckdb` | Converts the 66 GB FIA SQLite database into a local PostgreSQL database, either through shell/CSV migration (`master`) or SQLite → DuckDB → PostgreSQL (`duckdb`). | Useful for SQL-heavy cohort/QA work, but not required for the first ARTEMIS FVS pipeline because `config/data_paths.yaml` points at FIA SQLite tables that already include FVS-ready inputs. |
| `FIA_DATA_PREP` | `origin/main`, `origin/polars` | Builds longitudinal FIA subplot/cohort tables with EPA ecoregions, LANDFIRE fire zones, age calculations, disturbance flags, and species grouping. | Use as a validation/cohort-prep reference after the plot-level FVS smoke works. The `polars` branch is the better future port target for diagnostics and LANDIS/Pan-style cohorts. |
| `PRISM_LT_EPA` | `origin/master` | Downloads/processes PRISM long-term climate rasters and aggregates by EPA L3/L4 ecoregion into SQLite. | Useful for ecoregion climate summaries and FIA cohort covariates. For ARTEMIS pixels, prefer GEE PRISM normals already named in `artemis-model/config/data_paths.yaml`. Local `prism_lt_epa.db` is only a Git LFS pointer unless `git lfs pull` has materialized the 1.9 GB file. |

### FVS repos

| Repo | Branches inspected | Role in ARTEMIS | Recommendation |
|---|---|---|---|
| `FVS/fvs2py` | `origin/main` | Python `ctypes` wrapper around official FVS shared libraries; Docker/dev-container path uses `ghcr.io/vibrant-planet-open-science/usfs-fvs:FS2026.1`. | Best first runtime wrapper for ARTEMIS smoke tests. Implement keyfile/input DB/output parsing in ARTEMIS, not in `fvs2py`. |
| `FVS/fvs-modern` | many active branches, see branch inventory below | Modernized FVS fork with local `lib/FVSsn.so`, calibration pipeline, CONUS stress harnesses, gompit mortality, and TreeMap pilot evidence. | Use as a research/runtime reference and possible local library source. Local `FVSsn.so`, `FVSne.so`, and `FVSpn.so` loaded successfully via `ctypes` on 2026-06-09, but keep official/FVS Docker as the reproducibility baseline until ARTEMIS has its own smoke logs. |
| `FVS/microfvs` | `origin/main`, `origin/regimpute` | FastAPI service/keyfile builder around FVS, with optional REGIMPUTE regeneration keyword components on `regimpute`. | Not needed for the first no-management run. Revisit when adding regeneration or if an HTTP FVS service is useful. |

### ARTEMIS repo facts to respect

`/home/chazm/projects/artemis-model` is already scoped for Florida first:

- `README.md` and `PLAN.md`: Florida v1, TreeMap 2022 + FIA, FVS Southern (`SN`), 50 years, 5-year cycles.
- `config/projection.yaml`: `base_year: 2022`, `horizon_years: 50`, `cycle_years: 5`, `n_cycles: 10`, `default_variant: SN`.
- `config/data_paths.yaml`: local paths for TreeMap 2022, FIA SQLite, ownership, LANDFIRE, NHD, parcels, roads, and GEE-only PRISM/POLARIS/3DEP/LCMS.
- `pipeline/s4_fvs/` is only a stub; this is where the first ARTEMIS FVS modules should go.
- Existing notes flag unresolved inconsistencies: TreeMap 2020 vs 2022 artifacts in older R work, plot-vs-condition FVS choice, missing/pyc-only management-unit source in the repo, and a `versions.lock` reference that may need to be created/verified before publication.

### Local data checks from this pass

These files were present on 2026-06-09:

- `/mnt/d/TreeMap-2022/Data/TreeMap2022_CONUS.tif` (~4.6 GB)
- `/mnt/d/TreeMap-2022/Data/TreeMap2022_CONUS_Tree_Table.csv` (~228 MB)
- `/mnt/d/SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db` (~66 GB)
- `/mnt/d/RDS-2025-0045/Data/US_forest_ownership.tif` (~3.7 GB)

These were not locally materialized:

- `/mnt/d/PRISM_LT_EPA/prism_lt_epa.db` was missing.
- `/home/chazm/projects/bahaa-scripts/PRISM_LT_EPA/prism_lt_epa.db` is a 135-byte Git LFS pointer to a ~1.9 GB SQLite file, not the database itself.

## Branch inventory and relevance

### `FIASQLITE2PGSQL`

| Branch | Status | Key content | ARTEMIS relevance |
|---|---|---|---|
| `origin/master` | default | Shell pipeline: dump SQLite schema/data, translate schema text, start local PostgreSQL, `\COPY` CSVs, apply constraints. Keeps `CN` as `VARCHAR(34)` and widens some point-count fields. | Stable reference for a PostgreSQL FIADB mirror if cohort SQL needs it. |
| `origin/duckdb` | diverged | Replaces most shell dump/copy pieces with `sqlite2duckdb2pgsql.py`, which loads SQLite through DuckDB, applies type overrides, creates PostgreSQL schema, and inserts via DuckDB's postgres extension. | Better future migration path because it is more inspectable and handles type overrides in Python. Still optional for first ARTEMIS FVS. |

### `FIA_DATA_PREP`

| Branch | Status | Key content | ARTEMIS relevance |
|---|---|---|---|
| `origin/main` | default | Numbered SQL/Python pipeline: plot-to-EPA, plot-to-fire-zone, plot ECO table, longitudinal subplots, disturbances, seedlings/trees, age propagation, species/ecoregion maps, final `DATA_ECO_COHORTS`. | Useful for FIA-derived cohort validation, ecoregion grouping, and disturbance/harvest history signals. Not the first FVS input path. |
| `origin/polars` | ahead of default | Adds `fia_curation_v2.py`, `age_imputation.py`, `cohort_tracking.py`, `cohorts_landis2.py`; moves tree-level longitudinal logic into DuckDB + Polars with diagnostics, model-based age imputation, stable birth-year cohort IDs, and LANDIS/Pan export shape. | Highest-value branch if ARTEMIS later needs cohort models, FIA remeasurement validation tables, or non-FVS cohort exports. |

### `PRISM_LT_EPA`

| Branch | Status | Key content | ARTEMIS relevance |
|---|---|---|---|
| `origin/master` | default | `run.sh` downloads EPA L4 polygons, optionally downloads very large PRISM_LT data, zonal-stats monthly/yearly PRISM rasters by EPA polygons, coalesces parquet to SQLite, aggregates L3/L4, vacuums DB. | Keep as an EPA-scale climate summary reference. For pixel-level ARTEMIS, use GEE PRISM normals to avoid a 559 GB local PRISM_LT dependency. |

### `FVS/fvs-modern`

| Branch | Status vs `origin/main` | Key content | ARTEMIS relevance |
|---|---|---|---|
| `origin/main` | default | Modernized free-form Fortran libraries, calibration pipeline, local `lib/FVS*.so`, CONUS stress files, and `calibration/python/conus_100yr_projection.py`. | Candidate local runtime/reference. Local `FVSsn.so` loads. |
| `origin/feature/conus-mortality-clean` | merged | CONUS gompit mortality and full-FIADB stress harness. | Already reflected in main; useful for later mortality sensitivity. |
| `origin/fix/issue-54-keyword-multipliers-5comp` | merged | Five-component calibration multiplier fix. | Already reflected in main; matters if using calibrated `fvs-modern` configs. |
| `origin/feature/gompit-projection-wiring` | ahead/diverged | In-engine gompit mortality integration, validation docs, Maine TreeMap pilot. Shows TreeMap-spatial expansion vs FIADB uniform expansion and confirms raster-join scaling pattern. | Valuable later scenario branch; do not use gompit for baseline Florida until default growth-only run validates. |
| `origin/feature/conus-mortality-gompit` | diverged | Handoff docs for CONUS projection and corrected FVS TreeInit source. Important warning: FVS `STANDINIT` must join to FVS-native `FVS_TREEINIT_*`, not raw DataMart `TREE` CSVs. | High-value gotcha for scaling and HPC manifests. |
| `origin/feature/conus-projection` | diverged | `conus_100yr_projection.py`: SLURM-friendly no-harvest default vs calibrated projection by variant. | Reuse concepts for batching and output rows, but ARTEMIS needs 50-year/5-year Florida pixel linkage, not 100-year CONUS aggregate first. |
| `origin/conus-variant` | diverged | Design docs for a unified FVS-CONUS variant with national equations and climate/site covariates. | Research direction, not initial ARTEMIS implementation. |
| `origin/conus-sf-integration-2026-05-21` | diverged | Species-free/trait-driven growth/height model experiments. | Research only for later model comparisons. |
| `origin/calib/dg-exact-refit` | diverged | Exact diameter-growth species mapping for adopted variants. | Calibration research; later only. |
| `origin/fix/issue-54-calibration-multipliers` | diverged | Five-component calibrated config fix, PR-base complications. | Check only if using that old PR path; main already has the newer merged state. |
| `origin/acd-bridge-followup-2026-05-20` | diverged | Acadian/MAGPlot/FVS bridge and database keyfile route. | Low relevance to Florida except as a DB-keyfile example. |
| `origin/feature/silc-v10-mortcal-yr100` | diverged | SILC overstory benchmark and reports. | Low direct relevance. |
| `origin/manuscript-*`, `origin/v3-scope-*` | diverged | Manuscript/scoping/eval skeletons. | Low direct implementation relevance. |
| `origin/upstream-sync/20260601`, `origin/upstream-sync/20260608` | diverged/ahead | Upstream report updates. | Maintenance context only. |

### `FVS/microfvs`

| Branch | Status | Key content | ARTEMIS relevance |
|---|---|---|---|
| `origin/main` | default | FastAPI wrapper to run FVS from JSON/keyfile templates inside Docker. | Optional service interface later. |
| `origin/regimpute` | diverged | Adds REGIMPUTE regeneration KCPs, enums/models, and template placeholder. | Revisit after growth-only and harvest/regime phases; do not start here. |

## How the Bahaa scripts fit into ARTEMIS

```text
ARTEMIS
  pipeline/s1_initial_state
    TreeMap 2022 raster + tree table -> tm_id/plt_cn/pixels table

  pipeline/s4_fvs
    FIA SQLite FVS-ready tables -> filtered FVS input DB
    keyword builder -> FVS keyfiles (10 cycles x 5 years)
    fvs2py or fvs-modern lib -> FVSOut.db
    output parser -> trajectory_library.parquet
    linkage/paint -> raster/Zarr outputs

Bahaa scripts
  FIASQLITE2PGSQL
    optional FIADB PostgreSQL mirror for SQL-heavy exploration/cohort prep

  FIA_DATA_PREP
    optional/secondary FIA cohort, disturbance, age, ecoregion/fire-zone tables
    validation and non-FVS cohort products

  PRISM_LT_EPA
    optional EPA ecoregion climate summaries; pixel PRISM should come through GEE

FVS repos
  fvs2py
    first Python runtime wrapper around FVS shared libs / official Docker image

  fvs-modern
    modernized FVS libraries, calibration/gompit experiments, CONUS/HPC harness patterns

  microfvs
    optional REST/keyfile/regeneration service layer
```

## Florida-first implementation plan

### Milestone 0 — settle inputs and runtime contract

**Goal:** make the first run reproducible and small.

Decisions:

- Projected extent: Florida only (`STATECD/FIPS = 12`).
- FVS variant: `SN` for every Florida pixel for v1.
- Runtime: use `fvs2py` Docker/official FVS as baseline; allow local `fvs-modern/lib/FVSsn.so` only after recording library path and smoke output.
- Grain: plot-level first (`FVS_STANDINIT_PLOT`, `FVS_TREEINIT_PLOT`, `FVS_PLOTINIT_PLOT`). Keep condition-level tables for later comparison.
- Regime: `no_management` only.
- Cycles: `NumCycle 10`, `TimeInt 0 5`.
- Outputs: `FVS_Cases`, `FVS_Summary2`, `FVS_Carbon`, `FVS_Error`.

Acceptance checks:

- `versions.lock` or equivalent records TreeMap, FIA DB, FVS tag/lib, and random seed.
- Local paths in `config/data_paths.yaml` exist or are flagged.
- A documented choice exists for handling FIA `INV_YEAR` vs TreeMap base year 2022.

### Milestone 1 — build Florida `TM_ID -> PLT_CN -> pixels`

Implement in `artemis-model/pipeline/s1_initial_state/build_treemap_tmids.py`.

Inputs:

- `/mnt/d/TreeMap-2022/Data/TreeMap2022_CONUS.tif`
- `/mnt/d/TreeMap-2022/Data/TreeMap2022_CONUS_Tree_Table.csv`
- Florida extent from `config/extent.geojson`

Outputs:

- `data/interim/treemap_tmids_fl.parquet`
- `data/interim/treemap_pixel_counts_fl.parquet`

Rules:

- Count raw integer raster values; do not rely on displayed VAT categories.
- Read `PLT_CN` as string from TreeMap tree table.
- Assert one `TM_ID` row has one `PLT_CN`, unless TreeMap documentation proves otherwise.
- Record pixel count, acres, forest type, BA, TPA, carbon fields available from TreeMap.

Acceptance checks:

- `tm_id` unique.
- `plt_cn` matches `^\d+$` and is string/object, not float.
- Pixel counts are positive.
- Row counts and Florida forest area are logged.

### Milestone 2 — build filtered FVS input SQLite DB

Implement in `artemis-model/pipeline/s4_fvs/input_db.py`.

Inputs:

- FIA CONUS SQLite from `config/data_paths.yaml`.
- `treemap_tmids_fl.parquet`.

Output:

- `data/interim/fvs/fia_fvs_fl.db`

Initial table strategy:

| Table class | Filter rule |
|---|---|
| `PLOT` | selected `PLOT.CN / PLT_CN` values |
| Standard plot-linked FIA tables | `PLT_CN` in selected donor plots, only if needed for QA |
| FVS plot tables | `STAND_CN` in selected plot IDs, after verifying key equivalence |
| FVS condition tables | keep filtered by matching `COND.CN` for later, but not used first |
| Reference/group tables | copy full or minimally required references |

Important gate:

- Verify `FVS_STANDINIT_PLOT` and `FVS_TREEINIT_PLOT` join each other on the same key in the local SQLite. The `fvs-modern` CONUS CSV work found that raw DataMart `TREE.csv` did **not** join to FVS `STANDINIT`; the fix was to use FVS-native `FVS_TREEINIT_PLOT`. ARTEMIS should only use matched FVS-ready tables from the same FIA source.

Acceptance checks:

- Count requested stands, matched standinit rows, matched treeinit rows.
- No tree rows with missing standinit.
- `VARIANT` is `SN` or is explicitly overridden to `SN` for Florida projection.
- Any zero/null `INV_PLOT_SIZE` values are logged; do not apply the older `0.041800` patch unless a smoke test proves it is required.

### Milestone 3 — keyfile builder

Implement in `artemis-model/pipeline/s4_fvs/keyword_builder.py`.

Output one keyfile per stand for the first smoke path:

```text
StandCN
<STAND_CN>
Screen
NumCycle 10
TimeInt 0 5

DataBase
DSNOUT
/path/to/FVSOut.db
Summary 2
CarbReDB 2
End

DataBase
DSNIN
/path/to/fia_fvs_fl.db
StandSQL
SELECT * FROM FVS_STANDINIT_PLOT WHERE Stand_CN = '%Stand_CN%'
EndSQL
TreeSQL
SELECT * FROM FVS_TREEINIT_PLOT WHERE Stand_CN = '%Stand_CN%'
EndSQL
End

FMin
CarbRept
CarbCut
End

Process
Stop
```

Acceptance checks:

- Unit test confirms `NumCycle 10`, `TimeInt 0 5`, `DSNIN`, `DSNOUT`, `Summary 2`, `CarbReDB`, `Process`, and `Stop` are present.
- Keyfile path and hash are recorded per run.

### Milestone 4 — run 10-stand smoke test

Implement in `artemis-model/pipeline/s4_fvs/run_fvs_smoke.py`.

Run:

```bash
uv run python -m pipeline.s4_fvs.run_fvs_smoke \
  --input-db data/interim/fvs/fia_fvs_fl.db \
  --limit-stands 10 \
  --variant SN \
  --out-dir data/interim/fvs/smoke_no_management
```

Acceptance checks:

- 10 requested stands produce 10 output DBs or an explicit failure ledger.
- `FVS_Cases` exists and reports `Variant = SN`.
- `FVS_Summary2` has expected cycle years.
- `FVS_Carbon` exists if carbon keywords are enabled.
- `FVS_Error` is parsed and warning counts are summarized.
- Year-0/first-cycle BA and TPA are compared to TreeMap `BALIVE`/`TPA_LIVE`.

### Milestone 5 — parse trajectories and paint one raster

Implement:

- `pipeline/s4_fvs/output_parser.py`
- `pipeline/s4_fvs/build_linkage.py`
- `pipeline/s4_fvs/paint.py`

Outputs:

- `data/interim/fvs/trajectory_library.parquet`
- `data/interim/fvs/fvs_errors.parquet`
- `data/interim/fvs/treemap_fvs_linkage.parquet`
- one test raster, e.g. `data/interim/fvs/paint_ba_2027.tif`

Acceptance checks:

- Every trajectory row joins to `TM_ID` through the linkage table.
- One painted raster aligns to the TreeMap grid and has non-null values only where expected.
- County/forest-type summaries are produced for QA.

### Milestone 6 — scale to all Florida unique `TM_ID`s

Only after the smoke passes:

- Batch all Florida unique `TM_ID`s/stands.
- Keep no-management baseline separate from later management scenarios.
- Use process-level parallelism; one FVS instance per process is safer than a long-lived shared instance.
- Persist ledgers and warning summaries.

Acceptance checks:

- Failure rate and warning classes reported.
- Aggregate year-0 comparisons against TreeMap and FIA/EVALIDator are documented.
- Runtime/memory profile is recorded.

### Milestone 7 — add management/ownership and Southeast expansion

After no-management Florida is validated:

1. Add ownership/riparian/management-unit/regime assignment.
2. Calibrate harvest with LCMS Tree Removal and ownership classes.
3. Add state-specific BMP rules before each new state.
4. Add explicit state/variant mapping rather than assuming one variant for the whole Southeast.
5. Expand in batches:
   - Phase A: Florida only.
   - Phase B: neighboring core Southeast states (suggested: GA, AL, SC, NC) after variant/BMP rules are verified.
   - Phase C: broader Southeast (suggested candidates: MS, LA, TN, AR, VA/KY depending final scope).

Expansion acceptance checks per state:

- State extent and TreeMap clip validated.
- Projected FVS variant(s) documented.
- BMP rules documented.
- TreeMap `TM_ID -> PLT_CN` join rate logged.
- FVS stand/tree match rate logged.
- 10-stand smoke passes before statewide batch.

## Open decisions before coding Milestone 1

1. **Base-year alignment:** do we grow FIA plots from their `INV_YEAR` to 2022 before the 50-year ARTEMIS projection, or document `INV_YEAR` as the prototype start? This is publication-critical.
2. **Runtime baseline:** official FVS Docker via `fvs2py`, local `fvs-modern/lib/FVSsn.so`, or both in A/B smoke?
3. **TreeMap source of truth:** enforce TreeMap 2022 only and quarantine older `/mnt/d/TreeMap_Chaz` TreeMap 2020 artifacts.
4. **Variant assignment for expansion:** build a state/variant map now or defer until Florida passes?
5. **Carbon pools:** FVS `FVS_Carbon` does not supply every pool ARTEMIS names (especially soil organic carbon); decide separate SOC source before claiming all five IPCC pools.

## Working-tree cautions observed

- `bahaa-scripts` itself is not a git repo; its subfolders are separate repos.
- `FIASQLITE2PGSQL` is on `master`, clean, with `stash@{0}: local documentation changes before remote sync` still present.
- `FIA_DATA_PREP` has an untracked `README.md` in the local working tree.
- `PRISM_LT_EPA` has a file-mode-only local modification on `download_prism.sh`.
- `artemis-model` has multiple local modifications/untracked data/notes/notebooks; do not assume a clean branch.
- `FVS/fvs2py` has local modifications to `.gitignore`, an example notebook, `pyproject.toml`, and `uv.lock`.

