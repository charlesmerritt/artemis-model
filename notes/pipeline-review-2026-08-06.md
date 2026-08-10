# Pipeline review — `pipeline/`, 2026-08-06

> **Follow-up (2026-08-07).** PR #21 adopted the trajectory-library + simulated-annealing
> architecture in documentation and config. It changes no executable code, so every bug in
> §3 is still open. Two conclusions here are superseded: §1.2 argued for keying trajectories
> on `(plot, regime, SI bin)`; the PR keys them on `(management unit, prescription)` and its
> reasoning is better. §1.3's scheduler critique is resolved — greedy is retained
> deliberately as the annealer's seed and reported baseline. See
> [`pr21-review-2026-08-07.md`](pr21-review-2026-08-07.md) for the full status table.

Full read of all nine modules in `pipeline/s3_management/` and `pipeline/s4_fvs/`,
checked against (a) the stated project direction, (b) the LETO prototype scripts, and
(c) what the code actually does when run.

**Stated direction this review is measured against** (from the 2026-08-06 conversation):

1. Initial state is set up by the **LETO scripts** — management-unit delineation, TreeMap
   population, and SMZ creation from the Florida SMZ/BMP handbook (buffer widths and
   practices keyed on stream class).
2. **No cyclic / iterative coupling.** The design is a **library of trajectories** plus a
   **simulated-annealing harvest scheduler**, following the Diaz et al. Climate-FVS / BLM
   western-Oregon work.

Both are *changes* from what the committed code assumes, so a lot of what follows is
"this was right for the old design" rather than "this was always wrong."

**Verification note.** LETO is not on `main` or this branch. It lives on
`origin/scripts/leto-workflow` (`scripts/LETO.V1.1.txt`, `scripts/LETO_CSV_PIPELINE.txt`,
`scripts/Create_FVS_Database.txt`, `scripts/Join_FVS_output_to_arc.txt`,
`scripts/README.txt`). I read it there. Three pipeline modules cite `scripts/LETO.V1.1.txt`
in their docstrings and that path does not resolve on this branch — see §5.1.

---

## Summary of findings

| # | Finding | Severity | Where |
|---|---|---|---|
| 3.1 | Forest mask selects **zero pixels**; every county returns `None` | Blocker | `sketch_management_units.py:339` |
| 3.2 | Pilot processes **Nassau instead of Suwannee** | Blocker | `sketch_management_units.py:43` |
| 3.3 | `TimeInt` written into the wrong keyword field → **10-yr cycles, not 5** | Blocker | ~~`regime_templates.py:125`~~ **FIXED 2026-08-07** |
| 3.4 | Keyfile has no `Stop` — diverges from the verified fixture | High | ~~`regime_templates.py:146`~~ **FIXED 2026-08-07** |
| 3.5 | Scheduler **silently blocks every unit** on a caps-key mismatch | High | `harvest_scheduler.py:98` |
| 3.6 | Clearcuts never regenerate — stands stay bare for the rest of the run | High | ~~`regime_templates.py:20`~~ **FIXED 2026-08-07** |
| 3.7 | `FVS_StandInit` is built without any site variables | High | `build_fvs_inputs.py:91` |
| 3.8 | `perennial_large` (75 ft) is unreachable; waterbody buffer never applied | High | `sketch_management_units.py:51` |
| 3.9 | Unmapped county → filter silently skipped, all parcels processed | Medium | `sketch_management_units.py:297` |
| 3.10 | `MU_ID` non-numeric → units silently vanish from rasterization | Medium | `assign_plt_cn.py:116` |
| 3.11 | `.unary_union` deprecated in the installed GeoPandas | Low | 4 sites |
| 3.12 | `max(scored)` raises when no pairing exists | Low | `paint_fvs_to_raster.py:174` |
| 4.x | Six broken seams between modules (`MU_ID`, `SMZ_Pct`, `OWN_CODE`, …) | High | §4 |
| 1.x | Three architectural conflicts with the stated direction | — | §1 |
| 2.x | Eleven embedded policy decisions with no explicit owner | — | §2 |

Tests: `146 passed, 22 skipped`. None of the blockers above are covered — the test suite
only exercises pure helper functions, never `process_county`, never a rendered keyfile's
field columns, never a cross-module contract.

---

## 1. Direction conflicts

### 1.1 Delineation: LETO Voronoi vs. the committed parcel∩EVT fishnet

`sketch_management_units.py` builds units as **parcels ∩ LANDFIRE EVT forest mask, minus
stream/water/road buffers**, then chops anything over 40 ha with a rectangular fishnet
(`split_large_geometry`, L124-170).

LETO does something structurally different:

```
TreeMap raster domain → clip to parcels
repeat until nothing > 200 ac:
    split into ≤200 ac and >200 ac
    for the >200 ac set: random points (1 per 100 ac, ≥1000 ft apart)
                         → Thiessen/Voronoi polygons → clip back to parent
    merge small + replacement
→ multipart to singlepart → delete <5 ac → clip to parcels → MU_ID
```

Differences that matter:

- **No LANDFIRE EVT anywhere in LETO.** The forest extent comes from the TreeMap raster
  domain clipped to forest parcels. The entire EVT code path in `sketch_management_units.py`
  is not part of the LETO initial state — which is convenient, because that path is broken
  (§3.1).
- **Subdivision is Voronoi on random points, not a fishnet.** Voronoi cells follow the
  parent polygon's shape and give plausible stand-like geometry; a fishnet produces
  rectangular slices with arbitrary edges and manufactures new slivers at every grid
  boundary, which the default sliver policy then deletes (§2.3).
- **Max unit size is 200 ac (~81 ha) in LETO vs. 40 ha here** (`TARGET_MAX_AREA_HA`, L39,
  which follows `PLAN.md` §3a). Two different size policies are live at once.
- **Clip to parcels happens last in LETO, first here.** Different order, different geometry.

On the word *cellular automata*: LETO's mechanism is iterative random-point Voronoi
tessellation, not a neighborhood-rule cellular automaton. It is "grow regions until they
are the right size," which is CA-flavored, but if an actual CA (seed + neighborhood growth
rule, e.g. region-growing on the TreeMap/ownership stack) is what's intended, that exists
nowhere — not in LETO, not in `pipeline/`, and not in `research/mgmt_units/` (which
implements Felzenszwalb/SLIC segmentation, a third approach). **Worth confirming which of
the three you mean before more delineation work happens.**

**Recommendation.** Port LETO's delineation as the primary path and demote
`sketch_management_units.py` to a comparison baseline (or delete it). The BMP-buffer
machinery in it is worth keeping — but as an *attribute* step (§1.5), not an erase step.

### 1.2 FVS unit: per-management-unit runs vs. the library of trajectories

`build_fvs_inputs.py` builds **one FVS stand per management unit**, initialized from a
weighted union of its donor plots' tree lists (each donor tree's `TREE_COUNT` scaled by the
plot's area weight, L76-88). That is a faithful LETO port, and it is *better* than averaging
— it keeps every real tree record and the full diameter distribution.

It is also the wrong shape for a trajectory library. Run count scales with **unit count**:
Union County alone is 2,442 clean units, so the 5-county pilot is roughly 10-15k FVS runs
and statewide Florida is in the hundreds of thousands. The library-of-trajectories design
keys runs on `unique(plot × regime × SI bin)` — 693 plots × ~5 regimes ≈ 3,500 runs for the
pilot, and that count grows with the regime library, not with the landscape.

`notes/methodology-directions.md` items 1 and 4 already worked this out and landed on
exactly the library design (Option B: assign the regime at the unit, simulate at the plot,
area-weight back). `build_fvs_inputs.py` implements Option A's run structure. The two
disagree, and the direction you just stated resolves it in favor of the library.

**What this means concretely:**

- The composite-tree-list build (`build_tree_init`) is no longer needed for *simulation*.
  It is still useful for **reporting** initial unit-level condition.
- `assign_plt_cn.py` becomes *more* important, not less — the `MU_ID → PLT_CN → WEIGHT`
  table is precisely the painting key that turns per-plot trajectories into per-unit
  results.
- `paint_fvs_to_raster.py` is the module closest to the target architecture. It already
  does `TM_ID → PLT_CN → trajectory → pixel`. It needs one thing: the lookup key extended
  from `plot` to `(plot, regime, SI bin)`.
- `impute_nearest_runnable` (L112-161) mostly stops being necessary — a unit with no
  TreeMap pixels has no trajectory to paint, which is a delineation bug to fix rather than
  a hole to fill with a neighbor's trees.

### 1.3 Scheduler: greedy priority queue vs. simulated annealing

`harvest_scheduler.py` is a **single-pass greedy allocator**: sort candidates by stand age
descending, walk the list, take a unit if every active budget has room (L88-106). No
objective function, no re-visiting, no escape from a bad early commitment. Oldest-first is
hardcoded as *the* rule (`notes/management-pipeline-plan.md` decision 2).

Simulated annealing needs three things this module does not have:

1. **An objective function.** Right now there is none — feasibility *is* the goal. Annealing
   needs something to minimize (deviation from TPO targets by county × owner × period, plus
   whatever else: even-flow, NPV, carbon, adjacency violations).
2. **A move set over a full schedule.** The unit of work becomes a whole
   `unit × cycle × regime` assignment vector, perturbed by moves (shift a unit's harvest
   cycle, swap two units, change a regime), not a single greedy pass.
3. **Constraints as penalties or feasibility filters**, decided explicitly. TPO caps as hard
   filters keeps the current semantics; as soft penalties it lets the annealer trade a small
   overshoot in one county against a big improvement elsewhere. **This is a decision, not a
   detail** — with hard caps you need a feasible starting schedule, which is what the greedy
   allocator is actually good for.

The good news: the existing pieces slot in. `_build_budgets` / `_dim_key` become the
constraint evaluator; `allocate_cycle` becomes the initial-solution generator; the
`docs/superpowers/plans/2026-07-28-lamps-scheduler-integration.md` LAMPS constraints
(MHA/MHP eligibility, ARM/URM adjacency blocking) are exactly the kind of feasibility rules
an annealer needs. What is superseded is only the *greedy allocation order* in that plan,
not its constraint definitions.

Two problems with that LAMPS plan as written, independent of the direction change:

- Tasks 1 and 2 run `git show lamps-harvest-constraints:…`. **That branch does not exist on
  `origin`.** The plan is not executable as written.
- Its `allocate_cycle_with_blocks` is still greedy (priority-ordered blocks, first-come
  budget consumption). It would need re-framing as an annealing move set.

**Also missing from the repo entirely:** any citation, spec, or note for the Diaz et al.
work. Everything else in this project has a `notes/` entry pinning the source. Worth adding
one — cooling schedule, objective, move set, and acceptance criterion should be written down
before they're coded, or they'll end up as magic constants.

### 1.4 Iterative-coupling residue to clean up

The old design is still visible in several places and will actively mislead:

- **`main.py`** — the entire documented flow is "project 5 yr → check thresholds → apply
  prescriptions → repeat." That is the design you're dropping. It raises
  `NotImplementedError`, so nothing breaks, but it is the repo's top-level entry point and
  it describes the wrong architecture.
- **`config/projection.yaml:carbon_extension: false`** — disabled because FVS stop/restart
  silently resets FFE live-fuel state and understates `Total_Stand_Carbon` by ~8% per
  restart. The note's own last line: *"Continuous (unsegmented) runs are unaffected."* **A
  trajectory library is exactly a continuous unsegmented run.** The reason for the disable
  evaporates with iterative coupling, and `PLAN.md` §6 requires all five IPCC pools. This is
  probably the single highest-value consequence of the direction change: **carbon can be
  turned back on.** Worth re-confirming against `notes/restart-fidelity-findings.md` before
  flipping it, then updating the comment so the next reader doesn't re-disable it.
- **`notes/duckdb-iterative-coupling-cells.md`** — builds `fvs_cycle_change` and
  `fvs_management_candidates` (threshold triggers on BA/SDI/TPA) as the core of the
  coupling loop. The removals/ledger views stay useful; the trigger view does not.
- **`docs/superpowers/specs/2026-07-16-parallel-fvs-runs-design.md`** and
  **`2026-07-17-orchestrator-sketch.md`** — same, unreviewed here but flagged as likely stale.
- **`config/projection.yaml:harvest.forward_method: pseudo_deterministic`** with
  `random_seed: 42` — written for "draw a harvest schedule once per pixel at init"
  (`PLAN.md` §3c). Annealing is stochastic in a different way and needs its own seed
  discipline. The comment says the seed must be locked before external reporting; decide
  whether one seed covers both or they're separate.

### 1.5 SMZ: three different policies are currently live

This is the messiest area, and it directly touches what you asked for (handbook-derived
buffers and practices).

| Source | Buffer rule | What happens to the buffer |
|---|---|---|
| LETO (`calculate_smz_percent`) | **flat 35 ft**, all streams, one class | **Retained**; becomes `SMZ_Acres` / `SMZ_Pct` attributes on the unit |
| `config/bmp_rules.yaml` | 35 / 50 / 75 ft by class + 75 ft waterbody | (config only — nothing consumes the full set) |
| `sketch_management_units.py` | 35 / 50 ft (75 unreachable, §3.8) | **Erased**; acreage discarded entirely |
| `notes/methodology-directions.md` item 2 | by class | Retained as **separate no-entry polygons**, grown and reported |
| `regime_assignment.py` | — | Unit is riparian iff `SMZ_Pct >= 50%` → `no_management` |

Four incompatible things at once. Specifically:

- **LETO's flat 35 ft is the ephemeral/intermittent width only.** It under-buffers perennial
  streams (should be 50 or 75 ft) and does not buffer waterbodies at all. If the SMZ is
  supposed to come from the Florida handbook keyed on stream class, LETO's `SMZ_BUFFER`
  parameter needs replacing with the `bmp_rules.yaml` classification, and
  `classify_stream_fcode` needs to actually be able to return `perennial_large` (§3.8).
- **Erase vs. attribute is a fork in the road.** LETO computes an SMZ *percentage* and lets
  the regime rule act on it. `sketch_management_units.py` cuts the buffer out and throws the
  acreage away. `methodology-directions.md` says keep them as their own polygons. These are
  three different answers and only one can be implemented. LETO's + the regime rule is
  self-consistent; `methodology-directions.md` is more faithful to "reported as unique
  buffer polygons"; the erase is the only one that is straightforwardly wrong, because it
  silently deletes standing volume and carbon from the landscape.
- **`SMZ_Pct >= 50%` is a coarse policy.** A unit that is 49% SMZ gets a clearcut regime
  applied across the whole unit, riparian half included. A unit that is 51% SMZ has its
  non-riparian half locked up. Whether that's acceptable depends on unit size relative to
  buffer width — at LETO's 100-200 ac units it's a real distortion.
- **"No entry, ever" is stricter than the Florida handbook.** The FL Silviculture BMP manual
  defines SMZ practices — limited selective harvest with basal-area retention, canopy/shade
  retention, restrictions on ground disturbance and machinery — rather than a blanket
  prohibition. `notes/methodology-directions.md` item 2 chose absolute no-entry, and
  `PLAN.md` §4b was updated to match. That's a defensible, conservative modeling choice, but
  it is *your* choice and not the handbook's rule. Since you specifically said "buffers and
  other practices that come from the SMZ handbook," this needs an explicit decision: model
  the handbook's SMZ prescription (partial retention harvest), or keep the conservative
  no-entry simplification and document the departure.

---

## 2. Policy decisions embedded in code, with no explicit owner

These are all *live* in the code today, decided by whoever typed them, and most are
undocumented as decisions.

| # | Decision | Value in code | Where | Conflict |
|---|---|---|---|---|
| 2.1 | Riparian threshold | `SMZ_Pct >= 50%` → whole unit unmanaged | `regime_assignment.py:30` | vs. per-polygon buffers (§1.5) |
| 2.2 | Riparian treatment | No entry, ever | `regime_assignment.py:68` | Stricter than FL handbook (§1.5) |
| 2.3 | Sliver policy | **`drop`** (delete <5 ac) | `sliver_merge.py:271,300` | vs. area conservation (below) |
| 2.4 | Min stand size | 5 ac | `sliver_merge.py:54` | Matches LETO ✓ |
| 2.5 | Max unit size | 40 ha | `sketch_management_units.py:39` | LETO uses 200 ac ≈ 81 ha |
| 2.6 | Road artifact buffer | 3 m, erased | `sketch_management_units.py:40` | Not in LETO at all |
| 2.7 | Min plot weight | 5%, then renormalize | `build_fvs_inputs.py:39` | Matches LETO ✓ |
| 2.8 | Ownership assignment | (not implemented) | — | Plan says centroid; LETO uses zonal majority |
| 2.9 | Harvest priority | Oldest stand first | `harvest_scheduler.py:88` | Superseded by annealing objective |
| 2.10 | Inventory year | Hardcoded 2022 for every stand | `build_fvs_inputs.py:96`, `regime_templates.py:36` | Plots have differing `INVYR` |
| 2.11 | Regeneration | None, ever | `regime_templates.py:20` | See §3.6 |

Three that deserve more than a table row:

**2.3 — the sliver `drop` default deletes forest, and its stated justification does not
hold in this pipeline.** `sliver_merge.py:24-28` defends `drop` by saying "the forest they
cover is picked up downstream by LETO's *second* script, which imputes tree lists for
tree-less/edge units from the nearest runnable unit (`GenerateNearTable`)." That is a
misreading of what `GenerateNearTable` does. LETO's nearest-runnable step gives a tree list
to units that **exist but received no live trees**. It does not resurrect polygons that were
deleted. Once a sliver is dropped from the GeoPackage, `build_fvs_inputs.impute_nearest_runnable`
never sees it — it iterates over `units_gdf`, and the row is gone. Those acres are
permanently absent from the projected landscape.

This matters at scale: 87% of raw Union County polygons are sub-threshold
(`notes/terminology.md`), and `split_large_geometry`'s fishnet manufactures more of them at
every grid edge. It also directly contradicts the area-accounting invariant in
`notes/methodology-directions.md` item 2:

    Σ managed + Σ riparian == (forest mask ∩ parcels) − (waterbodies ∪ road buffer)

`drop` cannot satisfy that. `merge` can. LETO's own delete-small step is defensible in
LETO's context because LETO deletes slivers created by Voronoi subdivision of a *contiguous*
forest domain, where the deleted area is small and marginal; here the slivers are created by
BMP erasure and fishnet splitting, so they are a large fraction of the map. **Recommend
flipping the default to `merge`**, and either way emitting the dropped/merged acreage as an
explicit line in the summary so the loss is visible.

**2.5 — two max-unit-size policies.** 40 ha (`PLAN.md` §3a, "typical for the Southeast") vs.
LETO's 200 ac. Unit size drives run count, so this is also a compute decision. Under a
trajectory library it matters much less than it used to (run count is bounded by
`plot × regime × SI`, not by unit count), which argues for going with LETO's 200 ac and
fewer, more operationally realistic units.

**2.10 — `INV_YEAR = 2022` for every stand.** LETO hardcodes it
(`standlist["INV_YEAR"] = 2022`) and the port faithfully reproduces it. But
`paint_fvs_to_raster.py:113-119` documents the opposite fact from the real baseline run:
*"the 693 stands have differing inventory start years"* — which is why it anchors snapshots
on `years_since_start == 0` rather than a calendar year. Stamping 2022 on a tree list
measured in, say, 2014 silently pre-dates it by eight years of growth with no adjustment.
This is a real bias in the initial state and it propagates through every trajectory. Either
carry each plot's true `INVYR` into `StandInit`, or grow the tree lists forward to a common
2022 vintage — but not neither.

---

## 3. Bugs

### 3.1 Forest mask selects zero pixels; every county returns `None` — **Blocker**

`sketch_management_units.py:339` (and the dead copy at :203):

```python
forest_mask = (out_image[0] >= 1000) & (out_image[0] < 3000)
```

LANDFIRE 2022 EVT codes for this AOI are **7292+**. This was already discovered on the
research side — `research/mgmt_units/run_segmentation_aoi.py:14-17` says so explicitly:

> `create_forest_mask()` keys on EVT codes 1000-2999, which selects **ZERO pixels** in the
> real LANDFIRE 2022 EVT for this AOI (its codes are 7292+).

The research runner works around it by loading a pre-computed
`landfire_evt_forest_mask_5070.tif`. The pipeline module was never fixed. With an empty
mask, `process_county` logs `"Empty forest mask - no intersection possible"` and returns
`None` (L362-363) — for every county, every time. The pipeline's first stage cannot produce
output.

The code's own comment admits the approach: *"This is a simplified approach; production code
should use the VAT."* The EVT VAT has `EVT_LF` and `EVT_ORDER`; `notes/management_units.md`
already records that `EVT_LF == "Tree"` or `EVT_ORDER == "Tree-dominated"` is the correct
filter. If LETO's TreeMap-domain approach is adopted (§1.1), this whole path goes away
instead of being fixed.

### 3.2 The five-county pilot processes Nassau instead of Suwannee — **Blocker**

`sketch_management_units.py:43`:

```python
PILOT_COUNTIES = ["003", "023", "047", "089", "125"]  # Baker, Columbia, Hamilton, Nassau, Suwannee, Union
```

Five codes, six names in the comment. `089` is **Nassau**; **Suwannee is `121`** and is not
in the list. The pilot AOI everywhere else in the repo is Baker / Columbia / Hamilton /
Suwannee / Union:

- `notes/management_units.md:23` — the parcels GDB contains "Columbia, Suwannee, Hamilton,
  Baker, Union." No Nassau.
- `config/tpo_targets.yaml` — `Baker, Columbia, Hamilton, Suwanee, Union`.
- `notes/management-pipeline-plan.md` — same five.

Result: `--pilot-five-county` runs Nassau (0 parcels → `logger.error` → `None`) and never
runs Suwannee. `county_name_map` (L288-295) has no `"121"` entry either, and does have a
stray `"091": "OKALOOSA"`. Fix is `089 → 121` plus `"121": "SUWANNEE"` in the map.

### 3.3 `TimeInt` is written into field 1, not field 2 — **Blocker**

`regime_templates.py:125`:

```python
InvYear   {inv_year:>10d}
TimeInt   {cycle_years:>10d}
NumCycle  {num_cycle:>10d}
```

FVS reads keyword parameters in 10-column fields: cols 1-10 keyword, 11-20 field 1, 21-30
field 2. For `TIMEINT`, **field 1 is the cycle number** the interval applies to (blank/0 =
all cycles) and **field 2 is the interval length in years**.

This renders `5` at columns 11-20 → field 1 → "cycle 5", with field 2 blank. It does not set
a 5-year cycle length. The verified fixture this module says it mirrors gets it right —
`research/restart_fidelity/make_keyfiles.py` via `make_cut_keyfiles.py:38`:

```
TimeInt                 {cycle_years}
```

which lands `5` at column 25 → field 2. `InvYear` and `NumCycle` are both single-field
keywords and are fine as written; only `TimeInt` is misplaced. Effect if unnoticed:
`NumCycle 10` at FVS's default 10-year interval projects **100 years, not 50**, and every
cycle boundary lands on the wrong year. No test covers keyword field columns — the module's
tests check `ThinDBH` field positions carefully (`test_thindbh_field_order_…`) but nothing
checks the schedule block.

### 3.4 Rendered keyfiles have no `Stop` — **High**

`regime_templates.py:146` ends the template at `Process`. The verified fixture appends
`"Stop\n"` (`make_cut_keyfiles.py:124`). In FVS, `Process` ends the current stand and `Stop`
ends the run. Since this module says its "schedule/DataBase scaffolding mirrors the verified
keyfiles exactly," and it does not, either fix the template or amend the claim. Fixing it is
cheap.

### 3.5 The scheduler silently blocks every unit on a caps-key mismatch — **High**

`harvest_scheduler.py:98`:

```python
if budgets[dim].get(key, 0.0) < vol:
```

A missing key gets a **0.0 budget**, so the unit is blocked and the run reports
`blocked_by = "county"` — indistinguishable from a genuine budget exhaustion. There is no
warning, no error, no diagnostic.

This is not hypothetical. The TPO config spells the county **"Suwanee"** (one `n`), while
parcels carry `CNTYNAME = "SUWANNEE"` — `tpo_targets.py:26-28` explicitly documents the
discrepancy and deliberately leaves it unresolved ("left as-is here to stay faithful to the
source"). Nothing normalizes it anywhere. And the case difference alone breaks every county.
Reproduced:

```
unit_id   county  harvested  volume_removed blocked_by
     u1 SUWANNEE      False             0.0     county
     u2    BAKER      False             0.0     county
```

Both units blocked against caps of 18.5M and 11.8M cuft/yr. A schedule that harvests nothing
is a plausible-looking output, so this fails silently at the worst possible level.

Fix: raise (or at minimum warn loudly) on a key present in `units` but absent from `caps`,
and add a normalization layer for the county vocabulary. Note that `by_owner_group` has the
same shape of problem — its keys are `"Federal (NF)"`, `"Other public"`, `"Private"`, but
`regime_assignment.py` works in numeric `OWN_CODE` 3-8. Nothing maps between those two
vocabularies.

### 3.6 Clearcuts never regenerate — **High**

`regime_templates.py:20` states plainly that regeneration keywords (`PLANT`, `NATREGEN`) are
"intentionally **not** emitted here — they need their FVS field layouts verified first."
Meanwhile `regime_assignment.py:85` assigns `clearcut` at `inv_year + 30` to corporate
hardwood, and `plantation_rotation` (L81-84) ends in a clearcut at `inv_year + 30`.

Over a 50-year horizon that means: **clearcut at year 30, then twenty years of an empty
stand.** FVS will not regenerate it on its own without regeneration keywords or the
regeneration establishment model configured. Corporate/other-private is the dominant
ownership class across the pilot AOI, so this is not an edge case — it will visibly and
badly understate landscape volume and carbon in the back half of every managed run, and it
will do so in a way that looks like a "management effect" rather than a modeling artifact.

`notes/management-pipeline-plan.md` Step 3.1 explicitly asks for regeneration
("optionally replant", "site prep, plant, thin, clearcut on rotation"). The honest options
are: verify and emit the regeneration keywords, or cap the clearcut year so no stand is
clearcut inside the horizon, or state the limitation prominently on every output. The
current state — a documented gap in a docstring, invisible in the results — is the worst of
the three.

### 3.7 `FVS_StandInit` is built without any site variables — **High**

`build_fvs_inputs.py:91-109` builds `StandInit` from the **management-unit attribute table**:
`STAND_ID`, `VARIANT`, `INV_YEAR`, `STATE`, `MU_ID`, plus whatever columns happen to be on
the polygons. Missing: **site index, slope, aspect, elevation, forest type code, BAF /
sampling design.** Without site index in particular, FVS falls back to defaults and growth
predictions are not meaningful.

Two aggravating factors:

- The module docstring says it uses "Chaz's FVS-ready per-plot tables
  (`FL_FVS_TREEINIT_PLOT.csv` / **`FL_FVS_STANDINIT_PLOT.csv`**)". The code never reads
  StandInit. The CLI has `--tree-init` and no `--stand-init` (L193). The donor plots' site
  variables — which exist, in the FIA DB, as `FVS_STANDINIT_PLOT` per `config/data_paths.yaml`
  — are simply dropped.
- LETO has the same gap and **says so**: `scripts/README.txt` warns that its outputs "do not
  yet have info like Plot BAF, FVS variant, and FVS state so **tread lightly**." That warning
  did not survive the port.

`PLAN.md` §1b requires validating that every plot has these fields. Under the library-of-
trajectories design this gets easier, not harder: runs are keyed on the plot, so the plot's
own `FVS_STANDINIT_PLOT` row is the natural StandInit and there's nothing to synthesize.

### 3.8 `perennial_large` is unreachable; waterbody buffer never applied — **High**

`sketch_management_units.py:51-72` classifies only two outcomes:

```python
ephemeral_intermittent = {46000, 46003, 46007}
perennial              = {46006}   # → "perennial_small"
```

`"perennial_large"` is never returned, so the 75 ft width looked up at L375 is dead. Every
perennial stream — including large ones — gets 50 ft. `config/bmp_rules.yaml` documents the
missing discriminator (Strahler order 3+, or channel width ≥15 ft) and
`notes/management_units.md` records that the NHD layer "exposes `fcode` in quick inspection
but not stream order or channel width," so the input needed to make the distinction has to
come from NHDPlus VAA (`StreamOrde`) or a width attribute. Right now the config promises a
three-tier policy and the code delivers two.

Separately, **FCode 46000 is "stream/river, unspecified"** — unknown permanence — and is
mapped to the *narrowest* buffer. For a regulatory buffer, unknown should default to the
conservative width, not the permissive one.

And the waterbody rule (`bmp_rules.yaml`: lakes/ponds 75 ft, matching `PLAN.md` §3b) is
never applied at all: `sketch_management_units.py:391-397` erases the raw waterbody polygons
with **no buffer**. Any of the three SMZ designs in §1.5 needs this.

### 3.9 Unmapped county → filter silently skipped — **Medium**

`sketch_management_units.py:297-299`:

```python
if county_fips in county_name_map:
    county_name = county_name_map[county_fips]
    parcels = parcels[parcels["CNTYNAME"] == county_name].copy()
```

For a FIPS not in the map — e.g. `121`, which is missing (§3.2) — the filter is skipped
entirely and **the whole multi-county parcel layer is processed as if it were that county**,
then labeled `county_name = "Unknown"` (L466) and written out. Wrong results, no error.
Should raise on an unmapped FIPS.

### 3.10 Non-numeric `MU_ID` silently drops units from rasterization — **Medium**

`assign_plt_cn.py:116, 123`:

```python
units[id_field] = pd.to_numeric(units[id_field], errors="coerce")
shapes = ((geom, int(val)) for geom, val in zip(units.geometry, units[id_field]) if pd.notna(val))
```

`sketch_management_units.py:462` writes `unit_id = "mu_12125_00000001"` — a string. Pass
`--id-field unit_id` and **every** unit coerces to `NaN`, every shape is filtered out,
`mu_arr` is all-nodata, and you get `"No overlapping MU / TreeMap pixels — check alignment
and nodata"` — a message pointing at CRS/nodata when the real cause is the ID field. Coercion
failures should be counted and raised, not dropped.

Related, in the same function: the docstring (L9) says *"max-area cell assignment"*, which
correctly describes LETO (`cell_assignment="MAXIMUM_AREA"`), but the port uses
`merge_alg=MergeAlg.replace` (L126) — **last-feature-wins by iteration order**, not
max-area. For adjacent units the boundary pixels go to whichever unit happens to be later in
the GeoDataFrame. Either implement max-area (rasterize at a finer resolution and take the
modal unit per 30 m cell) or correct the docstring.

### 3.11 Deprecated `.unary_union` — **Low**

GeoPandas 1.1.3 is installed; `.unary_union` raises `DeprecationWarning: The 'unary_union'
attribute is deprecated, use the 'union_all()' method instead.` (verified). Four sites in
`sketch_management_units.py`: L383, L394, L402, L418.

### 3.12 `max(scored, …)` raises on an empty dict — **Low**

`paint_fvs_to_raster.py:174` — if neither pairing's files exist (likely off the workstation,
since `TREEMAP_CHAZ = Path("/mnt/d/TreeMap_Chaz")` is hardcoded at L28), both are skipped
and `max()` raises `ValueError: max() arg is an empty sequence` after printing a friendly
"missing files, skipped" for each. Needs a guard with an actionable message. Same function,
L153: `vals.min()` on an empty merge would also raise.

### 3.13 Minor / dead code

- `load_florida_counties()` (`sketch_management_units.py:179-183`) — stub, `pass`, returns
  `None`, never called.
- `create_forest_mask_from_evt()` (L186-224) — never called; its logic is duplicated inline
  at L333-355 with a **different** `all_touched` setting (`True` in the dead function,
  `False` inline). Two versions of one rule, one of them unreachable.
- `from shapely.geometry import shape` re-imported inside `process_county` (L331); already
  imported at module level (L27).
- `unit_id` is assigned by row position *after* the large-polygon split (L462), so IDs are
  not stable across runs if input ordering changes.
- `summarize_schedule` (`harvest_scheduler.py:147`) assumes a `unit_id` column exists without
  validating it.

---

## 4. Broken seams between modules

Nothing in `pipeline/` currently runs end-to-end, because the modules do not agree on their
interfaces. Producers and consumers:

| Field | Consumed by | Produced by | Status |
|---|---|---|---|
| `MU_ID` | `assign_plt_cn.py`, `build_fvs_inputs.py` | **nothing in-repo** (LETO) | `sketch_management_units.py` writes `unit_id` (string) |
| `SMZ_Pct` | `regime_assignment.py:66` | **nothing in-repo** (LETO) | sketch *erases* buffers instead |
| `OWN_CODE` | `regime_assignment.py:52` | **nothing in-repo** (LETO) | plan Step 2.2 unimplemented |
| `FORTYPCD` | `regime_assignment.py:41` | **nothing in-repo** | never joined from FIA |
| `stand_age` | `harvest_scheduler.py:88` | **nothing in-repo** | plan decision 3 (area-weighted) unimplemented |
| `removable_volume` | `harvest_scheduler.py:98` | **nothing in-repo** | semantics undefined (see below) |
| `county` / `owner_group` | `harvest_scheduler.py` | — | vocabulary mismatch with TPO config (§3.5) |

So `regime_assignment.py` reads **three** fields (`OWN_CODE`, `SMZ_Pct`, `FORTYPCD`) that only
LETO produces, and it degrades gracefully on all three — meaning if you run it on
`sketch_management_units.py` output today, **every unit silently gets the
`thin_from_below` default**. It will not error. It will just produce a uniform,
meaningless assignment. That's the same failure mode as §3.5: plausible output, wrong.

Two more contract problems:

**4.1 — `removable_volume` has no definition, and two modules disagree about who decides
harvest timing.** `regime_assignment.py` hardcodes harvest *years* into the regime params
(`inv_year + 10`, `+15`, `+30`). `harvest_scheduler.py` independently decides which units
harvest in which cycle. Both write harvest timing, neither consults the other. A unit the
scheduler declines to harvest still gets a keyfile that cuts at 2052 because its regime says
so — and the FVS run, not the scheduler, is what produces the numbers. Under a trajectory
library this is resolvable and actually cleaner: the regime *defines the trajectory* and the
scheduler *chooses which trajectory each unit follows*. But that has to be made explicit,
because right now there are two sources of truth.

Relatedly, the scheduler treats every harvest as all-or-nothing whole-unit
(`harvest_scheduler.py:11-12`, "Units are whole stands, so harvest is all-or-nothing per
unit"), but three of the five regimes are *partial* cuts (`thin_from_below`,
`selection_harvest`, and the thin leg of `plantation_rotation`). `removable_volume` is
presumably clearcut volume; for a 35% thin it is something else entirely. Undefined.

**4.2 — non-forest and water ownership classes are never masked.**
`config/projection.yaml` declares `mask_values: [1, 2]` (`non_forest`, `water`) and
`PLAN.md` §3c says those pixels are "masked from FVS pipeline entirely." Nothing masks them.
In `regime_assignment.py`, `OWN_CODE` 0/1/2 falls through every branch to the
`thin_from_below` default (L87-88) — i.e. **water gets a thinning prescription.**

**4.3 — config-path policy is violated by both stages.** `config/data_paths.yaml` opens with
"All downstream pipeline code reads paths from here — no hardcoded `/mnt/d/` in scripts."
`sketch_management_units.py:275-279` hardcodes six input paths under `data/raw`, and
`paint_fvs_to_raster.py:28` hardcodes `/mnt/d/TreeMap_Chaz`. Neither reads `data_paths.yaml`.
Everything is unrunnable anywhere but the one workstation, and the config file's stated
contract is fiction.

---

## 5. Docstrings and comments that don't match reality

Worth fixing because several of these are load-bearing — someone will trust them.

1. **`scripts/LETO.V1.1.txt` and `scripts/LETO_CSV_PIPELINE.txt` do not exist on this
   branch.** Cited by `sliver_merge.py:9`, `assign_plt_cn.py:7`, `build_fvs_inputs.py:9`.
   They're on `origin/scripts/leto-workflow`. **Recommend merging that branch's `scripts/`
   into `main`** — the ports are unreviewable without the source, and this is exactly the
   "may not be entirely in the repository" gap.
2. **`pipeline/README.md` documents 2 of 9 modules.** It says "the committed pipeline
   currently contains two implemented slices" and lists only `sketch_management_units.py`
   and `paint_fvs_to_raster.py`. Seven modules landed after it was written.
3. **`sliver_merge.py:24-28`** — the `GenerateNearTable` justification for the `drop`
   default is wrong (§2.3).
4. **`assign_plt_cn.py:9`** — "max-area cell assignment" describes LETO, not the port
   (§3.10).
5. **`build_fvs_inputs.py:6-7`** — claims to use `FL_FVS_STANDINIT_PLOT.csv`; it doesn't
   (§3.7).
6. **`regime_templates.py:22-23`** — "The schedule/DataBase scaffolding mirrors the verified
   keyfiles exactly." It does not (§3.3, §3.4).
7. **`sketch_management_units.py:43`** — comment lists six county names for five codes
   (§3.2).
8. **`sketch_management_units.py:55-58`** — cites the FL BMP Manual for a mapping that
   silently omits the manual's third buffer tier (§3.8).
9. **`notes/terminology.md`** describes sliver-merge as "conserving area" and quotes
   17,020 → 2,442 for Union County. That's the `merge` policy; the shipped default is
   `drop` (§2.3). The note and the code disagree about what the pipeline does.

---

## 6. What survives the direction change, and suggested order

**Keep as-is:** `tpo_targets.py` (clean, verified against the real workbook, well-documented
including the "Suwanee" quirk), `assign_plt_cn.py`'s `build_weighted_plt_cn` (pure, tested,
and *more* central under a trajectory library), `paint_fvs_to_raster.py`'s reclassification
core (`reclassify_by_key` is correct and scales), `sliver_merge.py`'s merge machinery (the
union-find + shared-boundary + nearest-fallback logic is solid; only the default policy is
in question).

**Suggested order:**

1. **Merge `origin/scripts/leto-workflow`'s `scripts/` into `main`.** Everything else is
   guesswork without it. (§5.1)
2. **Fix the two blockers that stop anything from running** — the EVT mask (§3.1) and the
   pilot county list (§3.2) — *or* skip §3.1 entirely by porting LETO's delineation, which
   deletes that code path. Decide §1.1 first.
3. **Fix the keyfile schedule block** (§3.3, §3.4) and add a test asserting keyword field
   columns, not just `ThinDBH`'s. This one is cheap and silently corrupts every run.
4. **Decide the SMZ design** (§1.5) — erase / attribute / separate polygons, and
   handbook-practice vs. absolute no-entry — then implement one and delete the other two.
   `classify_stream_fcode` needs the perennial-large tier and a waterbody buffer either way
   (§3.8).
5. **Close the four broken seams** (§4): produce `MU_ID`, `SMZ_Pct`, `OWN_CODE`, `FORTYPCD`
   from somewhere, and make `regime_assignment` **raise** on missing inputs instead of
   defaulting to `thin_from_below`. Same for the scheduler's caps keys (§3.5).
6. **Re-enable carbon** (§1.4) once someone confirms the restart-fidelity finding doesn't
   apply to continuous runs. This is free capability recovered by dropping iterative
   coupling.
7. **Write down the Diaz et al. design** — objective function, move set, cooling schedule,
   hard-vs-soft constraint treatment — in `notes/` before coding it. Then rebuild the
   scheduler around it, reusing `_build_budgets` as the constraint evaluator and
   `allocate_cycle` as the initial-solution generator (§1.3).
8. **Fix site variables in `StandInit`** (§3.7) and the `INV_YEAR` stamp (§2.10). Under the
   library design this is mostly "use the plot's own `FVS_STANDINIT_PLOT` row."
9. **Regeneration** (§3.6) — verify the `PLANT`/`NATREGEN` field layouts the same way
   `ThinDBH` was verified, or constrain clearcut years so nothing is cut inside the horizon.
10. **Retire the iterative-coupling residue** (§1.4): `main.py`, the trigger views in
    `notes/duckdb-iterative-coupling-cells.md`, the two orchestrator specs.
11. **Update `pipeline/README.md`** to cover all nine modules and the actual architecture.
