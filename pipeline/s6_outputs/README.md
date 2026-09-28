# s6 — canonical FVS outputs

The reporting end of the pipeline. It takes an FVS Online output database — a finished run,
not a projection in progress — and turns it into the age-class distributions ARTEMIS reports
on, cut by forest type, owner class, and area, as CSV tables and the figures drawn from them.

```bash
# everything: resolve, extract, attribute, summarize, visualize
uv run python -m pipeline.s6_outputs.run_pipeline

# what would be read and written, writing nothing
uv run python -m pipeline.s6_outputs.run_pipeline --dry-run

# redraw the figures from tables already on disk
uv run python -m pipeline.s6_outputs.run_pipeline --stages visualize

# a different run
uv run python -m pipeline.s6_outputs.run_pipeline \
  --fvs-out /mnt/d/some_project/FVSOut.db --out-dir data/processed/that_run
```

## The stages

| # | Stage | What it does | Module |
|---|---|---|---|
| 1 | `resolve` | Locate `FVSOut.db` and the owner crosswalk, fetching from the R2 mirror when the workstation drive is not mounted | `pipeline/data_access.py` |
| 2 | `extract` | `FVS_Cases` ⨝ `FVS_Summary2` → one stand-year frame; settle the reporting year grid | `fvs_out_db.py` |
| 3 | `attribute` | Age class, forest-type group, state/county; owner-class acre shares | `age_class.py`, `owner_attribution.py` |
| 4 | `summarize` | The canonical tables, written to `tables/*.csv` with `run_summary.json` | `age_class.py` |
| 5 | `visualize` | The figures — and nothing else | `figures.py` |

The last stage is deliberately inert. Every number in every figure is already in a CSV beside
it before stage 5 runs, so a chart can be restyled, redrawn, or thrown away without
recomputing anything, and no figure can hold a value that is not also on disk. If a bar looks
wrong it is wrong in `tables/`, and that is the file to read.

Outputs land in `data/processed/fvs_outputs/` (gitignored, mirrored to R2 under
`Artemis_data/processed/`).

## Inputs

| What | Config key | Role |
|---|---|---|
| `FVSOut.db` | `raw.Artemis_project_fvs_copy_no_management.FVSOut_db` | The run. Requires the `FVS_Cases` and `FVS_Summary2` tables, which FVS Online writes when the Summary2 output is requested |
| `FVS_StandInit.csv` | `raw.hard_ownership_boundaries.stand_init_csv` | The owner-class crosswalk. Optional — without it the owner and management-type tables are skipped and everything else still runs |

Reporting policy — age-class width, the year-grid rule, labels, the palette — is in
[`../../config/fvs_outputs.yaml`](../../config/fvs_outputs.yaml). The pine/mixed/hardwood
split is **not** redeclared there: it is read from `config/fallback_treelists.yaml` through
`pipeline.s4_fvs.fallback_treelists.forest_type_group`, so the stage that reports on forest
type and the stage that routes on it cannot drift apart.

## Three things to know before reading a number off these tables

### The early years are not reportable

Stands enter an FVS Online run at their own FIA inventory year. In the five-county
no-management run only 106–181 of the 693 cases report in any year before 2026, and all 693
report from 2026 on. An age-class distribution computed over an unbalanced year describes
*which stands happened to be measured then*, not the landscape — so the canonical tables use
the balanced grid (2026–2076 for that run) and `run_summary.json` lists every year excluded.
`year_grid.mode: all` overrides this; a run whose cycles never line up is an error, not a
thin report.

### Two acre bases, never added together

| Basis | Where it comes from | Covers | Used for |
|---|---|---|---|
| `sampling_weight` | `FVS_Cases.SamplingWt` — FIA expansion acres | every case (3.80 M acres in the five-county run) | overall, forest type, state, county |
| `owner_acres` | management-unit acres from the ownership-segmented run | crosswalked plots only (1.05 M acres, 375 of 693 cases) | owner class, management type |

Both are acres and neither is wrong, but they answer different questions and their totals
differ by a factor of three. Every emitted row carries its basis in a `weight_basis` column
and every figure names it in the caption. A table that mixed them would be wrong in a way no
total would reveal.

### A plot is not one owner

`FVSOut.db` carries no ownership — a case is a FIA plot, and plots do not partition by owner.
The ownership run does, and TreeMap imputes the same plot onto management units all over the
landscape: 319 of the 375 crosswalked plots initialize units in more than one owner class. So
`owner_attribution.py` does not pick a dominant owner. It computes **acre shares** and
apportions each stand-year across them, which is how the trajectory actually lands on the
ground. Plots the crosswalk does not reach are reported as `Unattributed` rather than dropped,
so the owner tables still account for every case in the run.

## Case and cycle semantics

Each stand must have exactly one FVS case. S6 rejects multiple cases for a stand before
aggregation, including alternatives on disjoint year grids. To report alternatives, supply
separate input databases with one chosen case per stand; different stands may still have
different management IDs.

An unmanaged cycle uses `RmvCode: 0`. A managed cycle uses the post-removal state
(`RmvCode: 2`) and excludes the pre-removal state (`RmvCode: 1`). Duplicate cycle states
and pre-removal cycles without a final state are errors.

Mean-age tables retain reporting-year groups with no known ages as `mean_age: NaN` and
zero known-age acres. When no known ages exist, the mean-age figure is skipped with a
warning, while age-class figures remain available. Unknown forest types appear in every
forest-type figure, and custom labels retain their configured palette colors.

County figures show the largest `areas.top_n_counties` counties for the selected year
and pool the remainder into `Other counties`. Snapshot and time-series panels sharing an
axis use one acreage formatter chosen from their combined peak.

Rerunning summarize removes obsolete S6 tables and invalidates previous S6 figures;
visualize removes obsolete S6 figures after drawing the current set. Unrelated files are
preserved. Dry runs write nothing for every stage selection. An oversized optional owner
crosswalk logs a warning and continues without owner reporting.

## Tables

| File | Cut | Basis |
|---|---|---|
| `age_class_overall.csv` | the run | `sampling_weight` |
| `age_class_by_forest_type.csv` | pine / oak-pine / hardwood / nonstocked | `sampling_weight` |
| `age_class_by_forest_type_detail.csv` | FIA forest types inside those groups | `sampling_weight` |
| `age_class_by_state.csv`, `age_class_by_county.csv` | area × forest type | `sampling_weight` |
| `age_class_by_owner.csv` | owner class | `owner_acres` |
| `age_class_by_owner_forest_type.csv` | owner class × forest type | `owner_acres` |
| `age_class_by_management_type.csv` | upland vs. streamside zone × forest type | `owner_acres` |
| `mean_age_by_forest_type.csv`, `mean_age_by_owner.csv` | acre-weighted mean age per year | both |

Every row is `Year, <cut…>, age_class, age_class_label, acres, cases, share, weight_basis`.
`share` is computed *within* a cut, so it reads as that cut's composition; `acres` is what
totals across cuts.

## Figures

`age_class_by_forest_type` (the headline, two snapshot years side by side),
`age_class_over_time` (stacked area, age class on one hue light-to-dark),
`age_class_by_owner`, `age_class_by_management_type`, `age_class_by_state`,
`age_class_by_county` (small multiples, stacked by forest type), and the two
`mean_age_*` trajectories.

Encoding rules, so the set stays readable as one system: age class is ordered, so it is the x
axis, and where it has to be a color it takes one hue light-to-dark rather than a categorical
cycle. Forest type is an identity, so it is the color — three hues validated as a set (worst
all-pairs CVD ΔE 9.2, normal-vision ΔE 24.0), assigned in a fixed order that does not change
when a cut has no oak/pine acres. Nonstocked is a neutral gray because it is the absence of a
type, not a fourth one. Owner classes (nine) and counties (up to sixty) are faceted rather
than stacked onto one axis, well past what any categorical palette can separate; each facet
carries its own y axis with its total in the panel title, because the largest owner class
holds a hundred times the acreage of the smallest. Nothing here has two y scales.

The pine hue sits at 2.74:1 against a white surface, under the 3:1 bar, so the CSV written
beside each figure is the accessibility relief the palette requires — not an extra.

## Identifier precision

`Stand_CN` is a FIA control number up to 19 digits and SQLite hands a REAL-typed column back
as a float, which reformats it into a key that matches nothing (`9.87654321098765e+18`). The
reader casts on the stored value and normalizes through `pipeline.ids.as_id_series`, which
raises rather than pass on a plausible-looking corrupt key. The same applies to `PLT_CN` on
the crosswalk side. See [`../ids.py`](../ids.py) and [`../../AGENTS.md`](../../AGENTS.md).

## Verification

```bash
uv run pytest tests/test_s6_fvs_outputs.py
```

The tests build a miniature `FVSOut.db` in a tmpdir and run every stage against it, so they
need no data drive. One test reaches for the real five-county run and skips when neither the
drive nor the R2 mirror answers.
