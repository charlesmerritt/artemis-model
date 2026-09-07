# Weekly artifact — 2026-09-07

## Artifact

**The timing-offset grid, and the plan that comes out of a decision space that finally
carries *when*.**

`weekly-artifact/2026-08-31` produced the first annealed plan and, in the same run,
measured why it could not track its targets: 47 of 80 (dimension × cycle) targets lay
outside the range the library could reach *at any selection*. It named the cause and the
fix in its closing section:

> **Add the timing-offset grid to the library** (§4). It is the direct cause of 47
> unreachable targets and of an empty final cycle, it is already specified, and it costs
> FVS runs rather than scheduler work. Nothing else in the plan will improve much until
> the decision space carries "when".

This artifact does exactly that and re-runs the plan over it. Every cutting
`(plot, prescription)` run is expanded into four timing variants — the schedule as
resolved today, and the same schedule delayed 5, 10 and 15 years, which is Diaz et al.'s
own grid as §1.2 of the design note quotes it. The library goes from 22,317 rows to
**53,458**, and the median upland stand from **3 trajectories to 9** — inside §4's target
band of 6–12 for the first time.

The band is not reached everywhere, and the reason is arithmetic rather than a shortfall
in the grid: a stand's library is (its eligible cutting prescriptions × 4 delays) +
`no_management`, so the 910 upland stands whose ownership class admits only *one* cutting
prescription cap at five however many delays are offered. 3,381 stands land at 9 and 938
at 13; the remaining 910 sit at 5 or below. Widening those menus is a
`config/management_regimes.yaml` question, not a timing one.

| File | What it is |
|---|---|
| `timing_offsets.png` | The four-panel figure: both weeks' attainable ceilings against the plan, which targets moved, the delays the scheduler chose, and solution quality side by side. |
| `envelope_delta.csv` | **The headline table.** Every (dimension × cycle) target with its attainable ceiling before and after, and which of four states it moved between. 80 rows. |
| `offset_grid.csv` | What each delay cost in FVS runs and where it put the wood — runs, harvest cycles, mean removed volume, median first and last harvest year. |
| `offset_mix.csv` | The delays the scheduler actually *chose*, by stands, acres and volume. |
| `annealed_plan.csv` | **The plan.** One row per stand: `stand_id → trajectory_id`, now split into `base_prescription` and `offset_years`, with county, owner class, acres and removed volume in each of the ten cycles. 11,831 rows. |
| `trajectory_index.csv` | §5's narrow index, one row per FVS run, keyed by `fvs_run_id` and carrying the two halves of the key. 12,981 rows. |
| `trajectory_harvest_by_cycle.csv` | `harvest_cuft[cycle]` for the whole timed library. 142,791 rows. |
| `attainable_envelope.csv` · `constraint_violations.csv` · `harvest_by_cycle.csv` · `prescription_mix.csv` · `plan_by_dimension.csv` · `seed_spread.csv` · `solution_quality.json` | The §6 quality apparatus, same schema as 2026-08-31 so the two weeks diff cleanly. |
| `fvs_failures.csv` | The one trajectory FVS could not simulate — the same one as last week. |
| `make_offset_library.py` · `make_annealed_plan.py` · `make_figure.py` | The three drivers. |

**Why this artifact, and why it is not a repeat of 2026-08-31.** That run answered "what
plan does the annealer choose?". This one answers a different question the previous run
posed and could not answer: **does the attainability frontier move when the decision space
gains a timing dimension, and by how much?** Same objective, same config, same seeds, same
greedy baseline, same targets — one thing changed, and it is measured against last week's
committed numbers rather than against a memory of them.

## Headline results

### The frontier moved, and by most of the way

| | 2026-08-31 | 2026-09-07 |
|---|---:|---:|
| Targets **proven unreachable** (of 80) | 47 | **10** |
| — recovered | | 37 |
| — newly unreachable | | **0** |
| Annealed objective (lower is better) | 190.41 | **94.54** |
| Relaxation bound | 153.74 | 48.11 |
| Greedy baseline | 363.676 | 363.679 |
| Random (mean of 5) | 379.75 | 293.82 |
| Library rows (stand × trajectory) | 22,317 | 53,458 |
| Median trajectories per upland stand (§4 asks 6–12) | 3 | **9** |
| — most / fewest | 4 / 1 | 13 / 2 |

**Even flow is the thing that actually improved, and it is the thing the offsets were for.**
Across the nine cycles that can carry a harvest, the plan's deviation from target went from
a range of −16% to −92% (standard deviation **29.0** percentage points) to a range of
−22% to −42% (standard deviation **6.8**). The plan no longer lurches; it sits at a
consistent three-quarters of target. Total removed volume over the 50 years rose from
1.89 to **2.45 billion ft³**, +30%.

| Cycle | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Deviation from target, 2026-08-31 | −19% | −36% | −19% | −35% | −85% | −16% | −68% | −92% | −42% | −100% |
| Deviation from target, **this week** | −42% | −38% | −33% | −29% | −28% | −25% | −28% | −22% | −22% | −100% |

**Cycle 1 got worse, and that is the objective working as specified.** `evenflow_target`
prices squared *relative* deviation summed over every dimension and cycle, so trading a
19% miss in 2027 for turning an 85% and a 92% miss into 28% and 22% is a large net gain.
The plan is flatter because volume moved out of the early cycles, which is what a delay
grid lets it do.

**The scheduler uses the timing it was given.** 52% of cutting acreage takes a delayed
variant — 158k acres at +5 years, 133k at +10, 153k at +15, against 410k left as
resolved. A grid the search ignored would show every acre at offset 0.

It also stops using *not cutting* as a timing device. Last week 115,561 upland acres took
`no_management` by choice, because deferring a cut entirely was the only way to keep a
binding cycle target from being overshot. That is now **61,366 acres** — the scheduler
would rather cut later than not at all, once "later" is on the menu.

### The 10 that did not move, and why timing cannot fix them

This is the part worth reading, because the residual has exactly two causes and neither is
the search.

**Eight of the ten are cycle 10 (2072), and they are structural.** 2026-08-31 read that
cycle's zero ceiling as:

> No prescription in the enumerated library schedules an entry in 2072 at all.

**That diagnosis was wrong, and this run corrects it.** Twelve library rows (6 FVS runs,
12 stands, 167 acres) *do* schedule a clearcut in 2072. FVS accepts the keyword and never
executes it. The projection is ten five-year cycles from 2022 — 2022→2027 … 2067→2072 — so
2072 is the terminal *report* year and no cycle begins there. Measured directly rather
than inferred: the same donor plot clearcut at 2062 and at 2067 removes volume in those
years; clearcut at 2072 removes nothing and the run still ends normally at 2072. Across
all 12,981 trajectories, **no trajectory at any offset removes volume in cycle 10.**

So cycle 10 is empty because of the projection's shape, not the library's, and **no timing
grid can fill it.** Filling it means eleven cycles in `config/projection.yaml`, which is a
scenario change rather than a library one and is deliberately not made inside a
comparison.

**The other two need entries *earlier*, not later.** Suwanee county in cycle 1 tops out at
81% of its target and the `Other public` owner group at 62%, in both runs, unchanged to
the decimal. An offset grid only moves harvests *later*; the first cycle is the one place
it cannot help. Reaching those two needs negative offsets — entries resolved *before* the
age-based schedule puts them — or a shorter minimum harvest age. That is the natural next
increment and it is the same mechanism, pointed the other way.

## Controls: what was held fixed, and how that was checked

The whole artifact is a difference against last week, so the control does more work here
than any single number. It is asserted at three levels, and each one stops the run rather
than warning.

1. **The carved landscape is identical.** 5,240 pre-carve units and 925,097.8 acres in;
   11,831 stands, 22,317 base library rows, 6,602 riparian, 5,229 upland, 913,943.2
   harvestable acres out — every one an equality check against the committed 2026-08-24
   `library_riparian_delta.csv`, before anything else runs.
2. **Offset 0 renders exactly what 2026-08-31 rendered.** This week's driver builds
   operations itself and passes them to `render_keyfile` through its explicit
   `thins=`/`regen=` entry point, so out-of-horizon entries can be dropped before
   rendering; last week used the `params=` path. Every one of the 3,782 unshifted runs is
   rendered *both* ways and the bytes compared. All 3,782 match.
3. **Offset 0 produces exactly the trajectories 2026-08-31 published.** The 3,781
   surviving unshifted runs are compared row by row against the committed
   `weekly-artifact/2026-08-31/trajectory_index.csv` — cycle count, first and last year,
   total removed volume, harvest-cycle count, ending basal area and ending merchantable
   volume. Worst relative difference: **3.1 × 10⁻¹⁶**, which is CSV float round-trip and
   nothing else.

Also unchanged and not re-derived: `config/projection.yaml` (cooling schedule, move
mixture, objective forms and weights, seed 42, five restarts), `config/tpo_targets.yaml`,
the `2013_2024` target period, and the greedy seed from
`pipeline/s3_management/harvest_scheduler.py`.

### The greedy baseline moved by 0.0025, and it is the normaliser, not the baseline

`objective_greedy_baseline` reads 363.678783 against last week's 363.676330. The greedy
*selection* is byte-for-byte the same — the allocator converges in the same 31 passes,
admits the same 3,840 stands, drops the same 386 and leaves the same 83 blocked, and
**chooses no delayed variant at all**, since it resolves defaults through
`assign_prescription`, which returns base prescription ids.

What moved is the objective function. Its standing-volume term is normalised by
`standing_max` — the landscape's own attainable maximum ending volume — which is a
property of the library, and the library grew. Delayed variants cut less inside the
horizon and therefore leave more standing, so `standing_max` rose 0.276%, from
4.982×10⁹ to 4.996×10⁹ ft³. Scoring the *same* greedy choice on a landscape restricted to
offset-0 options reproduces **363.676329686**, last week's figure to every published
digit.

The effect on the total is 6.7×10⁻⁶ relative, far below anything claimed here, but it
means the two weeks' objective values are not on a perfectly identical scale, and that is
worth stating rather than leaving for someone to find. The attainability counts, which are
the headline, are unaffected: they are volumes against targets, with no normaliser.

## Two corrections made to the run rather than worked around

**1. The horizon cutoff was one cycle too generous, and it published 55 phantom options.**
The first batch used "an entry must fall on or before 2072", which is the obvious reading
of a 2022+50 horizon and is wrong for the reason established above. It admitted 55 delayed
clearcuts whose only surviving entry was in 2072: options named for a harvest, carrying an
`entry_years` value, removing nothing. That is precisely the silent data error the
fail-closed batch exists to prevent — the scheduler would have been able to "choose a
clearcut" that never happens.

The cutoff is now `LAST_SIMULATED_ENTRY_YEAR = 2067`, the last year an operation can
actually execute, and the whole batch was re-run rather than patched: 13,035 runs became
12,982, and the 55 are gone. The constant carries the measurement that justifies it.

**2. Offset 0's own phantoms are reproduced, not repaired.** The 12 library rows that
schedule a 2072 clearcut are offset-0 rows — they are last week's library, and last week's
library is this week's control. Filtering them would have moved the baseline the entire
artifact is measured against, so `resolve_variant` takes offset 0 verbatim and applies the
horizon rule only to delayed variants. They are reported here instead, as work for a later
run: they affect 12 stands and 167 acres, and they are trivially fixable once the horizon
question is settled properly.

## Not fabricated

Every number above comes from committed repository code or from FVS output.

- **The grid uses the repository's own resolver.** Delays are applied to the parameters
  `config/regimes.yaml` and `assign_prescription` resolved, and rendered by
  `pipeline.s4_fvs.regime_templates` — the same committed renderer every artifact in this
  series has used. `YEAR_PARAMS` names which parameter of each template is a calendar
  year; a template absent from it raises rather than silently never being delayed, and a
  test asserts every template in the committed enumeration has an entry.
- **A delay is the same silviculture, later.** Every year-valued parameter shifts
  together; proportions, DBH limits and intervals do not move. Tested per template, and
  the "delayed schedule is the delayed prefix of the original" property is tested too, so
  a delay can only lose entries off the end of the horizon, never gain them.
- **The batch fails closed, unchanged from 2026-08-31.** Of 12,982 runs, **12,981
  completed** and one was excluded: `473803917489998 / hardwood_clearcut_regen`, killed by
  SIGFPE in `varmrt.f:176` — the same division by zero in the SN mortality routine on a
  nearly-empty post-clearcut hardwood stand that last week reported. Its three *delayed*
  variants complete normally, so the stand that lost its only cutting option last week has
  one again (`stands_with_a_choice` 5,228 → 5,229). Publication still requires the
  operator to state the exclusion count exactly (`--allow-excluded-runs 1`).
- **Riparian no-entry survives the expansion.** All 6,602 riparian stands still carry a
  library of exactly `{no_management}` — `no_management` has nothing to delay and is
  emitted once per stand — so §3 rule 2's "enforced by the absence of an alternative"
  holds unchanged. Asserted in the driver and tested on a synthetic library.
- **The tests.** `tests/test_weekly_artifact_20260907_offsets.py` adds 86 tests covering
  the naming round-trip, what a delay may and may not change, both horizon rules, the
  regeneration-follows-its-harvest rule, offset-0 render equivalence for every template,
  the expansion over a synthetic library (including that it is additive and leaves
  riparian structural), and the four-way classification in `envelope_delta`.
  `uv run pytest tests/ -q` → **999 passed, 10 skipped**. `uv run ruff check .` clean.

## What is still unavailable, and unchanged by this week

**The two spatial penalties still cannot be evaluated, and are still reported as
unavailable rather than given a manufactured number.** `adjacency_greenup` and
`max_opening_size` need a neighbour relation between stands, and a "stand" here is still a
pixel class (`TreeMap plot × county × ownership`) — a scattered set of pixels across a
county, not a compact polygon. The `block` move goes with them; the mixture renormalises
over `single_stand` and `period_swap`. This is a property of the input, untouched by
anything done here, and it remains the single largest caveat on the plan. It clears when
the Phase 2.3 unit × stand crosswalk lands.

**The search is looser than last week's, and the seed spread says so.** Across the same
five restarts the objective ranged 94.54–96.38 — a spread of **1.84**, or 1.9% of the
objective, against last week's 0.088 (0.05%). The decision space is roughly four times
larger and `iterations_per_temperature` was deliberately not raised, because changing the
cooling budget would have confounded the comparison this artifact exists to make. So the
reported plan is the best of five on a search that has not converged as tightly as last
week's, and the honest reading of 94.54 is "at least this good", not "this is the
optimum". The gap to the relaxation bound is 46.44, most of which is the relaxation's own
looseness rather than search error.

**The bound still runs one way only.** The envelope comes from a relaxation in which every
stand may pick a different trajectory in each cycle and for each dimension at once, which
the real problem forbids. A target *outside* it is unreachable by any selection — a proof.
A target *inside* it is only "not proven unreachable": the choices are discrete and the
attainable set has gaps. So "37 recovered" means 37 targets are no longer provably out of
reach, not that 37 are attained; `constraint_violations.csv` says what was actually
attained. Deciding attainability exactly is a subset-sum problem per (dimension, cycle)
and is not attempted rather than approximated and reported as fact.

## R2 inputs pulled

**One file**, the same one as 2026-08-31. The carved landscape and the enumerated library
are committed artifacts, so nothing spatial had to be re-derived — no NHD, no parcels, no
ownership raster, no county geometry.

| R2 key | Local path | Size |
|---|---|---|
| `data/Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db` | `data/interim/stage/` | 1.004 GiB |

It supplies `FVS_STANDINIT_PLOT` / `FVS_TREEINIT_PLOT` for the 676 donor plots — the tree
lists FVS grows. All 676 were present; none was missing or imputed. No downloaded data is
committed; everything lands under gitignored `data/`.

## Exact commands

```bash
# 1. Build FVS Southern variant (the container has no FVS; the repo expects one at fvs/bin/)
apt-get install -y gfortran
git clone --depth 1 https://github.com/USDAForestService/ForestVegetationSimulator.git fvs/src
git -C fvs/src submodule update --init --depth 1 volume/NVEL      # NVEL volume library
sed -i 's/FVSbc_sourceList.txt/FVSsn_sourceList.txt/' fvs/src/bin/CMakeLists.txt
# Drop the underflow/denormal traps, per 2026-08-31's correction 1: the shipped makefile
# promotes benign gradual underflow -- which NVEL's own r9clark.f relies on -- into a fatal
# trap, aborting 12.5% of runs.
sed -i 's/-ffpe-trap=invalid,zero,underflow,overflow,denormal/-ffpe-trap=invalid,zero,overflow/' \
  fvs/src/bin/makefile
make -C fvs/src/bin FVSsn -j4
mkdir -p fvs/bin && cp fvs/src/bin/FVSsn fvs/src/bin/FVSsn.so fvs/bin/

# 2. The one input (rclone remote `r2` is preconfigured via RCLONE_CONFIG_R2_* env vars)
rclone copyto r2:artemis-r2/data/Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db \
  data/interim/stage/FIA_5county_consolidated.db

# 3. The run
# 12,982 FVS runs, ~19 min on 4 workers. Fails closed: refuses to publish unless the
# operator states the exclusion count exactly.
uv run python weekly-artifact/2026-09-07/make_offset_library.py --workers 4 --allow-excluded-runs 1
uv run python weekly-artifact/2026-09-07/make_annealed_plan.py    # ~10 min, 5 restarts
uv run python weekly-artifact/2026-09-07/make_figure.py           # reads only committed CSVs
```

## Dependencies

`gfortran` (apt) to compile FVS; nothing else new. The committed `uv.lock` environment was
used as-is: Python 3.14, pandas, PyYAML, matplotlib. `uv sync` reproduces it. `FVSSN_BIN`
overrides the binary location if it is not at `fvs/bin/FVSsn`.

## How to regenerate

```bash
uv sync
# build FVS and stage the one input per the commands above, then run the three drivers.
```

Output is deterministic: FVS is deterministic, and the annealer takes its seed
(`harvest.random_seed: 42`) and restart count from `config/projection.yaml`, running seeds
42–46 and reporting all five. Intermediates, all gitignored: `data/interim/fvs_batch/`
(the FVS input DB, 12,982 keyfiles, the full `trajectory_cycles` state table, and the
expanded library tables) and `data/interim/stage/` (the FIA database). Delete
`data/interim/fvs_batch/` to force the batch to re-run.

Figure colours are the Okabe–Ito-derived categorical set used across this series,
validated with the dataviz palette checker against the light surface `#fcfcfb`: lightness
band, chroma floor and normal-vision floor all pass; the worst adjacent CVD pair sits in
the 6–8 floor band, which is legal with the secondary encoding used here — every cell in
panel (b) carries its own value, every bar carries a direct label, and every plotted
number is also in a committed CSV.

## What this hands the next run

1. **Extend the horizon to eleven cycles, or stop counting cycle 10 as a target.** Eight
   of the ten remaining unreachable targets are 2072, and they are unreachable because the
   projection's terminal year hosts no cycle — not because of anything in the library. One
   line in `config/projection.yaml` either fixes it or makes the target set honest. This is
   the cheapest remaining item by a wide margin.
2. **Add negative offsets, or lower the minimum harvest age.** The other two residual
   targets are cycle-1 shortfalls, and a grid that only delays cannot reach them. The
   mechanism is already built; it needs to point both ways.
3. **Raise the search budget now that the decision space is four times larger.** The seed
   spread went from 0.05% to 1.9% on an unchanged `iterations_per_temperature`. That was
   the right call for a controlled comparison and is the wrong call for the next run,
   which no longer needs to hold last week fixed.
4. **Land the Phase 2.3 unit × stand crosswalk**, which turns the pixel classes into
   polygons and switches on adjacency, green-up, opening size, and the block move — the
   whole LAMPS half of the architecture, still dark.
5. **Promote the annealer into `pipeline/`.** `harvest_scheduler.py`, the README's
   "Current implementation", and `notes/trajectory-library-and-annealing.md` all still say
   the annealer is not built, and it has now produced two artifacts. Fix the offset-0
   phantom clearcuts (correction 2) as part of that, where changing the library is not
   also changing a control.
