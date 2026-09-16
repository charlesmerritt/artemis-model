# Trajectory libraries and simulated-annealing scheduling — the ARTEMIS v1 architecture

**Status:** Adopted direction, 2026-08-06. Documentation is ahead of implementation:
this note defines the target so the rest of the repository can be read against it.
Nothing in `pipeline/` implements the annealer yet.

ARTEMIS builds **one library of candidate trajectories per stand**, where the stand's
**ownership class** decides which management prescriptions are eligible for it. FVS runs
once per `(stand, prescription)` pair to produce those trajectories, offline. A
**harvest scheduler then uses simulated annealing** to choose exactly one trajectory per
stand so the resulting landscape plan satisfies volume, flow, adjacency, and reserve
constraints.

The one-line statement of the shift: **FVS leaves the scheduling loop.** Simulation
enumerates what each stand *could* do; the scheduler decides what each stand *will* do.

## Guiding references

Full citations, status, and the detailed side-by-side mapping:
[`docs/references/README.md`](../docs/references/README.md).

- **`CLIMATE-FVS`** — Diaz, Perry, Tutak, Hodges & Mertens (2015), *Potential climate change
  impacts on management outcomes for western Oregon BLM forestlands simulated using
  Climate-FVS*, Ecotrust, report to BLM. Committed at `docs/references/`.
- **`LAMPS`** — Bettinger & Lennette et al., Landscape Management Policy Simulator. PDF not
  yet in the repo; expected at `docs/references/LAMPS_Bettinger_et_al.pdf`.

**Neither reference alone gives us the architecture. The composition does**, and §1.2 below
is the statement of exactly how. Read that before anything else in this note.

---

## 1. The architecture, and where it comes from

### 1.1 The pipeline

```text
  ownership class (Harris 2025)  ──▶  eligible prescription set  ──┐
  riparian geometry (BMP)        ──▶  {no_management} (override)  ──┤
  eligibility screens (MHA/MHP)  ──▶  shrink the set              ──┤
  parameter grid (intensity × timing offset)                      ──┤
                                                                   ▼
                                            enumerate (stand × prescription)
                                                                   │
                                        one continuous FVS run per pair
                                        (embarrassingly parallel, no barriers)
                                                                   │
                                                                   ▼
                                        ┌──────────────────────────────────┐
                                        │      TRAJECTORY LIBRARY          │
                                        │  DuckDB / Parquet                │
                                        │  stand × prescription × cycle    │
                                        └──────────────┬───────────────────┘
                                                       │
                                    simulated annealing over one choice per stand
                                    objectives: maximize / minimize / evenflow /
                                    evenflow_target, dimensioned by county × owner
                                    penalties: adjacency, green-up, opening size
                                    absolutes: unrepresentable by construction
                                                       │
                                                       ▼
                                    SELECTED PLAN: stand_id → trajectory_id
                                                       │
                                                       ▼
                              painting → rasters, schedules, regional summaries
```

### 1.2 The synthesis: `CLIMATE-FVS` gives the pipeline, `LAMPS` gives the constraints

The two references divide cleanly, and the division is the design. Diaz et al. built a
working two-stage system but deliberately left out spatial constraints; LAMPS is built
around exactly those constraints. ARTEMIS is their composition plus an ownership-keyed
decision space that is our own.

| Component | From `CLIMATE-FVS` | From `LAMPS` | ARTEMIS |
|---|---|---|---|
| **Overall decomposition** | Batch-simulate every eligible alternative per stand, then select among precomputed results | Heuristic selection over a landscape of stands | Adopted wholesale — §2 |
| **What restricts a stand's options** | Land classification ("prescription zones"): Critical Habitat, wilderness, stream buffers | Industrial vs. public owner behaviour | **Ownership class** (Harris 2025, seven forest classes) — §3 |
| **Absolutes** | Structural: excluded classes simply have no active prescriptions | — | Structural, same device: riparian library = `{no_management}` — §3 |
| **Eligibility screens** | — | Minimum harvest age (MHA), minimum harvestable percentage (MHP) | Applied at library-build time, so ineligible options never reach the scheduler — §3 |
| **Timing as a decision** | "Offsets" delaying first activity 5/10/15 yr, explicitly to give the optimizer choices | — | Cycle-aligned age/offset schedules — §4 |
| **Simulation engine** | Distributed, fault-tolerant, parallel FVS batch → database | — | Barrier-free parallel FVS → DuckDB — §4, §5 |
| **Search** | Simulated annealing over prescriptions × timing for every stand | Simulated annealing / tabu / genetic for spatially constrained scheduling | Simulated annealing, seeded from greedy — §6 |
| **Objective structure** | Four forms: `maximize`, `minimize`, `evenflow`, `evenflow_target`; weights set priority | — | Adopted verbatim — §6 |
| **Spatial constraints** | **None** — no adjacency, no green-up, no opening size | ARM / URM adjacency, green-up, maximum opening size, blocks | Priced as penalties; blocks drive the block move — §6 |
| **Solution quality** | Reports the chosen prescription mix as a headline result | Heuristics give no optimality guarantee | Both: mix is a result (§8), quality report is mandatory (§6) |

**What is ARTEMIS's own**, and therefore what has no precedent to lean on:

1. **Ownership class as the library-defining key.** Diaz et al. keyed eligibility to land
   *designation* on a single federal ownership; LAMPS distinguishes industrial from public
   behaviour. Neither keys a prescription library to a national, 30 m, seven-class
   ownership raster across a mixed-ownership landscape. That is the move this project is
   making, and `config/management_regimes.yaml` is where it lives.
2. **Imputed tree lists composed to units.** TreeMap → FIA plots → weighted union per
   management unit (§4). Diaz et al. had the analogous problem and solved it with GNN
   imputation, but our composition rule is ours to defend.
3. **State BMP geometry as the absolute.** Their exclusions were federal designations; ours
   are Florida BMP stream-management zones derived from hydrography (§3).
4. **TPO-derived targets dimensioned by county × owner group** (§6) — chosen specifically
   *against* the global-target artifact Diaz et al. documented.
5. **Barrier-free generation justified by measurement.** The restart-fidelity work
   (`restart-fidelity-findings.md`) is why we can assert library trajectories are clean
   where an iteratively coupled run would not be (§2).

**Where the two references disagree, and how it is resolved.** Diaz et al. optimize global
metrics and accept whatever spatial pattern falls out; LAMPS exists because that pattern
matters. ARTEMIS follows LAMPS here — adjacency and green-up are real constraints on a
working forest, and a plan that clearcuts a contiguous block because nothing forbade it is
not a plan we can defend. The cost is that the Ecotrust scheduler code cannot be used
as-is: it has no notion of a spatial neighbour. Expect to reimplement the search with the
block move, using their objective structure.

## 2. Why this replaces iterative coupling

The previous direction (`docs/superpowers/specs/2026-07-17-orchestrator-sketch.md`) put
FVS *inside* the decision loop: run every stand to a 5-year barrier, gather state, solve
an allocation, inject cuts, resume. That design is what motivated the restart-fidelity
spike, and it works — but it has three structural costs.

| | Iterative coupling (previous) | Trajectory library + annealing (adopted) |
|---|---|---|
| Cost of evaluating one candidate plan | A full FVS re-projection | A table lookup and a sum |
| Number of plans the scheduler can consider | One (a single forward greedy pass) | Millions |
| FVS barriers per run | `n_cycles` per stand | **Zero** — each trajectory is one continuous run |
| Parallelism | Constrained by the synchronization barrier within an ownership bundle | Embarrassingly parallel over `(stand, prescription)` |
| Decision space | Implicit, discovered as the run proceeds | Explicit, enumerated up front, auditable |

The decisive point is the first row. When each objective evaluation costs an FVS run, no
search is affordable, so the scheduler is forced to be greedy and myopic — it commits to
cycle *t* before it can see what cycle *t+5* would have cost. Precomputing the
trajectories converts scheduling into a combinatorial selection problem over a fixed
discrete set, where evaluating a whole landscape plan is arithmetic. That is what makes a
metaheuristic worth using at all.

**A second consequence matters for the science.** The FFE carbon corruption measured in
`restart-fidelity-findings.md` is a *stop/restart* artifact: `Forest_Shrub_Herb` collapses
to `0.02` and total stand carbon is understated ~8% at each barrier. Library trajectories
have no barriers, so that failure mode does not arise. `carbon_extension` stays `false`
today and the tripwire test in `tests/test_config.py` stays in place — but the reason for
the flag was iterative coupling, and this architecture removes it. Re-enabling carbon is
now a review decision rather than a blocked one.

**What this costs.** The decision space is frozen when the library is built. A
prescription that was not enumerated cannot be selected, so genuinely state-dependent
silviculture ("thin when SDI crosses 450, whenever that happens") has to be expressed
*inside* a trajectory as FVS event-monitor logic, not as an external decision between
cycles. Enumerate the trigger as its own prescription; do not try to recover it in the
scheduler.

**What carries forward unchanged.** The proven mechanisms from the restart-fidelity work
still earn their keep in library *generation*: concurrent isolated FVS worker processes
(`research/restart_fidelity/parallel_demo.py`, 5 processes, bit-identical to sequential)
and the verified `ThinDBH` keyword path (`pipeline/s4_fvs/regime_templates.py`). What is
retired is the restart *barrier* as a scheduling mechanism.

## 3. Ownership class defines the library, not the choice

`pipeline/s3_management/regime_assignment.py` now answers two separate questions from one
policy source: the deterministic default used by the greedy baseline, and the **eligible
set** from which the scheduler selects.

| Resolved owner class | Active eligible prescriptions (plus universal `no_management`) | Rationale |
|---|---|---|
| `private_industrial` | short/long pine rotation, hardwood clearcut/regeneration | Rotation forestry; forest type controls whether pine planting is valid |
| `private_corporate_other` | long pine rotation, light thin, uneven-aged selection | Corporate forest without evidence of industrial-scale management |
| `private_family` | light thin, uneven-aged selection, long pine rotation | Heterogeneous non-industrial private objectives |
| `tribal` | public selection, family light thin | Conservative placeholder pending a documented source |
| `federal` | public selection, restoration thin | Public multiple-use; no stand-replacing harvest in v1 |
| `state` | restoration thin, public selection, long pine rotation | Includes active state-forest management without a short industrial rotation |
| `local` | public selection, restoration thin | Small public holdings, typically lower intensity |
| `unknown` | family light thin | Conservative placeholder; count and report separately |

Machine-readable form: [`config/management_regimes.yaml`](../config/management_regimes.yaml).
The YAML is the sole authority and carries the shared prescription parameters, defaults,
eligible menus, and the absolute riparian override.

Three rules govern the mapping:

1. **`no_management` is in every non-riparian library.** A stand must always be allowed to
   grow untreated, or the scheduler cannot satisfy a binding volume cap by *not* cutting,
   and "the plan harvested this stand" stops being a decision.
2. **Riparian is structural, not preferential.** A unit whose geometry puts it in a BMP
   stream-management zone (`SMZ_Pct >= RIPARIAN_SMZ_PCT`) gets a library of exactly one
   trajectory: `no_management`. There is no decision to make, so no-entry cannot be traded
   away by an objective weight, however the penalties are tuned. This is the strongest
   available form of `methodology-directions.md` item 2's "no entry, ever" — it is
   enforced by the absence of an alternative rather than by a constraint the search could
   violate. Buffers still grow, still get trajectories, and still report as their own
   polygons.
3. **Eligibility screens shrink the library; they never extend it.** Minimum harvest age,
   reserve status (federal wilderness is federal but not harvestable), and operability
   drop prescriptions from a stand's library at build time. A stand whose library reduces
   to `{no_management}` is a valid outcome and must be logged, not silently dropped.

## 4. What "one library per stand" means precisely

**Stand = management unit.** The library is keyed by the delineated polygon from
`sketch_management_units.py` → `sliver_merge.py`, not by the FIA plot. Union County alone
has 2,442 clean units; the five-county pilot is order 10⁴. Read
[`terminology.md`](terminology.md) before writing "stand" anywhere near this.

This resolves the tree-list question left open as item 1 of
[`methodology-directions.md`](methodology-directions.md). The unit's tree list is the
**weighted union** of its constituent plots' tree lists, which is what
`build_fvs_inputs.py::build_tree_init` already builds: every donor tree record is kept
intact and its `TPA` expansion factor is scaled by the plot's area share of the unit
(LETO 5% floor, then renormalize). That is neither of the two options as originally
framed — it has Option B's biophysics (no averaged trees, so the diameter distribution and
species mix survive, and the "growing the mean tree ≠ mean of grown trees" bias does not
apply) with Option A's run structure (one FVS stand per unit, so unit-level results need
no re-aggregation).

Two consequences worth stating plainly:

- **The partial-harvest distribution rule dissolves.** Item 1 flagged that a unit-level
  residual target ("thin to 60 sq ft/ac across the unit") has no unique translation onto
  separate per-plot runs. With one composite list per unit there is nothing to distribute:
  the `ThinDBH` proportion applies to the unit's own list.
- **The composite is one competitive arena.** FVS computes density-dependent mortality and
  diameter growth over the pooled list, so trees from plot A compete with trees from plot
  B as though co-located. For a delineated management unit that is the intended reading,
  but it is a modeling assumption, not a neutral bookkeeping step. State it in the methods
  writeup.

**Deduplication is a cache, never a reporting decision.** Two units with an identical
composite tree list, identical site attributes, and an identical prescription produce an
identical trajectory, so key the FVS run cache on a content hash of those three and reuse
the result. The library still carries one row set per `(stand, prescription)` and every
polygon keeps its own identity in the outputs — the dedup is invisible above the runner.

**Timing is deliberate, not padding.** Diaz et al. offered delayed activity starts so the
optimizer could choose both *what* and *when*. ARTEMIS resolves age-based or fixed-offset
schedules from `config/management_regimes.yaml` and snaps every operation to an FVS cycle.
Adding alternative timing parameterizations remains a library-budget decision.

**Library size.** Per stand, the library is the sum of resolved eligible prescriptions —
target **6–12 trajectories per stand** as timing variants are added,
which puts the five-county pilot at roughly 10⁵ FVS runs. That is hours on one
workstation core and minutes across a node, and it is a one-time cost per library version
rather than a per-scenario cost. Growth of the grid is multiplicative, so treat "how many
parameterizations does this family really need?" as a standing budget question, not a
detail.

## 5. Library schema

Two tables. The scheduler reads the narrow one.

**`trajectory_index`** — one row per trajectory. This is the annealer's working set, and
it must be small enough to hold in memory for the whole landscape.

| Column | Notes |
|---|---|
| `trajectory_id` | Primary key; deterministic hash of `(stand_id, prescription_id)` |
| `stand_id` | Management unit (`MU_<MU_ID>`); string |
| `prescription_id` | Family plus parameter signature, e.g. `plantation_rotation.thin2037_cc2052` |
| `ownership_class` | Harris 2025 class name; the library-defining key |
| `county`, `area_ac` | Constraint dimensions for TPO caps |
| `unit_class` | `managed` or `riparian` |
| `harvest_cuft[cycle]` | Removed merchantable volume per cycle — the constraint currency |
| `objective_terms` | Per-trajectory precomputed contributions (NPV, ending carbon, …) |

**`trajectory_cycles`** — one row per `(trajectory_id, cycle)`, the full FVS state:
`calendar_year`, `age`, `BA`, `TPA`, `QMD`, `SDI`, `total_cuft`, `merch_cuft`, `board_ft`,
`removed_*`, `RmvCode`, and the five carbon pools when enabled. This is what the selected
plan joins against for painting and summaries — not what the annealer reads per iteration.

The DuckDB view vocabulary in
[`duckdb-iterative-coupling-cells.md`](duckdb-iterative-coupling-cells.md) — `fvs_removals`,
`fvs_removal_summary`, `fvs_cycle_ledger`, `fvs_spatial_crosswalk` — is how
`trajectory_cycles` gets built from raw `FVS_Summary2` output, and carries over unchanged.
Only the loop those views were originally written to serve has been replaced.

## 6. The simulated-annealing scheduler

**Decision variable.** For each stand `s`, one choice `x_s ∈ L_s` from its library. A
solution is the vector over all stands. With ~10⁴ stands and ~8 trajectories each, the
space is ~8^10000 — which is why the answer is a heuristic and why the search quality has
to be *reported*, not assumed.

**Objective — four forms, after `CLIMATE-FVS` §"Scheduling model".** Rather than one
objective plus a bag of penalties, every scenario goal is expressed in one of four forms,
each evaluated by summing precomputed per-trajectory quantities across the plan:

| Form | Meaning | ARTEMIS use |
|---|---|---|
| `maximize` | Maximize a metric over the landscape | Carbon storage, NPV, habitat area |
| `minimize` | Minimize a metric | Harvest cost, high-fire-hazard area |
| `evenflow` | Minimize the standard deviation of a metric across periods | Non-declining yield where no target is set |
| `evenflow_target` | Minimize variation around a target — a value or a range, which **may vary over time** | TPO volume caps; the primary harvest goal |

Each objective carries a weight setting its priority relative to the others. Diaz et al.
weighted their binding timber target **6×** against 1× for everything else, so the scheduler
"will first and foremost attempt to achieve harvest targets and will try to
minimize/maximize the other objectives within that constraint." That is a sound default
shape for ARTEMIS: one dominant `evenflow_target` on volume, secondary objectives at unit
weight. Weights are a scenario input and must be recorded with the run.

Note what this reframing does to the TPO caps. They are **not** hard ceilings — they are an
`evenflow_target` the plan is pulled toward from both sides, which is the right model for a
figure derived from observed historical removals. A plan that undershoots the county target
is as much a finding as one that overshoots.

`config/projection.yaml` selects the `2013_2024` target period for forward projection and
the leakage-free `pre_2015` period for the 2015–2024 hindcast. The target file also retains
`all_years` for sensitivity scenarios, but the scheduler must never infer a period from key
order or silently choose between them.

**Targets must stay dimensioned.** Diaz et al. set all targets at a single global level and
documented the consequence: the scheduler shifted harvest between BLM Districts to hit the
landscape total, concentrating it in Salem and pulling it out of Coos Bay, Roseburg and
Medford under worsening scenarios — visible only in district-level figures, and contrary to
how BLM actually allocates sale quantities by Sustained-Yield Unit. `config/tpo_targets.yaml`
already carries county and owner-group dimensions. **Keep them, and report per-dimension
outcomes rather than only the total**, or ARTEMIS will reproduce the same artifact across
Florida counties.

**Constraints, and how each is enforced.**

| Constraint | Source | Enforcement |
|---|---|---|
| Riparian no-entry | `methodology-directions.md` item 2 | **Structural** — library of size 1 |
| Minimum harvest age, reserve status, operability | `LAMPS` eligibility screen | **Structural** — prescription dropped at build time |
| TPO volume caps (total / county / owner group) | `config/tpo_targets.yaml` | `evenflow_target` objective, per cycle per dimension |
| Even flow / non-declining yield within an ownership class | orchestrator sketch objective | `evenflow` objective |
| Adjacency and green-up (URM/ARM) | `LAMPS` adjacency | Penalty on adjacent stands harvesting in the same period |
| Maximum contiguous opening size | Florida practice | Penalty on block area exceeding the cap |
| Treatment budget / capacity | scenario input | Penalty per cycle |

The split is deliberate: constraints that encode a **policy absolute** are made
unrepresentable, and constraints that encode a **target to balance** are priced. A penalty
the search can pay is the right model for a volume target and the wrong model for a
no-harvest buffer.

Diaz et al. did the first two rows differently and it is worth knowing why. Their absolutes
(stream buffers, wilderness, Critical Habitat) were enforced exactly as ours are — by
restricting which prescriptions a land classification may draw on — which is the strongest
independent confirmation available that structural enforcement is the right call. But their
scheduler carried **no spatial constraint at all**: no adjacency, no green-up, no opening
size. Those three rows are ARTEMIS's own requirement, sourced from `LAMPS`, and they are
what forces the block move in the move set below. Do not expect the Ecotrust scheduler code
to supply them.

**Moves.** Propose from a mixture, not a single kind:

- *Single-stand* — reassign one stand to another trajectory in its library. The workhorse.
- *Block* — reassign every stand in an adjacency block together. Single-stand moves are
  nearly always rejected under a green-up penalty, so without this the search stalls in
  spatially clustered configurations.
- *Period swap* — exchange harvest timing between two stands with comparable volume.
  Cheap and near-neutral on volume, which is what lets the search repair even-flow
  violations without paying for them twice.

**Acceptance and cooling.** Metropolis: accept improvements always, accept a worsening
move of size `Δ` with probability `exp(−Δ/T)`. Geometric cooling `T ← αT` with `α ≈ 0.95`
and iterations-per-temperature scaled to the stand count. Calibrate `T₀` empirically —
sample random moves from the initial solution and set `T₀ = −mean(Δ⁺)/ln(0.8)` so roughly
80% of worsening moves are accepted at the start — rather than hardcoding a number that
silently means something different on a different landscape. Terminate on a temperature
floor or on no best-solution improvement across N consecutive temperature levels.

**Initial solution.** Seed from the existing greedy oldest-first allocator in
`pipeline/s3_management/harvest_scheduler.py`. It stays in the repository for exactly this
reason, plus its second job below.

**Reproducibility.** One documented seed, recorded with the cooling schedule and the
objective weights in `versions.lock`. Same seed + same library + same weights must give a
byte-identical plan; that is a test, not an aspiration. Report best-of-R restarts with
every seed logged — never the best of an unreported number of tries.

**Reporting solution quality — required, not optional.** Simulated annealing returns no
optimality guarantee, so a plan is not a result until it is reported alongside:

1. the objective value and the **full constraint-violation vector** at the returned
   solution, per dimension per cycle, not just a scalar penalty;
2. an **objective-specific relaxation bound**. For a separable maximization objective,
   use the unconstrained per-stand best, `Σ_s max_{x∈L_s} value(x)`. For a coupled form
   such as `evenflow` or `evenflow_target`, solve a relaxation that removes the spatial
   penalties but preserves the per-period, per-dimension aggregate objective. The
   objective implementation must declare which bound strategy it supports; if no
   validated relaxation exists, report the gap as unavailable rather than manufacture a
   denominator from stand-local scores;
3. the **greedy baseline** from `harvest_scheduler.py` and a **random-selection** baseline.
   A plan that does not beat greedy is a finding about the search, and must be reported as
   one rather than tuned until it goes away;
4. the **spread across seeds**. A plan whose objective swings widely between seeds has not
   converged, whatever its best run looks like.

## 7. Where the fitted LCMS harvest model goes

`PLAN.md` §3c previously fit a multinomial logit / gradient-boosted classifier on LCMS
Tree Removal to predict `P(harvest | features)`, and applied it forward
pseudo-deterministically with a fixed seed. Under the new direction the forward schedule
comes from the optimizer, so that model is no longer a *generator*.

It is not discarded — it changes job, from prediction to **calibration and validation
evidence**:

- Observed harvest rates by ownership class, county, and stand age become the target the
  selected plan is checked against. "Does the annealed plan reproduce the observed
  ownership-specific harvest intensity?" is a sharper question than the hindcast the model
  was originally fit for.
- Where the plan and the observed rates disagree, the disagreement localizes to either the
  eligible prescription sets (the wrong menu) or the objective weights (the wrong
  preferences) — both of which are inspectable, which a fitted probability surface was not.
- The `2015–2024` LCMS holdout described in `PLAN.md` §3d still applies, now as a check on
  the scheduler rather than on the classifier.

## 8. Validation

Beyond the growth validation in `PLAN.md` §5, which is unchanged:

**Library integrity** (cheap, run on every build):
- Every riparian stand has exactly one trajectory, `no_management`. Every non-riparian
  stand has at least two unless structural screens remove every active prescription; a
  screened-down stand has exactly `no_management` and appears in an audit report.
- After collapsing duplicate FVS removal rows, every trajectory has exactly
  `n_cycles + 1` state rows — the initial state plus one endpoint per cycle — with no
  gaps and no NaNs in any objective column.
- Every stand in the unit layer appears in the library, and every library stand exists in
  the unit layer. Both directions — a stand silently missing from the library is a stand
  the scheduler cannot manage, and it will not announce itself.
- Prescriptions in the library are a subset of the ownership class's eligible set in
  `config/management_regimes.yaml`. This is the test that keeps the config honest.

**Scheduler behaviour:**
- Determinism under a fixed seed.
- Monotone best-so-far objective.
- Reported plan honours structural constraints exactly and priced constraints within a
  stated tolerance.
- Final objective ≥ greedy baseline on the pilot.

**Landscape plausibility:**
- **The selected prescription mix, by ownership class.** This is a headline result, not a
  diagnostic. Diaz et al. report it as their Figure 16 — 18% regeneration harvest, 25% no
  active management, 58% thinning or patch cut in their baseline, shifting to 44–66%
  regeneration harvest as growth declined under high emissions — and it is how the plan's
  behaviour is actually read. Report the same distribution, and report it per ownership
  class, since the ownership libraries are what generate it.
- **Per-dimension outcomes, not just landscape totals.** Harvested volume and area per cycle
  broken out by county and owner group. A plan that hits the total by quietly reallocating
  harvest between counties is the failure mode Diaz et al. documented, and it is invisible
  in an aggregate figure.
- Harvested area per cycle per ownership class against TPO and LCMS observation.
- Age-class distribution through time — a plan that liquidates the oldest classes in cycle
  1 and flatlines is satisfying its constraints and failing forestry.
- Opening-size distribution against the green-up rules.

## 9. Implementation sequence

Ordered so each step is verifiable before the next depends on it. Steps 1–4 need no FVS
run beyond the library build; step 5 needs no FVS at all.

1. **Ownership assignment per unit** — dominant-owner vote over the Harris raster within
   each unit footprint, with the confidence threshold from the orchestrator sketch
   (sub-threshold units excluded *and logged*, never silently dropped).
2. **Eligible-set expansion** — read `config/management_regimes.yaml`, apply the riparian
   override and the eligibility screens, emit `stand_id → [prescription_id]`. Pure
   function over the unit table; unit-testable with synthetic fixtures.
3. **Library generation** — render a keyfile per pair via `regime_templates.py`, run FVS
   in parallel worker processes, load results to DuckDB, run the integrity checks in §8.
4. **Trajectory index** — precompute per-cycle harvest volume and objective terms into the
   narrow table the annealer reads.
5. **Annealer** — moves, penalties, cooling, seeding from greedy, and the quality report
   of §6. Testable end to end against a synthetic library with a known optimum.
6. **Painting and products** — join the selected plan to `trajectory_cycles` and feed the
   existing `paint_fvs_to_raster.py` path.

## 10. Open questions

1. **Objective for v1.** Which metrics, in which of the four forms, at which weights? The
   shape is settled by `CLIMATE-FVS` — one dominant `evenflow_target` on harvest volume at
   ~6× weight, secondary objectives at 1× — but the secondary set is ours to choose. Carbon
   storage is the obvious candidate and is currently disabled (question 6). The weights are
   the scenario definition, so this is a project decision, not a tuning parameter.
2. **Parameter-grid resolution per prescription family.** How many rotation ages and thin
   timings genuinely change the answer? Library cost is multiplicative in this. Diaz et al.
   used 4 timing offsets (0/5/10/15 yr) across 6 prescriptions as their working answer.
3. **Even-flow scope — leaning resolved.** Keep the county and owner-group dimensions
   already in `config/tpo_targets.yaml` rather than collapsing to a landscape total; that
   is the direct lesson of the Diaz et al. district-shifting artifact. Still open: whether
   the target is non-declining or a ± band, and whether ownership-class even flow applies on
   top of the county caps or instead of them.
4. **Adjacency source.** Unit-polygon topology, or a distance threshold between centroids?
   URM vs. ARM formulation for green-up. No help from `CLIMATE-FVS` here — their scheduler
   had no spatial constraint — so this comes from `LAMPS` alone.
5. **Tribal and unknown-ownership eligible sets.** Both are conservative placeholders in
   `config/management_regimes.yaml` and need a documented source before publication.
6. **Re-enabling carbon.** The barrier-free architecture removes the measured obstacle;
   whether v1 reports the five IPCC pools is now a scope decision.

## See also

- [`../docs/references/README.md`](../docs/references/README.md) — the two guiding papers,
  `LAMPS` and `CLIMATE-FVS`, and what each contributes.
- [`terminology.md`](terminology.md) — plot vs. management unit vs. FVS stand; read first.
- [`management-pipeline-plan.md`](management-pipeline-plan.md) — the phase-by-phase build
  plan, rewritten around this architecture.
- [`methodology-directions.md`](methodology-directions.md) — the advisor-meeting decisions
  this note resolves (items 1 and 4) and preserves (item 2).
- [`restart-fidelity-findings.md`](restart-fidelity-findings.md) — why barriers were a
  problem, and why library generation does not have them.
- [`../PLAN.md`](../PLAN.md) §3c, §4 — the target architecture in build-plan form.
- [`../config/management_regimes.yaml`](../config/management_regimes.yaml) — the sole
  prescription, scheduling, and ownership-eligibility authority.
