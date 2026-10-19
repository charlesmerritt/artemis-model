# Weekly artifact — 2026-10-19

## Artifact

The canonical FVS output set for the five-county Florida pilot: **10 age-class tables**
(`tables/*.csv`), **8 figures** (`figures/*.png`), and `run_summary.json` carrying the run's
identity, the reporting grid, crosswalk coverage, and the acreage each table foots to.

Produced by `pipeline/s6_outputs/run_pipeline.py`, which its own module docstring calls
"the canonical FVS output pipeline: a real FVS output database in, figures out."

## Why this one

Three reasons.

1. **It is the step-4 deliverable.** `AGENTS.md` step 4 asks for "a final output table with
   the trajectory for each management unit and its vegetation data from FVS for a 50 year
   time horizon." This run reports 2026–2076 on a balanced 5-year grid — eleven reporting
   years, a 50-year horizon exactly.
2. **The repo calls it canonical, and it had never been run here.** No previous weekly
   artifact touches `s6_outputs` or age-class output; `grep -rl 's6_outputs\|age_class'
   weekly-artifact/` returns nothing before this directory. The last six weeks
   (08-31 → 10-12) all worked the annealing/scheduling side.
3. **It is the baseline the annealed plans are measured against.** This is the
   `NoManagement_5countyFL_1` counterfactual: what the landscape does if nobody cuts. The
   harvest schedules and annealed plans of recent weeks only mean something against it.

Every number in every figure is written to a CSV beside it, so a figure can be redrawn or
restyled without recomputing anything — stage 5 reads only stage-4 tables.

## R2 inputs pulled

Two files, 35.4 MB total. The sandbox has no `/mnt/d`, so `pipeline/data_access.py` resolved
both from the bucket on its own; no path override or config edit was needed.

| R2 key | Size | Used for |
|---|---|---|
| `r2:artemis-r2/data/Artemis_project_fvs_copy_no_management/FVSOut.db` | 14.4 MB | `FVS_Cases` + `FVS_Summary2` → the stand-year frame |
| `r2:artemis-r2/data/20260804_095846_Hard_Ownership_Boundaries/Inputs/FVS_StandInit.csv` | 21.0 MB | `PLT_CN` → (`OWN_TYPE`, `MGMT_TYPE`, `Acres`) owner crosswalk |

Both landed in `data/r2_cache/` (gitignored). The 1.5 GB `FVS_TreeInit.csv` sibling and the
1.08 GB `FVS_Data.db` were **not** pulled — nothing in this pipeline reads them.

## Command

```bash
uv run python -m pipeline.s6_outputs.run_pipeline
```

That is the documented invocation, verbatim from the module docstring. No arguments, no
patches, no local config changes. Outputs land in `data/processed/fvs_outputs/`
(gitignored); this directory is a copy of that tree.

## Dependencies

None installed. The checked-in `.venv` already satisfied `pyproject.toml` (pandas 3.0.3,
rasterio, geopandas, matplotlib, duckdb on Python 3.14). `rclone` was already on PATH with
`RCLONE_CONFIG_R2_*` in the environment.

## What the run reported

| | |
|---|---|
| Run | `NoManagement_5countyFL_1`, SN (Southern) variant, mgmt id `A002` |
| Cases / stands | 693 / 693 |
| FVS span | 1997–2076 |
| Reporting years | 2026, 2031, … 2076 (11 balanced years) |
| Excluded | 28 unbalanced years before 2026 — not every case reports in them |
| Whole-run weight | 3,797,722 sampling-weight acres |
| Owner-attributed | 375 of 693 cases (54.1%), 1,052,306 acres |
| Age classes | width 10, max 140 |

Tables split by weight basis. `age_class_overall`, `*_by_forest_type`, `*_by_state`,
`*_by_county` and `mean_age_by_forest_type` foot to the full 3,797,722 acres on
`sampling_weight`. The four owner tables foot to 1,052,306 acres on `owner_acres`, because
only crosswalked plots carry an owner class.

### Reading the result

Under no management the landscape ages almost exactly one year per year, which is the
correctness check as much as the finding — mean stand age climbs ~5 years per 5-year cycle
in every forest type:

| Forest type | Mean age 2026 | Mean age 2076 |
|---|---|---|
| Pine (softwood) | 38.2 | 85.3 |
| Oak/pine (mixed) | 48.4 | 99.5 |
| Hardwood | 58.4 | 109.8 |

`age_class_over_time.png` shows the consequence: the young classes drain and never refill,
and by 2076 pine has essentially no 0–9 acres left. Corporate and NGO land starts youngest
(~32 and ~39 years), Federal and Unknown oldest (~57 and ~64) — the plantation signature,
visible before any management is applied.

## Caveats

- **54.1% owner coverage.** 318 of 693 cases are `Unattributed`. The owner tables are honest
  about this — they foot to 1.05 M acres, not 3.80 M — but the owner cut is a little over
  half the landscape, not all of it.
- **`Unknown` is the oldest owner class.** Plausible (unattributed land skews toward
  unmanaged remnants) but it is a residual bucket, not a real owner.
- **This is the no-management run.** It is the counterfactual, not a prediction. The
  managed trajectories live in the annealed plans of 08-31 → 10-12.

## Regenerate

```bash
# From the repo root, with RCLONE_CONFIG_R2_* set (data_access falls back to R2
# wherever /mnt/d is absent).
uv run python -m pipeline.s6_outputs.run_pipeline --dry-run   # check what it would read
uv run python -m pipeline.s6_outputs.run_pipeline             # ~1 min incl. the 35 MB fetch

# Redraw figures from tables already on disk, without recomputing:
uv run python -m pipeline.s6_outputs.run_pipeline --stages visualize

cp -r data/processed/fvs_outputs/{tables,figures,run_summary.json} weekly-artifact/2026-10-19/
```

Point it at another run with `--fvs-out <path> --out-dir <dir>`.
