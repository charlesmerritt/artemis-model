# Weekly artifact — 2026-11-09

The **young-plot initial state** for every added-back acre of the five-county north Florida
pilot: 333,040 add-back pixels (74,068 acres) that until now carried a neighbour plot's ID and
no tree list each get their own TreeMap ID, a stand age dated from Landsat, a full 27-column
VAT row and a real FIA tree list — 3,035 new IDs, 68,306 tree rows, zero unmatched pixels.

Produced by the repository's own ADR 0003 second pass, unmodified:

```bash
uv run python -m pipeline.s1_initial_state.add_back_stand_age <run>/aoi_5county --obata-tif … --hansen-tif …
uv run python -m pipeline.s1_initial_state.add_back_ids       <run>/aoi_5county --obata-tif … --hansen-tif … --vat … --tree-table … --db …
```

## Why this artifact

The run folder this reads carries its own open limitation, in its `validation.json`:

> "Donor provenance 4 requires consumer-side establishment; **tree lists were not synthesized**."

That is the hole `add_back_ids` fills. `county_improvement` adds forest back to TreeMap's holes
and stamps each added pixel with the *neighbour* plot's `TM_ID` — an ID that AGENTS.md warns must
never be joined to trees — and establishes the whole lot at one fixed `target_age: 5`. ADR 0003
(`docs/adr/0003-dated-stand-age-and-young-plots.md`) replaces both: each pixel gets the age its
own Landsat cut year implies, and a young plot drawn from TreeMap 2022's own library whose forest
type matches its neighbour's and whose FIA `STDAGE` is within ±2 years of that age. After this
pass an add-back pixel holds everything any other TreeMap pixel does, so stage 3 and FVS can read
it like any other stand.

**It has never been delivered in this series, and `<run>/young_plots/` does not exist anywhere in
the R2 bucket** — only the run's own 2,665-row `establishment_tree_lists.csv`, keyed by
`(TM_ID, establishment_mode)` at a single age, which is not a per-pixel initial state. Every
previous artifact is something else: basal-area rasters (07-19, 08-03), even-flow targets (07-26),
the greedy harvest schedule (08-10), the trajectory library (08-17), riparian/SMZ corridors
(08-24), the annealed plans (08-31, 09-07, 09-14), and the s6 FVS age-class report (10-19, and
**11-02, which re-shipped 10-19 byte for byte** — every table, figure and `run_summary.json`
identical). This week deliberately does not touch that report.

It is also reproducible here without a compiled `FVSsn`, which remains unavailable in this
sandbox (see *What did not run*).

## What it shows

The added-back acres are a recently-cut pine landscape, not a uniform age-5 blanket:

| | acres | share |
|---|---|---|
| dated by Landsat (Obata and/or Hansen) | 69,831 | **94.3%** |
| undated → bookend-stratum median | 4,237 | 5.7% |
| stand age ≤ 10 | 66,769 | 90.1% |
| stand age 5–8 (cut 2014–2017) | 56,728 | 76.6% |

Median stand age is **7**, not the 5 the run assumed, and the spread runs 0–20. Where both
detectors date a pixel they agree on 32,280 acres (43.6%); Hansen alone carries 22,592 and Obata
alone 14,960.

The match is close almost everywhere: **99.1%** of acres take a young plot of the neighbour plot's
*own* forest type, only 0.9% fall back to its forest-type group, and **nothing is unmatched** —
93.7% of acres land on a plot whose FIA age equals the pixel's exactly (`AGE_GAP` 0). The
established forest is what north Florida replanting looks like: slash pine 44.3%, loblolly 21.3%,
longleaf 12.0%, with slash (SPCD 111) and loblolly (131) together 60.4% of the 52.4 M live trees
the 74,068 acres now carry (708 TPA/acre, seedlings included at ages 0–2).

All six of the stage's own checks pass (`young_plots/validation.json`): every add-back pixel
resolves to a VAT row, every VAT row is full, every new ID has trees, no new ID collides with a
TreeMap value, the VAT counts match the raster, and no other pixel changed.

## Files

| | |
|---|---|
| `young_plots/treemap2022_improved.tif` | the run's raster, each add-back pixel under its new ID (uint32, ≥ 1,000,000) |
| `young_plots/treemap2022_provenance.tif` | the run's, every add-back pixel provenance 4 |
| `young_plots/treemap2022_improved_vat_extension.csv` | 3,035 rows: TreeMap's 27 VAT columns + age, cut year, age source, match level, age gap, and the young and neighbour plots |
| `young_plots/treemap2022_improved_tree_extension.csv` | 68,306 tree rows, one set per new ID |
| `young_plots/validation.json` | the six checks, and acres by stand age, match level and age source |
| `add_back_cut_year.tif`, `add_back_cut_year_detector.tif` | the dating evidence: cut year per pixel and which detector(s) set it |
| `add_back_stand_age.csv` | add-back acres by stand age and age class, from the first pass |
| `tables/*.csv` | seven summaries — by stand age, age source, match level, forest type, species mix, and the totals |
| `figures/*.png` | three figures, each drawn only from the table beside it |
| `make_figure.py` | the figures and tables; reads only `young_plots/` and the run provenance |
| `build_fia_al_fl_ga.py` | the FIA input prep described below |

Read the ID columns back with `pipeline.ids.read_id_csv` and
`add_back_ids.EXTENSION_ID_COLUMNS` — the control numbers are exact strings end to end.

## R2 inputs pulled

Six keys, ~1.5 GB kept. All land under gitignored `data/r2_cache/` and **none is committed**.

| R2 key | Size | Role |
|---|---|---|
| `data/improved-rasters/aoi_5county/` | 25 MB | the stitched run folder: improved/provenance rasters, bookend strata, manifest |
| `data/gee_raster_correct_data/runs/disturbance-2010-2023-p17-r39-MovAvgRange3-mif4-tol3/lastDist-…tif` | 8.2 MB | Obata `lastDist`, the cut-year detector |
| `data/raster_improvement_data/interim/aoi/hansen_aoi.tif` | 11.6 MB | Hansen `lossyear`, the other detector |
| `data/TreeMap-2022/Data/TreeMap2022_CONUS.tif.vat.dbf` | 34.3 MB | TreeMap's VAT: `Value`, `PLT_CN`, `FORTYPCD` and the 27 copied columns |
| `data/TreeMap-2022/Data/TreeMap2022_CONUS_Tree_Table.csv` | 238 MB | the young plots' tree lists (read in chunks) |
| `data/Lowe_TreeMap_Chaz/output/SQLite_FIADB_{AL,FL,GA}.db` | 12.6 GB | FIA `PLOT`/`COND`/`SEEDLING` for the young-plot library |

The 4.85 GB `TreeMap2022_CONUS.tif` was **not** pulled: this pass reads the run's own clipped
raster, not CONUS. Both detector rasters hash **exactly** to the SHA256s the run's `manifest.json`
recorded for them (`53af25fa…`, `bdcc1e79…`), so the dating uses the same pixels the run did.

### The one input that had to be assembled

`young_plot_library.read_young_plot_library` opens a *single* SQLite and filters
`STATECD IN (1, 12, 13)` in each of its three queries, because TreeMap draws donors across state
lines. Its declared source, `/mnt/d/SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db`, is **70.7 GB** and
does not fit this sandbox's 29 GB allowance, and R2 offers only per-state FIADB files beside it.
`build_fia_al_fl_ga.py` copies the three tables that library reads — `PLOT`, `COND`, `SEEDLING` —
out of `SQLite_FIADB_{AL,FL,GA}.db` into one 175 MB file, each filtered to its own `STATECD`.
Rows are copied verbatim with no cast, so control numbers keep every digit. The result holds
185,789 plots / 221,445 conditions / 353,941 seedlings across the three states; the 12.6 GB of
source databases were deleted after the merge. **No repository code was changed** — the local
paths go in through the stage's existing `--vat`, `--tree-table` and `--db` flags.

The repo's own `r/05_consolidate_FIA_SQLite.R` and R2's `FIA_5county_consolidated.db` are *not*
substitutes: both subset to the 693 TreeMap-matched plots of the AOI, which would draw the young
plots from the donor plots themselves rather than from the three-state library the code intends.
That was checked before falling back (693 PLOT rows) and rejected.

## A finding: the R2 run folder mixes two runs

`improved-rasters/aoi_5county/` is **not internally consistent**, and anyone reading it should
know. Its files fall into two groups by mtime:

* **11:57 (coherent, and what this artifact uses)** — `treemap2022_{improved,provenance,published}.tif`,
  `nwos2022_*`, `bookend_strata.tif`, `add_back_method_bits.tif`, `establishment_*`,
  `county_summaries.csv`, `summary.json`, and all five folders under `counties/`. This is a
  **bookends-only** run: `summary.json` gives `added_back_credit_acres` of 74,068 for bookends and
  `null` for both Obata and Hansen, the gated method bits hold only bit 1, and the provenance
  raster has 7,191 + 325,849 = **333,040** add-back pixels = **74,068.0 acres**, matching
  `summary.json` exactly.
* **13:15 (a different, later run)** — `manifest.json`, `validation.json`,
  `add_back_method_bits_raw.tif` (bits 1–7, i.e. several methods), `evt2022_*`,
  `method_proposals.csv`, `agreement_cells.csv`, `ownership.csv`. Its `validation.json` reports
  **849,926** added pixels (189,023 ac) and `rule: union` — 2.55× the provenance raster beside it.

So the headline count here is 333,040 px / 74,068 ac, taken from the rasters actually read, **not**
the 849,926 in the folder's `validation.json`. For the same reason the first pass writes no
`at_least_two` rows (the 11:57 gated bits never set two methods), and it does not reproduce the
committed `docs/improved-rasters-fl/data/add_back_stand_age.csv`, which was made from the
multi-method run. `add_back_ids` itself reads only 11:57 files — `treemap2022_improved.tif`,
`treemap2022_provenance.tif`, `bookend_strata.tif` — plus the two detector rasters, so its output
is internally consistent with the bookends-only run throughout.

Dating is unaffected by the run being bookends-only: `add_back_stand_age` dates a pixel from any
detector year in 2001–2022 whether or not that detector *proposed* it, which is why 94.3% of these
acres carry a Landsat date even though neither detector contributed to the add-back decision.

## Dependencies

None installed. The committed `uv.lock` environment was used as-is (`uv sync`): Python 3.14,
pandas, numpy, rasterio, pyogrio, scipy, matplotlib; rclone was already on the image. The modules'
own tests were run first and pass — `tests/test_add_back_stand_age.py`,
`tests/test_add_back_ids.py`, `tests/test_young_plot_library.py`: 41 passed.

## How to regenerate

With `/mnt/d` mounted, both passes take their defaults and the flags collapse to the run folder.
From this sandbox, with the inputs fetched into `data/r2_cache/` as above:

```bash
uv sync
RUN=data/r2_cache/improved-rasters/aoi_5county

uv run python weekly-artifact/2026-11-09/build_fia_al_fl_ga.py \
    --states-dir data/r2_cache/fia/states --out data/r2_cache/fia/fia_al_fl_ga.db

uv run python -m pipeline.s1_initial_state.add_back_stand_age $RUN \
    --obata-tif data/r2_cache/detectors/lastDist.tif \
    --hansen-tif data/r2_cache/detectors/hansen_aoi.tif

uv run python -m pipeline.s1_initial_state.add_back_ids $RUN \
    --obata-tif data/r2_cache/detectors/lastDist.tif \
    --hansen-tif data/r2_cache/detectors/hansen_aoi.tif \
    --vat data/r2_cache/treemap/TreeMap2022_CONUS.tif.vat.dbf \
    --tree-table data/r2_cache/treemap/TreeMap2022_CONUS_Tree_Table.csv \
    --db data/r2_cache/fia/fia_al_fl_ga.db

uv run python weekly-artifact/2026-11-09/make_figure.py --run-dir $RUN --out-dir weekly-artifact/2026-11-09
```

`add_back_ids` exits non-zero if any of its six checks fails, so a green run is the guarantee that
the four outputs join. The run is deterministic: the same run folder, detectors, VAT, tree table
and FIA database give the same IDs, because new IDs are issued from 1,000,000 in sorted
`NEW_ID_KEY` order. Outputs land in `<run>/young_plots/`; the copies here are verbatim.

## What did not run, and why

**The annealed plan (`uv run python main.py library|schedule`) was not attempted**, for the same
reason as 2026-11-02: its `library` stage needs a compiled Southern-variant FVS at `fvs/bin/FVSsn`,
and the documented build (`weekly-artifact/2026-08-31/README.md`) ends at a `make` that this
sandbox's permission classifier refuses for freshly cloned third-party source. That is an
environment policy, not a repository defect. Nothing was faked in its place.

Two things this artifact sets up but does not do:

1. **The young plots are not yet consumed downstream.** `pipeline/run.py`'s `--inventory leto`
   path reads a run directory's `stands.csv` and `FVS_Data.db`, and its FL5 driver
   (`experiments/2026-10-19_fl5-leto-stands`) is still not committed, so there is no committed
   path from `young_plots/` into an FVS run. The outputs here are the input that path will need.
2. **The spectral tie-break (#90) is still owed.** Where Obata and Hansen disagree by more than
   two years the later year stands; 43.6% of acres are dated by both, so the tie-break will move
   some ages when it lands.
