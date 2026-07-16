# Notebooks index (`notebooks/`)

What every notebook and helper in `notebooks/` does, what it needs to run, and its current
runnable/error status. Deep-dive notes are linked per group. Status last checked **2026-07-14**.

## Shared prerequisites

Most notebooks depend on one or more of these; a "can't run today" status below almost always
traces to one of them, not to a code bug.

| Prerequisite | State on 2026-07-14 | Who needs it |
|---|---|---|
| **`/mnt/d` external drive** (PERSEUS_DAT) | **Unmounted** — stale mount, reads fail with "No such device". `data/raw → /mnt/d` symlink is dead. | FVS, all three Clearcut notebooks (LF2022 EVT tif), Similarity-Embeddings |
| **Earth Engine auth** | Credentials file exists, but `ee.Initialize()` fails asking for re-auth; `cac.init_ee()` falls back to interactive `ee.Authenticate()`, so **no headless run**. | All 5 GEE notebooks |
| **Network egress** | **Working** (remote COG + Census download reachable). | TreeMap COG |
| **PyPI deps** (`geemap`, `geopandas`, `rasterio`, `sklearn`, …) | **All present** in `.venv` (py 3.14). | all |

## At-a-glance

| Notebook | What it does | Needs | Runnable today? |
|---|---|---|---|
| [`TreeMap_COG_County_Summary.ipynb`](#treemap_cog_county_summaryipynb) | Clip a remote COG (URL or STAC) and compute zonal stats per polygon (default: TreeMap-like raster × Southeast counties) | network | **Yes** (only one needing neither drive nor GEE) |
| [`Embedding-Similarity-AOI-Finder.ipynb`](#embedding-similarity-aoi-finderipynb) | Pick reference clearcut points → vector layer of all AlphaEarth-similar land in an AOI | GEE | Blocked by GEE re-auth only (no drive needed) |
| [`Clearcut-vs-Agriculture-Embeddings.ipynb`](#the-clearcut-vs-agriculture-investigation) | Method 1: AlphaEarth embedding separability of clearcut vs farmland | GEE + `/mnt/d` | No (GEE + drive) |
| [`Clearcut-vs-Agriculture-EVT-Change.ipynb`](#the-clearcut-vs-agriculture-investigation) | Method 2: LANDFIRE EVT 2016→2022 forest→ag/grass change detector + cross-method comparison | GEE + `/mnt/d` | No (GEE + drive) |
| [`Clearcut-Grassland-Feature-Engineering.ipynb`](#the-clearcut-vs-agriculture-investigation) | Build the model-ready feature table + baseline spatial-CV model to score "is this grassland pixel actually clearcut forest?" | GEE + `/mnt/d` | No (GEE + drive) |
| [`Similarity-Embeddings.ipynb`](#similarity-embeddingsipynb-prototype) | Original bare-bones AlphaEarth similarity prototype (superseded by the AOI finder) | GEE + `/mnt/d` | No — **superseded / stored error** |
| [`FVS_5county_growth_smoke.ipynb.old`](#fvs_5county_growth_smokeipynbold) | Grow 10 TreeMap/FIA stands with FVS Southern (SN); write keyfiles; summarize output into DuckDB | missing modules + `/mnt/d` | **No — broken; renamed `.old`** |
| `clearcut_ag_common.py` | Shared helpers for the 4 clearcut/similarity notebooks (constants, GEE/rasterio sampling, labeling, similarity, vectorization) | — | Imports OK; pure helpers covered by `tests/test_clearcut_ag_common.py` (pass) |

---

## The clearcut-vs-agriculture investigation

Four notebooks + `clearcut_ag_common.py`, all building toward one question: **are Florida pixels
that LANDFIRE EVT labels agriculture/grassland/shrubland actually recently clearcut forest?**
Full findings, data-availability constraints, and run results:
→ **[clearcut-vs-agriculture-embeddings.md](clearcut-vs-agriculture-embeddings.md)**.

- **`Clearcut-vs-Agriculture-Embeddings.ipynb`** — *Method 1.* Samples labeled Florida points,
  attaches AlphaEarth (`GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL`) vectors, and shows embeddings
  separate clearcut from farmland (CV acc ≈ 0.96). Writes the shared
  `data/interim/clearcut_ag/embeddings_samples.csv` that Method 2 reuses.
- **`Clearcut-vs-Agriculture-EVT-Change.ipynb`** — *Method 2.* Flags forest(2016)→ag/grass/shrub
  transitions as clearcut evidence, scores them against the LCMS tree-removal signal, and does the
  cross-method comparison. Bridges the GEE-2016 vs local-LF2022 code-scheme gap at forest-vs-not
  level only (GEE hosts a single EVT vintage).
- **`Clearcut-Grassland-Feature-Engineering.ipynb`** — assembles the model-ready table (AlphaEarth
  event + pre-year embeddings, L2 delta, LCMS history, EVT-change), tags leakage-prone columns,
  and runs a baseline logistic/RF model under spatial block CV. Key caveat baked into the notebook:
  featsets including the pre-year embedding score AUC ≈ 1.0 **by construction** (near-circular with
  the label); featset A (event embeddings only, AUC 0.994) is the honest number. Deliverable is the
  apply-set `frac_forest` per confused EVT class.
- **`clearcut_ag_common.py`** — the shared library all of the above (and the AOI finder) import:
  `init_ee`, `find_repo_root`, `load_florida`, EVT/LCMS sampling, `build_sample_table` /
  `build_feature_table`, labelers, similarity + vectorization helpers, and the constants
  (`EMBEDDING_COLLECTION`, `EMBEDDING_BANDS`, `DEFAULT_EVENT_YEAR=2022`, `DEFAULT_PRE_YEAR=2020`).
  Reads the local LF2022 EVT tif on `/mnt/d`. Pure helpers unit-tested in
  `tests/test_clearcut_ag_common.py`; output invariants in `tests/test_clearcut_ag_outputs.py`
  (skips when no run outputs exist).

## `Embedding-Similarity-AOI-Finder.ipynb`

Standalone tool (productionized successor to the Similarity-Embeddings prototype): pick reference
clearcut points as a coord list **or draw them on a geemap map**, choose an AOI (all Florida or
named counties via `TIGER/2018/Counties`), pick an embedding year, and get a two-layer GeoPackage —
`reference_points` (what you selected) + `similar_regions` (everything with max cosine similarity ≥
threshold, speckle-filtered by `min_area_ha`). GEE-only (uses local `config/extent.geojson`, not
`/mnt/d`), so the sole blocker today is GEE re-auth. **Commit gotcha:** executing the map cells
bloats the `.ipynb` to ~17 MB with embedded widget state — strip `nb.metadata["widgets"]` before
committing. Details in [clearcut-vs-agriculture-embeddings.md](clearcut-vs-agriculture-embeddings.md#embedding-similarity-aoi-finder-2026-07-02).

## `Similarity-Embeddings.ipynb` (prototype)

The original scratch notebook that first demonstrated AlphaEarth cosine similarity to a single
Ocala reference point — **superseded by the AOI finder and not maintained.** Uses inline
`ee.Authenticate()`/`ee.Initialize()` and a brittle hardcoded relative path
`data/raw/tl_2022_us_state/tl_2022_us_state.shp` (the dead `data/raw → /mnt/d` symlink); its
committed output already carries a stored `DataSourceError: ... No such file or directory` and its
last cell is empty. Kept for reference only — use the AOI finder for real work.

## `FVS_5county_growth_smoke.ipynb.old`

**Broken as committed — renamed to `.old` (2026-07-14) to mark it retired. Do not expect it to run.** Intended smoke-run harness: probe local FVS `SN`
shared libraries, select 10 FVS-ready stands from the consolidated SQLite DB, write no-management
keyfiles (Linux + Windows bundles), optionally run the local `FVSsn.so`, and summarize output into a
DuckDB table/viewer. Two blockers today: (1) its first cell imports four `pipeline/s4_fvs/` modules
(`keyword_builder`, `probe_libraries`, `run_smoke`, `summarize_smoke`) that **are absent from the
repo and were never committed** → `ModuleNotFoundError` at cell 1; (2) its input DB is on the
unmounted `/mnt/d` drive. Known runtime finding (from when it did run): local `FVSsn.so` loads but
DB-backed projections segfault (`OPEN FAILED FOR 17 … unrecognized token: "'"`), so the Windows GUI
FVS at `C:\FVS` is the intended handoff. Full context + recovery steps:
→ **[fvs-5county-growth-smoke.md](fvs-5county-growth-smoke.md)**.

## `TreeMap_COG_County_Summary.ipynb`

Generic remote-raster zonal-summary tool: reads a Cloud-Optimized GeoTIFF from a direct URL or STAC
Item (`resolve_cog_href`), optionally clips it to a vector footprint, and summarizes raster values
per polygon — `continuous` mode (count/sum/mean/std/min/max/quartiles, the default for the example
`float32` COG) or `categorical_counts` mode (per class value + area, for integer TreeMap
`TM_ID`/`VALUE` rasters). Default vector is Census 2023 generalized counties filtered to Southeast
state FIPS; outputs land in `data/interim/treemap_county_summary/`. Remote reads are windowed one
polygon at a time (no full-raster download). **The only notebook here that needs neither `/mnt/d`
nor GEE** — remote COG open re-verified working 2026-07-14. Set `MAX_FEATURES_FOR_DEMO = 5` for a
fast smoke test (default `None` runs all Southeast counties, slow). Details:
→ **[treemap-cog-county-summary.md](treemap-cog-county-summary.md)**.

---

## Testing summary (2026-07-14)

How the notebooks were checked and what turned up. Full execution was not possible for the
GEE/`/mnt/d` notebooks in this environment (unmounted drive + non-interactive GEE), so testing was
static + dependency/resource verification, plus a live check of the one network-only notebook.

**Checks run**
- **Syntax:** every code cell of all 7 notebooks `ast.parse`d cleanly — **no syntax errors**.
- **Imports:** every third-party import resolves in `.venv` (py 3.14).
- **Helper modules:** `clearcut_ag_common` imports; `pipeline/s4_fvs/*` FVS helpers **fail to
  import (missing)**.
- **Unit tests:** `uv run pytest tests/` → **60 passed, 10 skipped, 4 failed**. All 4 failures are
  `test_config.py` `/mnt/d` path-existence assertions (drive unmounted) — environmental, not code.
  (Run `pytest tests/`, not bare `pytest`: collection otherwise dies scanning the dead `data/raw`
  symlink.)
- **Live network:** the example remote COG opens (`GTiff 154179×97279 float32 EPSG:5070`).
- **GEE:** `ee.Initialize()` fails → requires interactive re-auth; no GEE notebook runs headless.

**Errors / issues found**
1. **`FVS_5county_growth_smoke.ipynb.old` — hard error (renamed `.old`).** Imports `pipeline.s4_fvs.keyword_builder`,
   `.probe_libraries`, `.run_smoke`, `.summarize_smoke`, none of which exist (never committed; only
   `paint_fvs_to_raster.py` is present). Also `tests/test_s4_fvs_keyword_builder.py` (referenced in
   the note) is absent. Fails at cell 1. → recover/rewrite those four modules.
2. **`Similarity-Embeddings.ipynb` — stored error + brittle path.** Hardcoded
   `data/raw/tl_2022_us_state/tl_2022_us_state.shp` on the dead symlink; committed output holds a
   `DataSourceError`. Superseded by the AOI finder; low priority.
3. **Environmental (not code bugs):** the three Clearcut notebooks + AOI finder + Similarity can't
   run headless because GEE needs re-auth, and everything except TreeMap COG additionally needs the
   `/mnt/d` drive remounted.

**No code errors** were found in the four Clearcut/AOI notebooks or TreeMap COG — they are blocked
only by the environment, not by defects.
