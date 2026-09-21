# scripts/ notes

Notes and export packages covering the `scripts/` folder of `artemis-model`.
The `scripts/` folder is **not** the production pipeline (that lives in
`pipeline/s1…s6/`). It is a collection of reference / preprocessing workflows
(largely from the "Bahaa-scripts" set) plus one ArcGIS-driven prototype (the
LETO workflow) plus repo plumbing. The plan per the handoff doc below is to use
these as references while `pipeline/` is rebuilt as reproducible Python.

## Index

- [Southeast FVS × FIA × TreeMap ARTEMIS export package](southeast-fvs-artemis-export-package.md) — cross-repo branch inventory, role of each script folder, and a Florida-first implementation plan for scaling FVS across the Southeast.

## Folder layout

```
scripts/
├── LETO.V1.1.txt              # ArcPy: stand/MU delineation (Voronoi) + PLT_CN/ownership/SMZ
├── LETO_CSV_PIPELINE.txt      # ArcPy: build FVS_StandInit / FVS_TreeInit CSVs from MUs + FIA TREE
├── Create_FVS_Database.txt    # Python: load CSVs into a blank FVS SQLite .db
├── Join_FVS_output_to_arc.txt # ArcPy: join FVS_Summary2 back to MUs per year
├── README.txt                 # LETO run order (human-in-the-loop, ArcGIS GUI)
├── Voronoi_TreeMap/           # earlier geometric-only version of LETO stand delineation
├── FIASQLITE2PGSQL/           # shell: migrate 66 GB FIA SQLite → local PostgreSQL
├── FIA_DATA_PREP/             # numbered SQL/Python: longitudinal FIA cohort tables
├── PRISM_LT_EPA/              # PRISM climate rasters aggregated by EPA ecoregion → SQLite
├── check-staged-large-files.sh # pre-commit hook: block staged blobs > 99 MiB
└── notes/                     # this folder
```

## Three groupings

1. **LETO workflow** — a complete manual/ArcGIS prototype of the growth pipeline
   for the Florida 5-county study area.
2. **Source-specific preprocessing** — FIA, PRISM climate, ecoregions.
3. **Repo infrastructure** — git hook + handoff notes.

---

## 1. LETO workflow (the `.txt` files + `README.txt`)

ArcGIS Pro (`arcpy`) Python scripts saved as `.txt`. `README.txt` documents the
run order. "LETO" is the case-study name. Together they implement the full
ARTEMIS growth chain — `s1` (initial state) + `s3` (management) + `s4` (FVS) +
`s6` (outputs) — as a one-off, GUI-driven, ArcGIS-based prototype.

Run order (from `README.txt`):

1. `LETO.V1.1` → 2. `LETO_CSV_PIPELINE` → 3. `Create_FVS_Database` →
   (run the DB in the FVS GUI) → 4. `Join_FVS_output_to_arc`.

### `LETO.V1.1.txt` — stand / Management Unit delineation (Voronoi)
Inputs: TreeMap 2022 raster, RDS-2025-0045 forest-ownership raster, parcel
polygons (clip layer), stream lines — all inside an ArcGIS `.gdb`.
- Builds the forested raster domain, clips to parcels.
- **Iteratively subdivides** any polygon > `MAX_ACRES` (200 ac) using random
  points + Thiessen (Voronoi) polygons, targeting ~100 acres/point, until every
  MU ≤ 200 acres. Cleans up: multipart→singlepart, drops polygons <5 acres,
  re-clips to parcels.
- Assigns `MU_ID` (permanent stand ID carried through the rest of the chain).
- **`assign_plt_cn`** is the heart of the ARTEMIS concept: rasterizes MUs onto
  the TreeMap grid, computes per-cell `MU_ID × TreeMap value`, and builds a
  **weighted MU × PLT_CN table** (cell-count weights). TreeMap raster values
  encode FIA plot CNs, so this is the bridge between pixel space and FIA plot
  space. Writes majority PLT_CN + TreeMap value onto each polygon.
- Assigns majority ownership code/type via zonal statistics on the ownership
  raster.
- Computes **Stream Management Zone (SMZ) percentage** via vector buffer +
  intersect (riparian BMP input).
- Exports `ManagementUnits_Final`.

This is essentially `pipeline/s3_management` (stand segmentation, stream
buffers, ownership) done in ArcGIS.

### `LETO_CSV_PIPELINE.txt` — FVS input CSV builder
- Reads the MU polygons + the weighted `MU_PLT_CN_Weights.csv` from step 1.
- Reads FIA `TREE.csv` from **multiple states** (FL, GA, AL, SC) — because
  TreeMap borrows plots across state lines, so neighbors' tree data is needed.
- Reads the FVS Species Crosswalk (`FIA CODE → SN_Mapped_To`) to translate FIA
  species → FVS SN species.
- Filters to plots contributing ≥5% (configurable `MIN_PLT_WEIGHT`), renormalizes
  weights.
- Joins weights → FIA trees, keeps live trees (`STATUSCD == 1`), and **expands
  `TREE_COUNT` (TPA) by the plot weight**.
- **Imputes missing tree lists** using ArcGIS `GenerateNearTable`: finds the
  nearest "runnable" MU and copies its tree list so every stand has trees to grow.
- Writes `FVS_StandInit.csv` and `FVS_TreeInit.csv` (variant `SN`, state `FL`,
  inv year 2022).

This is `pipeline/s1_initial_state` (FIA join, FVS-ready tree lists) as a one-off.

### `Create_FVS_Database.txt` — FVS SQLite DB builder
- Copies a blank FVS template `.db`, loads the two CSVs into `FVS_StandInit` /
  `FVS_TreeInit`.
- **Critically sets the FVS sampling design**: `BASAL_AREA_FACTOR = -1` (tells
  FVS that `TREE_COUNT` is already trees-per-acre, so don't re-expand),
  `INV_PLOT_SIZE=1`, `NUM_PLOTS=1`. The comments warn that getting this wrong
  "produces greatly inflated stand volumes." Sets `VARIANT=SN`, `STATE=12` (FL),
  `GROUPS=All_Stands`.
- Output is a `.db` you open directly in the FVS GUI.

### `Join_FVS_output_to_arc.txt` — FVS output → ArcGIS join
- After running the DB in the FVS GUI and downloading the output `.db`, reads
  `FVS_Summary2` (per-stand, per-year: Tpa, BA, SDI, CCF, TopHt, QMD, volumes
  CuFt/BdFt, Acc, Mort, MAI, ForTyp, SizeCls, StkCls).
- For each cycle year, copies the MU polygons and joins the summary metrics by
  `MU_ID` (stripping the `MU_` prefix), producing per-year Arc feature classes
  for mapping.

**Role of the LETO workflow:** a complete, working, human-in-the-loop proof that
the TreeMap → Management Units → FIA → FVS → maps chain works, before the
reproducible, scripted `pipeline/` is built. It pins down the fiddly
format/sampling-design details (TPA expansion, species crosswalk, FVS DB schema)
that the production code must reproduce.

---

## 2. `Voronoi_TreeMap/Voronoi_TreeMap.py`
An **earlier, simpler version** of the stand-delineation half of `LETO.V1.1`.
Same Voronoi tessellation of a TreeMap raster domain clipped to parcels
(5-county FL, `MAX_ACRES=120`), but **without** the PLT_CN assignment,
ownership, SMZ, and final cleanup steps — just the geometric core. Essentially
the prototype LETO.V1.1 was extended from.

---

## 3. `FIASQLITE2PGSQL/` — FIA SQLite → PostgreSQL migration
Shell pipeline to migrate the ~66 GB FIA CONUS SQLite database into a local
PostgreSQL instance (port 5433, db `FIADB`):
- `dump_schema.sh` / `countrows.sh` — dump SQLite schema + table list, row
  counts for QC.
- `build_migration.sh` — schema translation: `DATETIME→TIMESTAMP`,
  `VARCHAR(4000)→TEXT`, **`CN INTEGER → VARCHAR(34)`** (preserve FIA sequence
  IDs as strings — the same caution the handoff doc emphasizes for ARTEMIS),
  point-count fields → `BIGINT`; separates constraints from schema, generates
  `\COPY` statements.
- `startpgsql.sh` — `initdb` + `pg_ctl` start a local PG instance in `.pgsql`.
- `dump_data.sh` → `pgcopy.sh` — export each SQLite table to CSV, then create
  FIADB, apply schema, copy, apply constraints. Plus pgAdmin helpers.

**Role:** enables SQL-heavy cohort/QA work that PostgreSQL does better than
SQLite (arrays, `COPY FROM PROGRAM`, `CROSS JOIN LATERAL`, date arithmetic — all
used by `FIA_DATA_PREP`). The handoff doc flags this as **optional for v1**
because the FIA SQLite already contains FVS-ready tables.

---

## 4. `FIA_DATA_PREP/` — ecology-ready FIA cohort tables
Numbered SQL (PostgreSQL) + Python pipeline turning raw FIA tables (PLOT,
SURVEY, SUBPLOT, SUBP_COND, COND, TREE, SEEDLING, REF_SPECIES) into
**longitudinal cohort tables with spatial context**. The folder `README.md` is
thorough; key steps:
- `0_plot_epa.py` / `0_plot_fz.py` — spatially join FIA plots to EPA Level IV
  ecoregions and LANDFIRE fire zones (writes small SQLite lookups, resolves
  overlaps by smallest area, fills unmatched via TIGER counties).
- `0_DATA_TREEAGE.sql` — load external tree-age CSVs + 5-year age-class bins.
- `1–2` — import plot lookups, combine EPA + fire-zone into `DATA_PLOT_ECO`.
- `2_DATA_SUBPLOTS.sql` — longitudinal subplots (repeated measurements stored as
  arrays).
- `3` — disturbance/harvest flags per subplot; `4` — seedlings + trees grouped
  by physical tree; `6` — calculate tree ages across remeasurements (TOTAGE →
  stand age → external), shifted by measurement dates.
- `7_DATA_SPECIES_ECO_MAP.py` — within each ecoregion/fire-zone, map rare
  species to a softwood/hardwood bucket (keep common species).
  `7_DATA_COHORTS_DSTRB.sql` — group live trees into age/species cohorts,
  convert biomass lb/ac → g/m².
- `8_DATA_ECO_COHORTS.sql` — final spatially-annotated cohort table.

**Role:** **not** the FVS input path. Builds ecology/cohort products for
validation, disturbance history, and non-FVS cohort models (LANDIS/Pan). The
handoff doc flags it as secondary — used after the plot-level FVS smoke works.

---

## 5. `PRISM_LT_EPA/` — PRISM climate aggregated by ecoregion
Downloads/processes the PRISM long-term climate dataset and aggregates spatially
by EPA ecoregions:
- `download_epa.sh` — fetch EPA L4 ecoregion polygons → `process_epa.py` → gpkg.
- `download_prism.sh` — `lftp` mirror of full PRISM_LT (~559 GB, **commented
  out** by default).
- `process_prism.py` — zonal stats of monthly/yearly PRISM rasters (ppt,
  tmin/tmean/tmax, tdmean, vpdmin/vpdmax) by EPA polygon → parquet.
- `coalesce_by_agg.py` → `aggregate_polygons.py` → `vacuum.py` — parquet →
  SQLite, re-aggregate by EPA L3/L4, vacuum. Output: `prism_lt_epa.db`.

**Role:** ecoregion-scale climate summaries usable as FIA cohort covariates. For
**pixel-level** ARTEMIS climate, the project uses **GEE PRISM normals** instead
(named in `config/data_paths.yaml`), avoiding the 559 GB local dependency. The
committed `.db` is a Git LFS pointer (~1.9 GB when materialized).

---

## 6. `check-staged-large-files.sh` (repo plumbing)
Pre-commit Git hook that **rejects staged blobs > 99 MiB** (GitHub's practical
limit), checking the staged *object* (not the working tree) so partial commits
are caught. Supports override via `MAX_GIT_BLOB_BYTES`. The root `README.md`
tells users to `git config core.hooksPath .githooks` to activate it. Prevents
accidentally committing the multi-GB rasters/databases (TreeMap ~4.6 GB, FIA ~66
GB, ownership ~3.7 GB) the project works with. Not science — infrastructure.

---

## How it all serves the ARTEMIS motivation

ARTEMIS needs to go **pixel (TreeMap) → stand (Management Unit) → FIA plot
(PLT_CN) → FVS run → per-year metrics painted back to pixels**, at 30 m across
Florida and eventually the Southeast. The `scripts/` folder holds the
**field-tested, ArcGIS-and-Postgres-era predecessors** of the modules now being
rebuilt as deterministic, scriptable, pixel-level Python under `pipeline/`.

| ARTEMIS concern | Reference script |
|---|---|
| Stand delineation (Voronoi MUs, parcel clip, size targets) | `LETO.V1.1`, `Voronoi_TreeMap/` |
| TreeMap pixel → FIA plot linkage (weighted PLT_CN) | `LETO.V1.1` (`assign_plt_cn`) |
| Ownership + riparian SMZ for management | `LETO.V1.1` |
| FVS-ready stand/tree inputs (species crosswalk, TPA expansion, imputation) | `LETO_CSV_PIPELINE` |
| FVS DB schema + sampling design (the `BASAL_AREA_FACTOR=-1` gotcha) | `Create_FVS_Database` |
| FVS outputs → spatial join for mapping | `Join_FVS_output_to_arc` |
| Heavy FIA SQL exploration (cohorts, disturbances, ages) | `FIASQLITE2PGSQL/`, `FIA_DATA_PREP/` |
| Ecoregion climate covariates | `PRISM_LT_EPA/` |
| Safe data handling in git | `check-staged-large-files.sh` |
| Integration roadmap / gotchas | `notes/southeast-fvs-artemis-export-package.md` |

They encode the hard-won format knowledge (TPA expansion, species codes,
ID-as-string preservation, FVS DB layout, multi-state FIA borrowing) that the
production pipeline must reproduce.
