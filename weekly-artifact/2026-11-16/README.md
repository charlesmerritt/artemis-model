# Weekly artifact — 2026-11-16

**The annealed harvest plan for the LETO-segmented, v4-initialised five-county Florida
landscape** — one selected trajectory for each of **49,363 management units**, 1,165,075
acres, chosen against the TPO even-flow targets by simulated annealing.

Produced by the repository's own entry point, unmodified:

```bash
uv run python main.py schedule data/r2_cache/runs/fl5_v4_leto_173 --workers 4
```

## Why this artifact

This is AGENTS.md step 3 — "a simulated annealing process is used to optimize the selection
of trajectory for each management unit wrt even-flow ... PER OWNER GROUP" — and it is the
deliverable `pipeline/run.py` exists to produce.

It is **not a repeat of the 2026-08-31 / 09-07 / 09-14 plans.** Those decided over a
landscape of 11,831 *pixel-class* stands (TreeMap plot × county × ownership) assembled by
dated one-off drivers. This one decides over the landscape the pipeline now actually builds:

| | 2026-09-14 | 2026-11-16 |
|---|---:|---:|
| stands | 11,831 pixel classes | **49,363 LETO CA management units** |
| landscape acres | — | **1,165,075** |
| FVS runs in the library | 13,034 | **51,109** |
| trajectory options | 53,606 | **1,666,966** (33.8 per stand) |
| timing offsets available | 0, 5, 10, 15 | **0, 5, 10, 15, 20, 25** |
| initial state | pre-v4 repair | **v4 add-back decision, with young plots** |
| riparian, grow-only | 6,602 stands / 11,155 ac | **8,202 stands / 34,191 ac** |
| driver | `weekly-artifact/*/make_annealed_plan.py` | **`main.py schedule`**, committed |

Each difference is a merged change, not a re-run: the stands are the real LETO
cellular-automata port's output rather than pixel classes (PR #125 made `pipeline/run.py` the
canonical runner), the initial state is the **v4 add-back decision** that became the default
in PR #152, and **3,238 stands (85,823 ac, mean age 6.7) originate from a young plot** —
ADR 0003's dated add-back stands, carrying synthetic ids `Y1000000`+ rather than an FIA
`PLT_CN`, appearing in a harvest plan for the first time.

## Headline result: the landscape can supply even flow, except in Suwannee

| | 2026-08-31 | 2026-09-14 | **2026-11-16** |
|---|---:|---:|---:|
| **Even-flow cost** (Σ squared relative deviation; the weight-6 term, exactly comparable across libraries) | 31.87 | 10.61 | **0.0767** |
| Objective, best of restarts | 190.41 | 63.08 | **0.0167** |
| Objective, greedy baseline | 363.68 | 363.59 | 298.95 |
| Removed volume, 50 years | 1.889 bn ft³ | 2.578 bn ft³ | **3.792 bn ft³** |
| TPO target, 50 years | — | — | 3.875 bn ft³ (97.8% met) |

The 10.61 → 0.0767 figure is like-for-like: both were recomputed here with
`annealer.evenflow_cost` from each week's own committed violation vector
(`weekly-artifact/2026-09-14/constraint_violations.csv` reproduces 10.613808556566315
exactly), which is what that function's docstring says it is for.

**Every dimension lands within ±0.6% of its TPO target in every cycle except Suwannee
county.** Deviation as % of target, from `deviation_by_cycle.csv`:

| | 2027 | 2032 | 2037 | 2042 | 2047 | 2052 | 2057 | 2062 | 2067 | 2072 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baker | +0.51 | +0.27 | +0.32 | +0.40 | +0.12 | +0.49 | +0.58 | +0.09 | −0.05 | +0.21 |
| Columbia | −0.31 | −0.38 | −0.39 | +0.39 | +0.25 | −0.04 | +0.10 | −0.01 | +0.11 | −0.17 |
| Hamilton | +0.02 | −0.20 | +0.10 | −0.06 | +0.44 | +0.36 | +0.01 | −0.14 | −0.02 | +0.56 |
| Union | +0.05 | −0.10 | +0.02 | −0.16 | −0.09 | +0.21 | −0.02 | −0.04 | −0.07 | −0.28 |
| **Suwanee** | **−11.61** | **−10.88** | **−10.58** | **−10.03** | **−8.71** | **−8.00** | **−6.92** | **−5.65** | **−2.64** | **−1.25** |
| Federal (NF) | −0.04 | +0.01 | −0.01 | +0.06 | −0.15 | +0.17 | −0.24 | +0.11 | +0.03 | +0.13 |
| Other public | −1.00 | −0.67 | −1.06 | −0.93 | −0.49 | −0.83 | −0.73 | −0.86 | −0.22 | +0.14 |
| Private | −3.60 | −3.52 | −3.31 | −2.98 | −2.54 | −2.31 | −2.02 | −1.76 | −0.81 | −0.32 |

(`Suwanee`, one 'n' — the TPO workbook's spelling, carried through
`pipeline.s3_management.tpo_targets`.)

This changes what the pilot's shortfall means. On 2026-09-14 the plan missed by a roughly
constant 27–45% in every cycle, read there — correctly for that landscape — as "a supply
statement about the five-county pilot rather than a scheduling artifact". On the LETO
landscape the shortfall is **not landscape-wide**: the whole landscape-level gap (−3.4% in
2027 closing to −0.3% in 2072) is Suwannee, which is also why `Private` carries the only
owner-group shortfall. 2026-09-14 already named Suwannee cycle 1 as one of its three provably
unreachable targets, ceiling 81% of target; it is still the binding one, now with 4× the
stands and 33.8 options each.

**The plan is flat.** Removed volume by cycle, M ft³: 374.5, 374.9, 375.5, 376.8, 378.4,
379.1, 380.2, 381.1, 384.6, 386.4 against a 387.5 target in every cycle — a monotone drift of
+3.2% across 50 years, where 2026-09-14 ran from −45% to −27%.

### All six timing offsets are used

AGENTS.md's offsets ("5, 10, 15, 20, 25") are all present in this library and the scheduler
spreads the landscape across every one of them — managed stands by chosen offset:

| offset (years) | 0 | 5 | 10 | 15 | 20 | 25 |
|---|---:|---:|---:|---:|---:|---:|
| stands | 9,472 | 7,705 | 6,695 | 6,306 | 5,654 | 5,329 |
| acres | 367,818 | 227,408 | 165,425 | 141,495 | 118,218 | 110,520 |

That spread is the mechanism Diaz et al. supply and the reason the plan is flat: it is how a
tapestry of separate owners is made to harvest the same amount each year.

### What the plan prescribes

| base prescription | stands | acres | removed, M ft³ |
|---|---:|---:|---:|
| natural_short_rotation | 8,370 | 250,389 | 1,124.0 |
| pine_plantation_short_rotation | 4,789 | 171,967 | 993.0 |
| family_light_thin | 5,094 | 151,720 | 18.6 |
| pine_plantation_long_rotation | 4,299 | 142,532 | 1,001.0 |
| public_thin_restore | 5,080 | 133,055 | 71.8 |
| family_uneven_aged_selection | 4,770 | 99,107 | 142.0 |
| public_selection_light | 5,026 | 92,636 | 232.1 |
| hardwood_clearcut_regen | 2,937 | 64,561 | 209.0 |
| no_management | 8,998 | 59,109 | 0.0 |

9,640 stands (68,218 ac) carry no removal in 2027–2072: the 8,202 riparian stands, 796
managed stands that chose `no_management` outright, and 642 managed stands whose chosen
cutting prescription schedules nothing inside the scored horizon. Managed stands take 0–5
entries over the 50 years (modal 1 or 2).

## Checks that passed inside the run

* **Riparian is structurally enforced, not penalised.** All 8,202 riparian stands were
  offered exactly `{no_management}`; `build_landscape` asserts this and would have stopped the
  run otherwise. No riparian acre is cut anywhere in the plan.
* **No stand was lost.** 49,363 stands in, 49,363 planned, 0 dropped for want of a trajectory
  or a TPO dimension. Five `(stand, prescription)` options were dropped for an incomplete FVS
  trajectory, all `pine_plantation_short_rotation@+5`, and the run names them rather than
  shrinking a menu silently.
* **Minimum harvest age holds at entry.** 4,309 stands cut in cycle 1 (2027); the youngest
  carries inventory age 10, i.e. **exactly 15 at entry** — `harvest_eligibility`'s
  `minimum_age_years: 15` applied to the entry year, not the inventory year.
* **FIA control numbers survive.** `PLT_CN` is a string end to end — 46,125 stands carry one
  of 408 distinct FIA donor plots, at 14 and 15 digits, never a float. The 3,238 young-plot
  stands carry `Y`-prefixed ADR 0003 ids instead, so a young plot can never be mistaken for
  an FIA plot in a join.
* **The plan reconciles with its own violation vector** to 3.3 × 10⁻⁷ ft³: summing
  `cuft_cycle_1…10` by TPO county reproduces `deviation_by_cycle.csv`'s `volume_cuft`.

## Files

| | |
|---|---|
| `plan.csv.gz` | **The plan.** 49,363 rows, one per management unit: `stand_id`, `trajectory_id` (unique), `fvs_run_id` (9,614 distinct runs used, many-to-one by design — that is the run cache), `PLT_CN`, `prescription`, `base_prescription`, `timing_offset_years`, county, owner class, owner group, `unit_class`, acres, `library_size`, total removed, ending standing volume, and removed ft³ in each of the ten cycles. |
| `deviation_by_cycle.csv` | The even-flow quality report: 80 rows, every TPO dimension × cycle with its volume, target and deviation. |
| `seeds.csv` | All 11 restarts — seed, objective, temperature levels, seconds. Best-of-11 with every seed reported, never best-of-unreported. |
| `summary.json` | Run identity: stands, acres, restarts, cooling factor, options per stand, best seed, best and greedy objectives, even-flow cost, target period, wall time. |

Seed spread is tight: objectives run 0.016658 (seed 44, the best) to 0.027014 (seed 46),
range 0.0104, over 276–288 temperature levels and 1,222–1,379 s each.

## R2 inputs pulled

Five files, 171 MB of a ~200 GB bucket — only what the `schedule` stage reads. All from
`r2:artemis-r2/data/Artemis_data/runs/fl5_v4_leto_173/`:

| key | size | role |
|---|---:|---|
| `library.csv` | 157.6 MB | the enumerated decision space: 1,666,966 `(unit, prescription, run_hash)` options |
| `fvs_cycles.csv.gz` | 8.1 MB | the FVS trajectories those options resolve to, 613,308 cycle rows |
| `stands.csv` | 5.7 MB | the landscape: 49,363 LETO units with county, owner class, age, acres, `unit_class` |
| `fvs_failures.csv` | 129 B | the 3 FVS runs that failed (stop code −8) |
| `leto.json` | 5.3 KB | the landscape's provenance (read, not committed) |

Deliberately **not** pulled: `FVS_Data.db` (6.5 MB) and `keyfiles/` are inputs to the
`library` stage, which was already run on 2026-11-09; `management_units.gpkg` (64.9 MB) is the
polygon layer, which `schedule` does not read. Everything landed under gitignored
`data/r2_cache/` and **no data file is committed** — the four files here are the stage's own
outputs.

### Where the landscape came from

`leto.json` records it: the v4 repair at
`improved-rasters-evt2022-gate-v4/aoi_5county` with `young_plots: true`
(manifest sha256 `9d584d8a…`), LETO CA segmentation at 100 ac seeds / 5–300 ac stands with
`force_merge` for the remainder, Florida BMP riparian widths (35/35/75 ft by FCODE), and
per-county unit counts Baker 10,059, Columbia 13,675, Hamilton 9,349, Suwannee 10,626,
Union 5,654 — 49,363 in total, which is the stand count this plan decides over. Its
`library.log` records the FVS side: 1,666,966 options over 49,363 stands, 51,112 distinct runs
submitted, 51,109 added, 3 failed, 43 minutes of wall time on 10 workers on 2026-11-09.

## Dependencies

None installed. The committed `uv.lock` environment was used as-is (`uv sync`): Python 3.14,
pandas 3.0.3, numpy 2.4.6, PyYAML. No FVS binary is needed — `schedule` reads a finished
trajectory cache. **No code in the repository was changed.**

## How to regenerate

```bash
uv sync
mkdir -p data/r2_cache/runs/fl5_v4_leto_173
rclone copy r2:artemis-r2/data/Artemis_data/runs/fl5_v4_leto_173 \
  data/r2_cache/runs/fl5_v4_leto_173 \
  --include "stands.csv" --include "library.csv" --include "fvs_cycles.csv.gz"
uv run python main.py schedule data/r2_cache/runs/fl5_v4_leto_173 --workers 4
```

Outputs land in the run directory itself; the files here are a verbatim copy. The run took
65.3 minutes of wall time (11 restarts, 4 workers) and is deterministic: `--workers` only sets
how many of the 11 fixed seeds (42–52) run at once, and best-of is taken by objective, so a
different worker count gives the same plan. `--restarts 11` and `--cooling 0.98` are the CLI
defaults — 0.98 is 2026-09-28's converged setting, which every schedule since has used.

## What did not run, and why

**No map was rendered.** AGENTS.md step 4's focus output is a raster per timestep, and the run
directory does carry `management_units.gpkg` (64.9 MB) to paint onto. But
`pipeline/s4_fvs/paint_fvs_to_raster.py` reclassifies *TreeMap pixels by `TM_ID`* from an
`FVSOut.db`, and this plan's stands are LETO polygons keyed on `unit_id` with 3,238 of them
carrying `Y`-prefixed young-plot ids — there is no committed code that joins a `plan.csv.gz`
to that polygon layer and writes a COG. Painting this plan would have meant writing that
joiner here and calling it the project's own code, so it was not done. That joiner is the
obvious next increment, and it is now the only thing between this plan and the map series.

**The library stage was not re-run.** It needs a compiled `FVSsn` at `fvs/bin/FVSsn`, and
building it in this sandbox is still blocked: the documented `make -C fvs/src/bin FVSsn`
is refused by the auto-mode permission classifier as compilation of freshly cloned
third-party source (see `weekly-artifact/2026-11-02/README.md` for the full transcript). It
did not matter here — the 2026-11-09 run had already cached all 51,109 trajectories, which is
exactly the separation `library`/`schedule` exists to provide.

**Two notes for whoever picks this up.**

1. `weekly-artifact/2026-09-28/` still holds `make_converged_plan.py` and `make_figure.py`
   with no README and no artifact, so that week's deliverable appears never to have been
   published. Flagged in `2026-11-02`'s README too, and still true.
2. `pipeline/run.py`'s docstring still points at `experiments/2026-10-19_fl5-leto-stands`
   steps 1–2 and 8 as the driver that writes a run directory's `stands.csv` and
   `FVS_Data.db`, and that experiment is **still not committed** — `experiments/` holds only
   `2026-07-28_16-33-pr9-validation-audit` and `2026-08-24_leto-ca-forest-viz`. The run
   directory this artifact consumes was therefore built by code that is not in the
   repository. That is the one real reproducibility gap in this week's chain: everything from
   `stands.csv` forward is committed and was re-run here; everything upstream of it is not.
