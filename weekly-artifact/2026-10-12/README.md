# Weekly artifact — 2026-10-12

## Artifact

**The converged plan, and the measurement of how much of last month's plan was search error.**
40 annealing searches over the *identical* 13,034-run FVS library `2026-09-14` published, two
cooling factors × twenty restarts, and the plan that falls out of the best cell.

This closes the job `2026-09-14` named first, in its own words:

> **Give the search the restarts the bigger space needs.** Seed spread went from 0.05% to 5.5%
> of the objective. Nothing about the library needs to change — more restarts, or a cooling
> factor nearer 1, or both, and the numbers above are a floor on what this decision space can
> do rather than its best.

The drivers for it were committed on 2026-09-28 (`48b2632`, "Add the 2026-09-28 convergence
sweep drivers and their tests"), whose message ends: *"The artifact tables and README follow
once the sweep finishes."* They never did — that run shipped code and tests and no output.
**This week runs those committed drivers unmodified and publishes what they produce.** Not a
line of `make_converged_plan.py` or `make_figure.py` was changed; the only new files here are
their outputs and this note.

| File | What it is |
|---|---|
| `convergence.png` | The four-panel figure: where the restarts stop paying, how wide the seeds are, the plan beside `2026-09-14`'s, and what the extra search cost in CPU. |
| `annealed_plan.csv` | **The plan.** One row per stand: `stand_id → trajectory_id`, `base_prescription`, `timing_offset_years`, county, owner class, acres and removed volume in each of the ten cycles. 11,831 rows. |
| `arm_summary.csv` | **The 2×2.** Cooling factor {0.95 config, 0.98} × restarts {5 config, 20}, from one set of 40 searches — the cells nest, so best-of-5 is the first five seeds of best-of-20. |
| `restart_curve.csv` | Best-of-first-R against R, every seed's own objective and its wall time. The table behind panel (a). |
| `seed_spread.csv` | All 40 searches: calibrated `T₀`, temperature levels, moves proposed/accepted, accept rate, seconds. |
| `solution_quality.json` | The §6 quality report, plus `reproduction_of_20260914` and `library_identical_to_20260914` — the two checks described below. |
| `comparison_to_20260914.csv` | This plan beside `2026-09-14`'s on 18 measures, every "before" read from that artifact's own committed files. |
| `harvest_by_cycle.csv` | Harvest per cycle against the TPO target, with the deviation. |
| `attainable_envelope.csv` · `constraint_violations.csv` · `prescription_mix.csv` · `plan_by_dimension.csv` · `timing_offsets_chosen.csv` | The envelope, the violation vector, and the plan summarised four ways — built exactly as `2026-09-14` built them, so the two artifacts' tables are comparable row for row. |

**Why this artifact, and why it is not a repeat of `2026-09-14`.** That artifact changed the
*decision space* and could not say how much of its remaining objective was the landscape and
how much was a search that had stopped converging — its own seed spread (5.5% of the
objective) was larger than any plausible gain. This one changes the *search* and nothing else,
over a library asserted identical, so the difference is attributable to search alone. The
answer is a number nobody had: **0.8%**.

**Where the files live.** The drivers write into their own directory,
`weekly-artifact/2026-09-28/`. The outputs were moved verbatim into this dated folder and not
regenerated anywhere else, so `convergence.png` carries the driver's own `(2026-09-28)` label
in its title. The run date is 2026-10-12.

## Headline results

| | 2026-09-14 | 2026-10-12 | |
|---|---:|---:|---|
| **Objective, best** | 63.077 | **62.571** | −0.80%; same library, so exactly comparable |
| Even-flow cost (Σ squared relative deviation) | 10.614 | **10.529** | −0.8% |
| Objective, greedy baseline | 363.594 | 363.594 | identical, as it must be |
| Objective, random selection (mean of 5) | 276.369 | 276.369 | identical |
| Searches run | 5 | **40** | 2 cooling factors × 20 restarts |
| Seed spread at 5 restarts, cooling 0.95 | 3.483 | 3.483 | reproduced to the last digit |
| Seed spread at 5 restarts, cooling 0.98 | — | **1.070** | −69% per-search variance |
| Targets proven unreachable (of 80) | 3 | 3 | unchanged — a library limit, not a search limit |
| Removed volume, 50 years | 2.578 bn ft³ | 2.583 bn ft³ | +0.2% |
| Worst cycle, % below TPO target | −44.6% | −44.9% | cycle 1, both |
| Deviation band across the ten cycles | 17.7 pp | **16.6 pp** | −28.3% … −44.9% |
| CPU spent searching | 4.3 min | **62 min** | the "before" is this sweep's baseline cell; 4 workers, 956 s wall |

### 1. The search was already close. Search error was 0.8% of the objective.

| arm | objective (best) | seed spread | % of best | CPU |
|---|---:|---:|---:|---:|
| cooling 0.95, 5 restarts (**`2026-09-14`'s setting**) | 63.077 | 3.483 | 5.5% | 4.3 min |
| cooling 0.95, 20 restarts | 62.575 | 3.985 | 6.4% | 17.6 min |
| cooling 0.98, 5 restarts | 63.033 | 1.070 | 1.7% | 11.0 min |
| cooling 0.98, 20 restarts (**best**) | **62.571** | 2.309 | 3.7% | 44.0 min |

Both levers work, and they work on different things:

- **Restarts buy the tail.** At the config's cooling factor, the sixth seed (47) found 62.575
  and no seed in the next fourteen beat it. The curve is flat from restart 6 to restart 20 —
  `restart_curve.csv`, panel (a).
- **Slower cooling buys consistency, not depth.** It cuts per-search spread by 69% at equal
  restarts (3.483 → 1.070) while barely moving the best (63.077 → 63.033). It is the lever that
  makes a *single* search trustworthy, which matters more for a statewide run than best-of-20
  does.
- **Together they bought 0.8%.** 62.571 against 63.077, for 10× the baseline cell's CPU
  in the winning cell alone (44.0 min against 4.3 min) and 14× across the whole sweep.

The honest reading of `2026-09-14`'s seed spread is therefore the opposite of what it feared.
That spread was real, but it was variance around a value already within 1% of what 40 searches
can reach. **The 2026-09-14 plan was not materially search-limited, and neither is this one.**
What remains between this objective and the relaxation bound (62.571 against 0.195) is the
decision space and the landscape, not the annealer.

### 2. The objective is a plateau: 59% of the stands that have a choice moved, for 0.8%.

Comparing the two plans stand by stand:

| | stands |
|---|---:|
| Stands in the plan | 11,831 |
| Stands with more than one trajectory to choose from | 5,229 |
| **Stands choosing a different trajectory than `2026-09-14`** | **3,081** (59% of those with a choice) |
| — of which changed base prescription | 1,206 |
| — of which kept the prescription and moved its start year | 1,875 |

A quarter of the whole landscape, and three in five of the stands that have any choice at all,
are rearranged for a 0.8% objective gain. That is a property of the objective, not an artifact
of the search: at the landscape level, many very different allocations are near-equivalent on
even flow, because what the objective reads is ten county-and-owner aggregates per cycle and
there are thousands of ways to hit them.

This matters for the next stage more than the 0.8% does. **A single "optimal" plan is not
identified by this objective**, so the map ARTEMIS exists to produce is one draw from a large
near-optimal set. Nothing downstream currently says which draw it is, and two plans that score
the same can differ on things nobody has priced — contiguity, haul distance, how concentrated
the clearcuts are in any one place. The prescription mix barely moves across those 3,081
changes (`no_management` 6,833 → 6,825 stands; `pine_plantation_long_rotation` 1,126 → 1,146;
`public_thin_restore` 350 → 303), which is consistent with the rearrangement being mostly
*which* stand takes a given prescription and *when*, rather than a change of silvicultural
policy.

### 3. The cross-week reproduction is exact, on a different machine

This container compiled `FVSsn` from source (FVS `FS2026.3`, gfortran 13.3) and re-ran all
13,035 trajectories. Three independent checks, all reported in `solution_quality.json`:

| Check | Result |
|---|---|
| Rebuilt FVS library vs `2026-09-14/trajectory_index.csv` | **13,034 runs, max \|diff\| exactly 0 cuft/ac** (this one is a hard gate — the driver refuses to plan over a drifted library) |
| Baseline cell (cooling 0.95, 5 restarts) objective vs published | 63.076986320415 vs **63.07698632041486** (\|diff\| 1.4e-13) |
| Baseline cell *plan* vs published, stand by stand | **11,831 of 11,831 stands choose the same trajectory** |

Independently of the drivers' own checks: after the library rebuild, `git status` reported **no
change to any committed file** under `weekly-artifact/2026-09-14/`. The rebuild rewrote that
artifact's tables byte for byte.

The one FVS failure is the same one, on the same trajectory:
`473803917489998 / hardwood_clearcut_regen@+0`, SIGFPE, identical to the set committed in
`2026-09-14/fvs_failures.csv`. The batch fails closed on it and compares the exclusion *set*
key by key, not just its size, before publishing.

### 4. What did not change, and is not search

- **Three targets remain provably unreachable** from this library: county cycle 1, county cycle
  10, and one owner group in cycle 1 (`attainable_envelope.csv`). No amount of searching
  reaches them; two of the three are the cycle-1 problem `2026-09-14` flagged, where an
  offsets-only grid is powerless by construction because every offset moves entries *later*.
- **Every cycle is still 28–45% below the TPO target.** The band tightened by 1.1 pp. The level
  is a decision-space and landscape question (five counties, 11,831 stands, the eligibility
  rules), not a search question.
- **Spatial penalties remain structurally unavailable.** `adjacency_greenup` and
  `max_opening_size` need polygon adjacency that a pixel-class landscape (TreeMap plot × county
  × ownership) cannot supply, and the `block` move goes with them. Still the largest caveat on
  the plan, and still untouched.
- **Riparian no-entry is structural**, verified again here: 6,602 riparian stands, every one of
  them with `{no_management}` as its entire library.

## R2 inputs pulled

One file, 1.08 GB:

| R2 key | Local path | Bytes |
|---|---|---:|
| `r2:artemis-r2/data/Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db` | `data/interim/stage/FIA_5county_consolidated.db` | 1,078,423,552 |

That is the whole data requirement: it supplies `FVS_STANDINIT_PLOT` / `FVS_TREEINIT_PLOT` for
the 676 donor plots (671 of them with a species SDI table). Nothing else was downloaded, and **no downloaded data is committed** —
everything lands under gitignored `data/`.

## Exact commands

```bash
# 0. Dependencies: gfortran/make/cmake (apt) to compile FVS. No change to pyproject.toml or
#    uv.lock; the committed environment was used as-is (Python 3.14, pandas, PyYAML,
#    matplotlib).
apt-get install -y gfortran make cmake
uv sync

# 1. Build FVS Southern variant (the container has no FVS; the repo expects one at fvs/bin/)
git clone --depth 1 https://github.com/USDAForestService/ForestVegetationSimulator.git fvs/src
git -C fvs/src submodule update --init --depth 1 volume/NVEL
sed -i 's/FVSbc_sourceList.txt/FVSsn_sourceList.txt/' fvs/src/bin/CMakeLists.txt
sed -i 's/-ffpe-trap=invalid,zero,underflow,overflow,denormal/-ffpe-trap=invalid,zero,overflow/' \
  fvs/src/bin/makefile
make -C fvs/src/bin FVSsn -j4
mkdir -p fvs/bin && cp fvs/src/bin/FVSsn fvs/src/bin/FVSsn.so fvs/bin/

# 2. The one input
rclone copyto r2:artemis-r2/data/Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db \
  data/interim/stage/FIA_5county_consolidated.db

# 3. Rebuild 2026-09-14's library, unmodified. 13,035 runs, 4 workers, ~12 min here.
#    Fails closed: the first run stops at the exclusion gate reporting the one SIGFPE, and
#    --reuse-raw republishes from cached FVS output rather than re-running the batch.
uv run python weekly-artifact/2026-09-14/make_timing_library.py --workers 4
uv run python weekly-artifact/2026-09-14/make_timing_library.py --workers 4 \
    --reuse-raw --allow-excluded-runs=1

# 4. THE ARTIFACT: the sweep (40 searches, 956 s wall / 3,698 s CPU on 4 workers) and the figure
uv run python weekly-artifact/2026-09-28/make_converged_plan.py \
    --restarts 20 --coolings 0.95,0.98 --workers 4
uv run python weekly-artifact/2026-09-28/make_figure.py

# 5. The drivers' own tests
uv run python -m pytest tests/test_weekly_artifact_20260928_convergence.py -q   # 23 passed

# 6. Outputs were moved from weekly-artifact/2026-09-28/ into weekly-artifact/2026-10-12/
```

## How to regenerate

Run steps 0–4 above. Output is deterministic: FVS is deterministic, the annealer takes its seed
from `config/projection.yaml` (`harvest.random_seed: 42`, so seeds 42–61 here), and a search is
a pure function of (library, cooling factor, seed) — the four forked workers change nothing but
wall clock. Same library + same seeds + same weights gives the same plan, as §3 above
demonstrates across two machines.

Intermediates, all gitignored, under `data/interim/timing_library/` (116 MB: the FVS input
database, 13,035 keyfiles, the raw `FVS_Summary2` cache, `trajectory_cycles`, the expanded
library, `excluded_runs.csv`) and `data/interim/stage/` (the FIA database). Delete the former to
force the batch to re-run. `FVSSN_BIN` overrides the binary location.

## What this hands the next run

1. **Stop tuning the search; the 0.8% is spent.** Twenty restarts at cooling 0.98 cost 10× the
   CPU of the published setting and bought less than one percent. For the statewide runs the
   useful setting out of this sweep is **cooling 0.98 at 5 restarts** — within 0.7% of the best
   anything found, at a quarter of the CPU, and with a third of the per-search variance, which
   is what makes a single search per unit defensible when there are far more units than there
   are restarts to spend on them.
2. **Decide how to choose among near-equivalent plans** (§2). 3,081 stands move for 0.8%, so
   the objective does not identify one map. Either price something that currently is not priced
   — adjacency, opening size, haul distance — or publish the near-optimal *set* and say
   explicitly which draw the maps show. This is now the biggest open question about the output,
   and it was invisible before this sweep because there was only ever one plan to look at.
3. **Decide whether negative offsets are admissible**, unchanged from `2026-09-14` and now the
   only remaining way to reach two of the three unreachable targets: both are in cycle 1, where
   an offsets-only grid can only move entries later. A policy question, not a search one.
4. **Land the Phase 2.3 unit × stand crosswalk**, unchanged and still the largest caveat: it
   turns pixel classes into polygons and switches on adjacency, green-up, opening size and the
   `block` move — the whole LAMPS half of the architecture, still dark. §2 above is a second,
   independent reason to want it.
5. **Re-check `pipeline/`'s status lines**, unchanged from the last three artifacts.
   `harvest_scheduler.py`, the README's "Current implementation" and
   `notes/trajectory-library-and-annealing.md` still say the annealer is not built, and there
   are now three weekly drivers that build it. Promoting them into `pipeline/` was again
   deliberately not done here: an artifact PR should not quietly become an architecture PR.
