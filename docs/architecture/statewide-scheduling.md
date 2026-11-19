# Statewide harvest scheduling, one county at a time

Status: **adopted** (2026-11-19; drafted 2026-11-16). Tracked as GitHub issues (see §8). Extends `pipeline/s3_management/annealer.py`;
it does not replace it.

## 1. The idea in one paragraph

For each county in a state, the annealer selects one trajectory (regime x timing offset) per
management unit, so that each cycle's removed volume matches a TPO target for every
**(county, owner group)** cell. Over the 10-cycle, 50-year horizon, every cycle's target is
the cell's annual TPO volume x 5. Each county is solved on its own, on local hardware, with
counties running in parallel. The state plan is the union of the county plans. Rolled up to
owner groups, it is checked (not optimised) against the state's own TPO totals.

## 2. Why county-by-county is exact, not an approximation

The current objective (`Objective._dim_cost`) scores two *marginals*: county totals and owner
group totals. Each is summed over the whole landscape. The owner-group term couples every
county, so splitting the problem by county would change the answer.

Scoring **cells** removes that coupling:

    cost = sum over counties c, owner groups g, cycles t of  f(v[c,g,t], T[c,g])

- Every unit belongs to exactly one county, because ADR 0004 makes county lines hard edges
  for units.
- Each `v[c,g,t]` depends only on county `c`'s units.

So the state objective is a sum of independent county objectives, and the county optima add
up to the state optimum. That holds as long as no term crosses a county line:

| Term | Crosses county lines? | Consequence |
|---|---|---|
| (county, owner group) even-flow | no | separable |
| standing volume (maximise) | no, if normalised per county (see section 5.3) | separable |
| state-total even-flow | **yes** | must stay a *report*, never an objective term |
| adjacency / green-up | only along a county line | already unavailable; when added, accept the seam |
| mill capacity / procurement zone | yes | out of scope; a later state-level pass (section 9) |

**Rule:** a county run never reads another county's state.

## 3. Targets

### 3.1 What we need

A tidy table, `config/tpo_targets_<state>.csv` (or parquet):

| column | notes |
|---|---|
| `state_fips`, `county_fips` | join key. **FIPS, not names**: this retires `TPO_COUNTY_SPELLING` |
| `owner_group` | `Private`, `Federal (NF)`, `Other public` (and the industry/NIPF split, if the source has one) |
| `period` | `2022`, `2013_2024`, … (an explicit scenario key, as today) |
| `cuft_per_year` | roundwood output, county of origin |
| `source`, `source_table` | provenance |

### 3.2 Open questions on the data (verify before writing a parser)

1. **Does the source really cross county by owner group?** The current workbook gives county
   totals and owner-group totals for the five-county aggregate: two marginals, not cells.
   Check the 2022 Florida TPO release (TPO Online / the RPA TPO database) for a true county
   x ownership table.
   - **If yes:** use the cells directly.
   - **If no:** derive cells by iterative proportional fitting (IPF/raking). Seed with
     FIADB EVALID removals by county x `OWNGRPCD` (the full FIADB is on R2), and rake to the
     county totals and the state owner-group totals. Record `derivation = ipf` per row.
2. **Single year (2022) or an average?** Florida's annual volumes carry storm salvage
   (Hurricane Michael in 2018, in the Panhandle). One year can be atypical for a county. We
   could keep `2022` as a scenario key and default to a multi-year average. Either way, the
   scheduler must never pick a period silently (it already enforces that).
3. **Does "Other public" include non-NF federal land?** It probably does. Statewide this stops
   being immaterial (`known_limitations.federal_non_nf`): Eglin AFB, Tyndall, Avon Park, and
   the national wildlife refuges are federal but not National Forest, and Eglin runs a real
   timber program. The fix is to split Harris `federal` by managing agency (PAD-US
   `Mang_Name`), routing USFS land to `Federal (NF)` and the rest to `Other public`.
   **This blocks a statewide run.**
4. **Units: TPO roundwood output vs FVS removed merchantable volume.** These differ by
   utilisation (residue, cull, non-growing-stock sources, non-forest land). A pre-schedule
   check should compare county TPO output with FIA removals for the same years, and the
   ratio should be reported. If it is far from 1 and consistent, apply it as a declared
   `utilisation_factor` in config, never inline.
5. **Does the sum of the county cells equal the state total?** TPO can suppress or leave
   volume unattributed. Store the state rows too, so the gap is a known quantity.

## 4. Decision space

No change: each unit has its owner class's eligible regimes x offsets {0, 5, 10, 15, 20, 25},
`no_management` is always present, and riparian units have exactly {`no_management`}
(enforced structurally, `verify_riparian_structural`). Owner *classes* choose regimes; owner
*groups* carry targets. Several classes charge one group (`tpo_group_for`).

## 5. Objective

### 5.1 The cost form is an enum

`harvest_volume.cost_form` in `config/projection.yaml`, following the repo's
"enums over booleans" rule:

| value | per-cell cost | behaviour |
|---|---|---|
| `relative_squared` (today) | `((v - T) / T)^2` | every cell weighs the same, whatever its size |
| `chi_squared` (**proposed default**) | `(v - T)^2 / T` | a miss weighs by cell size; small cells cannot dominate |
| `absolute_squared` | `(v - T)^2 / T_scale^2` | only volume matters; small owner groups are ignored |

Why change the default: with 67 counties x 3 groups, many cells are tiny (for example
Federal (NF) in a county with a sliver of national forest). Under `relative_squared`, a 30%
miss on a 50,000 cuft cell costs as much as a 30% miss on a 20 million cuft one. A single
clearcut is a large *relative* step in a small cell, so the search would spend its effort
there. `chi_squared` keeps the small cells in play without letting them take over.

### 5.2 Zero and near-zero targets

`T = 0` divides by zero today. Rule: a cell with `T < min_target_cuft` (in config) is scored
as `v^2 / min_target_cuft`. Harvest there is penalised, not forbidden. A cell with units but
no TPO row is a configuration error and stops the run. A TPO row with no units is reported
(volume the landscape cannot produce).

### 5.3 Standing volume

Normalise per county by that county's own attainable maximum, as `Objective` already does
per landscape. Each county's objective is then self-contained.

### 5.4 Weights

`harvest_volume` 6, `standing_volume` 1, as today (after Diaz et al.). Because of the change
in section 5.1, the weights need recalibrating on FL5 before statewide numbers mean
anything. Check that FL5 under `chi_squared` still lands within today's per-cell deviations.

## 6. Execution

### 6.1 Command

    uv run python -m pipeline.run schedule <run_dir> --partition county [--county FIPS ...] \
        [--workers N] [--restarts R]

`--partition` is an enum: `none` (today's single landscape) or `county`.

### 6.2 Per county

For each county `c`, as an independent task:

1. Load the stands, library, and cycles filtered to `c`. Read the cycles from the DuckDB
   trajectory store once it exists (step 2 of AGENTS.md); until then, read `fvs_cycles.csv.gz`.
   Read IDs with `as_id_series` throughout.
2. Build the `Landscape` and a `CellObjective` over (owner group x cycle).
3. Seed from the greedy plan, run R restarts, and keep the best.
4. Write `<run_dir>/schedule/<fips>/plan.csv.gz`, `deviation_by_cycle.csv`,
   `attainable_envelope.csv`, `seeds.csv`, and `summary.json`.

Requirements:

- **Seeds:** derive each county's seed as `seed_c = hash(base_seed, fips)`. Re-running one
  county then reproduces it exactly and leaves every other county unchanged.
- **Resume:** skip a county whose `summary.json` matches the current config hash and inputs
  hash, as the leto stage resumes.
- **Parallelism:** counties in a process pool, restarts serial within a county. That keeps
  memory at one county's landscape per worker. Florida's largest county landscapes should be
  sized from the statewide LETO run before choosing `--workers`.

### 6.3 Merge

`schedule/merge` runs after the last county and writes:

- `plan.csv.gz`: the concatenated county plans. `trajectory_id` is unchanged, so the
  outputs stage needs no change.
- `deviation_by_cycle.csv` at three levels: cell, county, and state x owner group.
- `reconciliation.csv`: per owner group and cycle, the state TPO target, the sum of the
  county targets, and the sum of the county plans, with the two gaps kept separate:
  - **target gap** = state TPO minus the sum of county TPO (the data's gap)
  - **plan gap** = the sum of county TPO minus the sum of county plans (the search's or the
    library's gap)
- `unreachable_cells.csv`: cells where `target_within_envelope = False` in any cycle. These
  are proven misses that no search can fix, so they are reported as findings.

## 7. Acceptance tests (TDD order)

1. `CellObjective` over a toy with two counties: its total equals the sum of two
   single-county objectives, and the partitioned run gives the same choice as the
   unpartitioned one under the cell objective.
2. `chi_squared` and `relative_squared` give the expected values on hand-computed cells, and
   `min_target_cuft` handles `T = 0` without dividing by zero.
3. A county with units but no target row stops the run, and the message names the cell.
4. Seed derivation: re-running county A alone gives a plan byte-identical to A's slice of
   the full run.
5. Merge: the concatenated plan has no duplicate `unit_id`, and the reconciliation's target
   gap plus plan gap equals state TPO minus the plan total.
6. The target loader joins on FIPS. A `PLT_CN`/`unit_id` that comes through a numeric column
   survives (19-digit IDs, `as_id_series`).
7. FL5 regression: `--partition county --cost-form relative_squared` with the marginal
   dimensions reproduces today's `fl5_v4_leto_173` objective within tolerance, so the
   refactor itself is shown to be neutral before the objective changes.

## 8. Staged rollout

1. **FL5, partitioned, existing marginal targets:** proves the plumbing (test 7).
2. **FL5, cell targets** (IPF from the workbook marginals and FIA removals), with
   `chi_squared`.
3. **One full Florida county** outside FL5 with a federal non-NF holding (Okaloosa for Eglin),
   after the PAD-US split (section 3.2, question 3).
4. **Florida, all 67 counties:** review the reconciliation and the unreachable cells.
5. **Another state:** only the target table and the ownership split are new.

## 9. Not in scope (but designed not to block)

- **A state-level coupling pass** (mill capacity, procurement-zone flows, a state-total
  term). If needed, run it as a second annealing over county plans, with moves restricted to
  re-timing offsets, starting from the merged plan. Partitioning first does not prevent it.
- **Spatial adjacency.** This is blocked on unit adjacency, as today.
- **Species-group targets** (softwood/hardwood). TPO has them. This would add a fourth cell
  axis, still separable.
- **Minimum harvestable %** (AGENTS.md, not yet enforced) belongs in library eligibility,
  not the objective.
