# Weekly artifact — 2026-08-31

## Artifact

**The first simulated-annealing harvest plan — the thing ARTEMIS exists to produce.**
11,831 stands, one trajectory each, selected by the scheduler `PLAN.md` and
`notes/trajectory-library-and-annealing.md` have specified since 2026-08-06 and that
nothing in `pipeline/` had implemented.

The design note's own status line has read the same sentence for three weeks:

> Nothing in `pipeline/` implements the annealer yet.

Meanwhile `config/projection.yaml` declared the whole thing as executable policy —
`selection_method: "simulated_annealing"`, a cooling schedule, a move mixture, four
objective forms with weights, three priced spatial penalties — and
`harvest_scheduler.py` carried a docstring describing itself as "the annealer's initial
solution" for a scheduler that did not exist. This artifact closes that gap and runs it.

Getting there needed one thing first. The 2026-08-17 artifact enumerated the decision
space but could not price it: §5 of the design note names `harvest_cuft[cycle]` as "the
constraint currency", and no FVS run had ever been made, so the library had a schema and a
row count but not a single volume. **So this artifact also compiles FVS and runs the
batch** — 3,781 of 3,782 trajectories complete, one excluded and recorded — and the library
has volumes for the first time.

| File | What it is |
|---|---|
| `annealed_plan.png` | The four-panel figure: the plan against its target, attainability by county × cycle, the chosen prescription mix, and solution quality. |
| `annealed_plan.csv` | **The plan.** One row per stand: `stand_id → trajectory_id`, its prescription, county, owner class, acres, and its removed volume in each of the ten cycles. 11,831 rows. Carries two keys — `trajectory_id` (§5's primary key, unique per stand) and `fvs_run_id` (the `(plot, prescription)` run the stand shares with others), which joins many-to-one onto the two library tables. |
| `trajectory_harvest_by_cycle.csv` | **`harvest_cuft[cycle]` — the column that did not exist last week.** Removed merchantable ft³/acre per `(plot, prescription, cycle)`, keyed by `fvs_run_id`. 41,591 rows — 3,781 complete trajectories × 11 cycles. |
| `trajectory_index.csv` | §5's narrow `trajectory_index`: one row per FVS run, keyed by `fvs_run_id`, with total removed volume, harvest-cycle count, and ending state. 3,781 rows. |
| `solution_quality.json` | The §6 quality report — baselines, bound, seed spread, and what was structurally unavailable. |
| `constraint_violations.csv` | The full constraint-violation vector, per dimension per cycle (§6.1). 80 rows. |
| `attainable_envelope.csv` | What each dimension × cycle *could* reach from this library, at any selection. The table that separates a search failure from a decision-space limit. |
| `seed_spread.csv` | All five seeds, each with its calibrated `T₀`, level count, and accept rate (§6.4). |
| `harvest_by_cycle.csv` · `prescription_mix.csv` · `plan_by_dimension.csv` | The plan summarised three ways. |
| `fvs_failures.csv` | The one trajectory FVS could not simulate, and why. Written whenever the batch excludes anything; absent when nothing is excluded. |
| `make_fvs_batch.py` · `make_annealed_plan.py` · `make_figure.py` | The three drivers. |

**Why this artifact.** It is the terminal node of the pipeline diagram in §1.1 — every
weekly artifact since 2026-08-10 has been an input to it. `2026-08-10` produced the greedy
schedule that seeds it and is its reported baseline; `2026-08-17` enumerated the library it
selects from; `2026-08-24` carved the riparian landscape it selects over. It is not a
repeat of any of them: those built the decision space, this one *decides*.

## Headline results

**The annealed plan beats both required baselines, and the search has converged.**

| | Objective (lower is better) | |
|---|---:|---|
| Random selection (mean of 5 draws) | 379.75 | |
| **Greedy baseline** (`harvest_scheduler.py`) | **363.70** | §6.3 requires it beside the plan |
| **Annealed plan** (best of 5 seeds) | **190.40** | seed 45 |
| Relaxation bound | 153.74 | gap 36.66 |

Seed spread across all five seeds is **0.150** on an objective of 190.40 — a 0.08% range,
so the search has converged rather than got lucky once. `T₀` calibrated per seed to
0.081–0.138, 131–141 temperature levels, ~41% of proposals accepted.

**The greedy allocator is a real baseline here, and the annealer beats it by 48%.** The
TPO caps genuinely bind: of 4,309 stands whose owner-class default schedules a harvest,
the allocator admits every event for 3,827 and partially blocks 42, refusing units in
most cycles (1,249 of 1,270 candidates in the first, 765 of 1,211 in the fourth). Greedy
lands at 363.70, a little ahead of random selection's 379.75 — it is a sensible plan, not
a strawman — and the annealed plan is still roughly half its objective. That gap is the
measured answer to "what is the optimizer for": choosing *which* trajectory each stand
runs, rather than committing every stand to its default and then rationing by cycle, is
worth about as much as the entire greedy allocation step.

### The finding that matters: the decision space cannot supply the targets

**47 of the 80 (dimension × cycle) targets lie outside the range this library can reach at
*any* selection.** Not "the search missed them" — unreachable, by construction.

| Cycle | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Reachable county+owner targets (of 8) | 6 | 4 | 5 | 5 | **0** | 7 | **1** | 2 | 3 | **0** |

The ceiling — every stand simultaneously choosing its highest-volume trajectory — swings
from 413% of Baker's target in cycle 1 to 27% in cycle 5, and to **zero everywhere in
cycle 10**. No prescription in the enumerated library schedules an entry in 2072 at all.

The cause is timing, and the design note already names it. §4, "Timing is deliberate, not
padding": Diaz et al. offered delayed activity starts *specifically* so the optimizer could
choose both what and when. ARTEMIS's library currently offers **what** and almost no
**when** — entry years are resolved deterministically from stand age, with no offset
variants, so harvests pile into the cycles the age distribution happens to select and leave
the others empty.

The library sizes say the same thing. §4 targets **6–12 trajectories per stand**:

| Trajectories per stand | 1 | 2 | 3 | 4 |
|---|---:|---:|---:|---:|
| Stands | 6,603 | 909 | 3,381 | 938 |

6,602 of the single-option stands are riparian and correctly have exactly one; the
6,603rd is the upland stand that lost an option to the excluded FVS run below. The most
any stand in the pilot
gets is **four** — a third to a half of the design target, and none of the missing variants
are timing offsets. **Even flow is a timing property, so a library without timing variants
cannot deliver even flow however good the search is.** Adding the offset grid from §4 is the
single highest-value next increment, and it costs FVS runs, not scheduler work.

### The plan itself

| | |
|---|---|
| Stands | **11,831** (5,228 upland with a real choice, 6,602 riparian with one) |
| Acres | 925,098 total; 913,943 with at least one cutting option |
| Removed volume, 50 years | **1.88 billion ft³** merchantable |
| Best cycle against target | 2052, −17% |
| Worst | 2072, −100% (nothing in the library cuts then) |

Chosen mix, by acreage: `family_uneven_aged_selection` 332k ac · `pine_plantation_long_rotation`
224k ac · `no_management` 112k ac (101k upland + 11k riparian) · `hardwood_clearcut_regen`
67k ac · `public_thin_restore` 65k ac · `family_light_thin` 47k ac ·
`public_selection_light` 44k ac · `pine_plantation_short_rotation` 33k ac.

Note the 101k upland acres the scheduler leaves unmanaged **by choice** — `no_management` is
in every non-riparian library precisely so that a binding volume target can be satisfied by
not cutting (§3 rule 1), and the search uses it.

### Riparian no-entry survived contact with an optimizer

This is the first run in which the 2026-08-24 carve met a scheduler that could, in
principle, have traded it away. It could not: all 6,602 riparian stands carry a library of
exactly `{no_management}`, so there is no alternative for any objective weight to select.
The driver asserts this rather than assuming it. That is §3 rule 2's claim — "enforced by
the absence of an alternative rather than by a constraint the search could violate" —
verified end to end for the first time.

## What is structurally unavailable, and why it is not papered over

**The two spatial penalties cannot be evaluated on this landscape, and are reported as
unavailable rather than given a manufactured number.** `adjacency_greenup` and
`max_opening_size` need a neighbour relation between stands. A "stand" here is still a
pixel class (`TreeMap plot × county × ownership`) — a scattered set of pixels across a
county, not a compact polygon — which is the caveat `2026-08-24` recorded about its own
geometry. Two such classes are adjacent *somewhere* almost by definition, so a green-up
penalty computed on them would not mean green-up. The `block` move goes with them, since
blocks are adjacency components; the mixture renormalises over `single_stand` and
`period_swap`. **This is the single largest caveat on the plan**, it is a property of the
input rather than of the scheduler, and it clears when the Phase 2.3 unit × stand crosswalk
lands.

**The relaxation bound uses a declared strategy.** §6.2 asks for an objective-specific
bound and forbids manufacturing a denominator when none is validated. The recipe it names
first — remove the spatial penalties, keep the aggregate objective — is the *identity* here,
because the spatial penalties are already absent, so it yields no bound. The reported bound
instead uses a **per-cycle per-dimension interval relaxation**: every key and cycle is
minimised independently and every stand may pick a different trajectory for each cycle and
each dimension at once. That is a strict relaxation of the real problem, so 153.74 is a
valid lower bound; the 36.60 gap is an upper bound on what the search left on the table,
and most of it is the relaxation's own looseness rather than search error.

## Not fabricated

Every number above comes from committed repository code or from FVS output.

- **The scheduler reads its configuration, it does not embed it.** Cooling schedule, move
  weights, restarts, seed, objective forms, weights, and the target period all come from
  `config/projection.yaml`; the targets from `config/tpo_targets.yaml`; the initial solution
  from `pipeline/s3_management/harvest_scheduler.py`; the default prescriptions from
  `regime_assignment.assign_prescription`. The driver asserts the harvest objective is still
  `evenflow_target` on import, and takes `target_period: 2013_2024` explicitly — §6 forbids
  inferring a period. (`2026-08-10` used `all_years`; the config selects `2013_2024` for
  forward projection, which is the difference between the two baselines' caps.)
- **The keyfiles are the repo's.** `pipeline.s4_fvs.regime_templates.render_keyfile` renders
  all 3,782, the same committed renderer the 2026-08-17 artifact used. The run count matches
  that artifact's carved figure exactly (`library_riparian_delta.csv`, `fvs_runs = 3782`).
- **The carved landscape reproduces 2026-08-24 exactly**, asserted in the driver before
  anything else runs: 5,240 pre-carve units and 925,097.8 acres in, 11,831 stands, 22,317
  library rows, 6,602 riparian, 5,229 upland, 913,943.2 harvestable acres out — every one an
  equality check against the committed `library_riparian_delta.csv`, not a tolerance.
- **The FVS batch is validated and fails closed**: of 3,782 runs, **3,781 completed** and
  one was killed by SIGFPE and excluded (below). Every published trajectory carries the
  full 2022→2072 grid — 3,781 × 11 = 41,591 rows exactly, asserted, so no trajectory can
  enter the objective truncated.
- **The annealer's numerics carry their own tests.** `tests/test_weekly_artifact_20260831_annealer.py`
  builds a three-stand synthetic landscape (18 tests) and checks the pieces the real run can only
  assert end-to-end: `Objective.delta_and_apply` against a from-scratch `reset()` +
  `total()` recompute for every reachable move and every dimension subset, the swap move's
  apply→reject→reverse path being exactly reversible, riparian structural enforcement, an
  option with no trajectory being dropped rather than zero-filled, the relaxation bound
  actually lower-bounding the enumerated decision space, and the reporting contracts.
- `uv run pytest tests/ -q` → **912 passed, 10 skipped**. `uv run ruff check .` clean.

### The batch fails closed

A trajectory that FVS could not finish is **excluded and recorded**, never zero-filled. This
matters because the scheduler reads a trajectory as a dense vector over the ten cycles: a
run truncated by a crash would otherwise enter the objective as a stand that quietly stops
being harvested after the crash cycle — a silent data error, not a missing option. So:

- a run killed by a signal is rejected however many rows it already wrote (a nonzero exit is
  *not* itself a failure — FVS ends normally through a Fortran `STOP`, and both `STOP 0` and
  `STOP 10` occur across this batch; only signals and unknown stop codes are abnormal);
- every published trajectory is asserted to carry the whole 2022→2072 grid;
- and the driver **refuses to publish** a partial library unless the operator states the
  number of exclusions with `--allow-excluded-runs N`, which is how this run was produced
  (`N = 1`), with the exclusion listed in `fvs_failures.csv` and surfaced again by the
  annealer.

**One trajectory is excluded**: `473803917489998 / hardwood_clearcut_regen`, killed by SIGFPE
in `varmrt.f:176` — `ADJUST = TEMKIL/TEMSUM`, a division by zero in the SN mortality routine
on a nearly-empty post-clearcut hardwood stand. Unlike the underflow below, that is a
genuine numerical error, so the `zero` trap was **kept** and the run dropped rather than
made to pass. Its effect on the plan is one upland stand losing one of its options
(`stands_with_a_choice` 5,229 → 5,228), reported in `solution_quality.json` as
`options_dropped_no_trajectory`.

### Five corrections made to the run rather than worked around

1. **FVS aborted on 472 of 3,782 runs (12.5%) with a floating-point exception** in
   `r9clark.f:1286` — `R9ht`, computing `(1.0 - 17.3/totht)**p`. That is the exact condition
   NVEL's own `volume/NVEL_Patches.txt` documents as an underflow. The cause was the build,
   not the model: the FVS makefile ships
   `-ffpe-trap=invalid,zero,underflow,overflow,denormal`, which promotes benign
   gradual-underflow-to-zero — well-defined IEEE behaviour that this code relies on — into a
   fatal trap. Rebuilt with `-ffpe-trap=invalid,zero,overflow`, keeping the traps that catch
   genuine numerical errors. All 472 then completed. **No run was dropped, excluded, or
   substituted to get a clean batch.**
2. **Natural regeneration was falling back to a single loblolly pine record**, because
   `render_keyfile` was not given `stand_sdi`. That would have regenerated every bottomland
   hardwood clearcut as pine plantation and biased the volumes it produced. Fixed by
   supplying per-plot species SDI shares, which is the repo's own rule — natural
   regeneration apportioned across the stand's own species by SDI share (Diaz et al. 2015),
   implemented in `experiments/2026-08-24_leto-ca-forest-viz/04_fvs_run.py`. 671 of 676
   donor plots have a live-tree SDI table; the 20 runs on the other 5 plots (non-stocked,
   no live trees) still use the single-record fallback.
3. **The plan's `trajectory_id` was not unique.** It was keyed `PLT_CN::prescription` — the
   *FVS run* key — so the 11,831 stands carried only 2,482 distinct values, and a join on
   the documented primary key would have expanded or misassigned stand results. §5 defines
   `trajectory_id` as a hash of `(stand_id, prescription_id)`, and §4 is explicit that
   "deduplication is a cache, never a reporting decision ... every polygon keeps its own
   identity in the outputs". The plan now carries **two** keys: `trajectory_id`, unique per
   stand (11,831 distinct, verified), and `fvs_run_id`, the shared run cache key that joins
   many-to-one onto `trajectory_index.csv` and `trajectory_harvest_by_cycle.csv` (2,482
   distinct, 0 unmatched, verified).

4. **The greedy baseline was not the greedy baseline.** This one was found while working
   the review and is the most consequential. `regime_assignment.assign_prescription`
   returns a record exposing `prescription_id`; the driver read `.prescription`, and a bare
   `except Exception: continue` around the loop swallowed the resulting `AttributeError`
   for all 11,831 stands. `default_prescriptions` therefore returned `{}`,
   `greedy_seed` hit its empty-input fallback, and **`harvest_scheduler.schedule_harvests`
   was never called** — the "greedy baseline" was really "option index 0 for every stand",
   and the annealer seeded from it. A second contract error compounded it: the unit mapping
   passed the already-resolved `owner_class` / `forest_branch` strings, but `classify_owner`
   reads `OWN_CODE` and `forest_type_branch` reads `FORTYPCD`, so every stand degraded to
   the `unknown` owner and the `other` branch — silently, since that function is documented
   to degrade rather than raise.

   Both are fixed: the carved-stand table now carries `OWN_CODE` and `FORTYPCD`, the
   swallow is gone (a stand that cannot resolve a default now stops the run), and the
   baseline is genuinely `schedule_harvests`'s output. **The published numbers changed as a
   result** — greedy is 363.70, not the 1371.05 first reported, and it *beats* random
   selection rather than losing to it, so the earlier "greedy is worse than random" reading
   was an artifact of this bug and has been removed. The annealed plan is essentially
   unmoved (190.40 against 190.34), and no finding in this artifact depended on the wrong
   figure.

5. **A harvest cycle's state fields were the state *before* the cut.** FVS_Summary2 emits
   two rows for a cut year: `RmvCode = 1` carries the removal alongside the pre-cut state
   (BA 50.2, Tpa 4741, having removed 1,894 trees), and `RmvCode = 2` carries the post-cut
   state with the removal columns zeroed (BA 31.2, Tpa 2,848). Collapsing the year by
   largest `RMCuFt` kept the correct removal and the wrong state, so every harvest cycle in
   the per-cycle state table reported standing volume as though the trees were still there.
   The two rows are now merged — removals from the cut row, state from after it — with a
   clearcut's genuinely-zero post state preserved.

   **This changed no published number.** The bug reached only `trajectory_cycles`, which
   stays in gitignored `data/interim/`; the two published library tables carry removals and
   endpoint state, and the endpoint is always a non-harvest year because nothing in this
   library cuts in cycle 10. Every committed CSV is byte-identical after the fix, and the
   plan re-runs to the same objective (190.396043, seed 45). It is fixed because it would
   have mattered the moment a trajectory harvested in the final cycle — the
   `standing_volume` objective reads exactly that field — or the moment anyone read the
   state table.

Findings 1 and 3 and the fail-closed batch above were raised by Devin Review on the PR;
finding 4 surfaced while verifying its critique of the greedy mapping. Each was reproduced
against the data before being fixed. Four further review points are also addressed in the
drivers: `Objective` now scores exactly the dimensions `config/projection.yaml` declares
rather than always both; the quality report states the *renormalised* move probabilities
the sampler actually used (0.875 / 0.125) instead of the pre-renormalisation config values;
a worker that dies outright is recorded as a failure instead of discarding the whole batch;
`trajectory_index`'s `cycles` column no longer counts the cycle-0 inventory row; and IDs
read out of SQLite go through `pipeline.ids.as_id_series` rather than `.astype(str)`, per
`AGENTS.md`. A later round added two more: the harvest-year state merge above, and
`--allow-excluded-runs` becoming an *exact* acknowledgement rather than a ceiling — the
flag records that an operator looked at a specific set of exclusions and accepted them, so
a count that moves in either direction (including down) now stops publication until a
human looks again.

## R2 inputs pulled

**One file.** The carved landscape and the enumerated library are already committed
artifacts, so nothing spatial had to be re-derived — no NHD, no parcels, no ownership
raster, no county geometry.

| R2 key | Local path | Size |
|---|---|---|
| `data/Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db` | `data/interim/stage/` | 1.004 GiB |

It supplies `FVS_STANDINIT_PLOT` / `FVS_TREEINIT_PLOT` for the 676 donor plots — the tree
lists FVS grows. All 676 were present; none was missing or imputed.

*(`TreeMap2022_CONUS_5FlCntys.tif`, `FL_5county_TreeMap_TMIDs.csv` and the county shapefile
were also staged while scouting, then not needed once the carve proved reconstructible from
the committed CSVs. They are not read by any driver here.)*

No downloaded data is committed; everything lands under gitignored `data/`.

## Exact commands

```bash
# 1. Build FVS Southern variant (the container has no FVS; the repo expects one at fvs/bin/)
apt-get install -y gfortran
git clone --depth 1 https://github.com/USDAForestService/ForestVegetationSimulator.git fvs/src
git -C fvs/src submodule update --init --depth 1 volume/NVEL      # NVEL volume library
sed -i 's/FVSbc_sourceList.txt/FVSsn_sourceList.txt/' fvs/src/bin/CMakeLists.txt
# Drop the underflow/denormal traps -- see "Two corrections" above.
sed -i 's/-ffpe-trap=invalid,zero,underflow,overflow,denormal/-ffpe-trap=invalid,zero,overflow/' \
  fvs/src/bin/makefile
make -C fvs/src/bin FVSsn -j4
mkdir -p fvs/bin && cp fvs/src/bin/FVSsn fvs/src/bin/FVSsn.so fvs/bin/

# 2. The one input (rclone remote `r2` is preconfigured via RCLONE_CONFIG_R2_* env vars)
rclone copyto r2:artemis-r2/data/Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db \
  data/interim/stage/FIA_5county_consolidated.db

# 3. The run
# Fails closed: refuses to publish a partial library. One trajectory (473803917489998 /
# hardwood_clearcut_regen) dies of a genuine div-by-zero in FVS's mortality routine, so the
# exclusion is stated deliberately rather than silently absorbed.
uv run python weekly-artifact/2026-08-31/make_fvs_batch.py --workers 4 --allow-excluded-runs 1
uv run python weekly-artifact/2026-08-31/make_annealed_plan.py           # ~7 min, 5 restarts
uv run python weekly-artifact/2026-08-31/make_figure.py                  # reads only committed CSVs
```

## Dependencies

`gfortran` (apt) to compile FVS; nothing else new. The committed `uv.lock` environment was
used as-is: Python 3.14, pandas, PyYAML, matplotlib. `uv sync` reproduces it. The annealer
is plain Python and the standard library's `random` — the inner loop touches one stand's
ten-element vector per proposal, where numpy's per-call overhead costs more than the
arithmetic saves.

`FVSSN_BIN` overrides the binary location if it is not at `fvs/bin/FVSsn`.

## How to regenerate

```bash
uv sync
# build FVS and stage the one input per the commands above, then run the three drivers.
```

Output is deterministic: FVS is deterministic, and the annealer takes its seed
(`harvest.random_seed: 42`) and its restart count from `config/projection.yaml`, running
seeds 42–46 and reporting all five. Same seed + same library + same weights gives the same
plan, which §6 asks to be a test rather than an aspiration. Intermediates, all gitignored:
`data/interim/fvs_batch/` (the FVS input DB, 3,782 keyfiles, the full `trajectory_cycles`
state table, and the carved landscape tables) and `data/interim/stage/` (the FIA database).
Delete `data/interim/fvs_batch/` to force the batch to re-run.

Figure colours are the Okabe–Ito-derived categorical set used across this series, validated
with the dataviz palette checker against the light surface `#fcfcfb`: lightness band, chroma
floor and normal-vision floor all pass; the worst adjacent CVD pair sits in the 6–8 floor
band, which is legal with the secondary encoding used here — every low-contrast hue carries
a direct value label, and every plotted number is also in a committed CSV. Panel (c) is a
magnitude ranking, so it uses one hue rather than cycling categorical hues past six.

## What this hands the next run

1. **Add the timing-offset grid to the library** (§4). It is the direct cause of 47
   unreachable targets and of an empty final cycle, it is already specified, and it costs
   FVS runs rather than scheduler work. Nothing else in the plan will improve much until
   the decision space carries "when".
2. **Land the Phase 2.3 unit × stand crosswalk**, which turns the pixel classes into
   polygons and switches on adjacency, green-up, opening size, and the block move — the
   whole LAMPS half of the architecture, currently dark.
3. **Re-check `pipeline/`'s status lines.** `harvest_scheduler.py`, the README's "Current
   implementation", and `notes/trajectory-library-and-annealing.md` all still say the
   annealer is not built. A driver in `weekly-artifact/` is not the same as a module in
   `pipeline/`, and promoting this one — as `pipeline/leto_ca.py` was promoted out of an
   experiment — is the obvious follow-up. It was deliberately not done here: an artifact PR
   should not quietly become an architecture PR.
