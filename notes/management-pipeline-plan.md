# Management Pipeline Plan — From No-Management Baseline to a Scheduled Landscape

Build a spatially explicit harvest scheduling prototype for the 5-county Florida AOI that uses the completed FVS no-management baseline as standing inventory, TPO reports as harvest volume constraints, and ownership/county boundaries as constraint dimensions — then **generates a library of candidate trajectories per management unit from its ownership class, and selects one trajectory per unit by simulated annealing**.

> **Architecture note (2026-08-06).** Phases 1 and 2 below are unchanged — the data integration and spatial layers are needed either way. Phases 3–5 were rewritten: management is no longer one deterministic regime per unit fed to a greedy per-cycle allocator. Ownership class now defines an *eligible set*, FVS enumerates every eligible alternative up front, and the scheduler chooses among precomputed trajectories. See [`trajectory-library-and-annealing.md`](trajectory-library-and-annealing.md) for the design and [`../docs/references/README.md`](../docs/references/README.md) for the two guiding papers (`LAMPS`, `CLIMATE-FVS`).

---

## Current state summary

- **FVS no-management baseline**: Complete. 693 stands, 9,260 trajectory rows, FVS SN variant FS2026.1, ~50-year projections. Output CSVs in `FVS/fvs-outputs/` (zipped).
- **TreeMap-FVS linkage**: `TreeMap_FVS_linkage.csv` (688 rows) maps TM_ID → PLT_CN → STAND_CN → STAND_ID → pixel count/acres.
- **Ownership raster**: Harris et al. 2025, 30m, 7 forest classes. Path in `config/data_paths.yaml`.
- **TPO harvest guidance**: `data/raw/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx`. Two sheets:
  - `ByOwnerGroup`: Federal NF ~1.77M, Other public ~3.97M, Private ~66.3M, All ~72.05M cuft/yr
  - `ByCounty`: Baker ~11.76M, Columbia ~17.80M, Hamilton ~15.33M, Suwannee ~18.47M, Union ~8.70M cuft/yr
- **FVS infrastructure**: FVS is installed in Linux and can be run from command line. FVS lives in the ~/projects/ForestVegetationSimulator directory.

---

## Phase 1: Data integration — standing inventory + TPO constraints

### Step 1.1: Load and structure TPO harvest targets
- Parse `Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx` into a clean YAML/Parquet config (`config/tpo_targets.yaml` or `data/interim/tpo_targets.parquet`).
- Structure: nested dict with `owner_group` and `county` dimensions, both averaging periods (all years, 2013-2024).
- Add `openpyxl` to `pyproject.toml` dependencies.
- **Verify**: unit test confirms parsed targets match spreadsheet values.

### Step 1.2: Load FVS baseline trajectories into analysis-ready format
- Load `fvs_trajectory.csv` from the extracted FVS output zip.
- Join to `TreeMap_FVS_linkage.csv` on `stand_cn` / `stand_id` to get pixel counts and acres per stand.
- Compute per-stand sampling weights (pixel acres / total acres) for area-expansion.
- Store as `data/interim/fvs/baseline_trajectories.parquet` (one row per stand × cycle).
- **Verify**: row counts match source (693 stands, 9,260 trajectory rows); all stands have linkage rows.

### Step 1.3: Compute standing inventory summaries by constraint dimensions
- Aggregate baseline trajectories to:
  - Total standing volume (merch cuft, total cuft, board ft) by year, by county, by owner group, by county × owner group.
  - Per-acre averages weighted by pixel acres.
- County assignment: join via `TreeMap_FVS_linkage.csv` COUNTY field (already present).
- Owner group assignment: spatial join of TreeMap pixel centroids to ownership raster (Step 2.2).
- **Output**: `data/interim/inventory/baseline_inventory_by_dimension.parquet`.
- **Verify**: total standing volume across all dimensions reconciles to the same grand total.

---

## Phase 2: Spatial layers — management units + ownership

### Step 2.1: Bring `sketch_management_units.py` into main branch
- Copy from `.claude/worktrees/mgmt-units-research/pipeline/s3_management/sketch_management_units.py` to `pipeline/s3_management/`.
- Run for all 5 pilot counties, save outputs to `data/interim/management_units_5co/`.
- **Verify**: Union County output matches previous smoke test (17,020 polygons before splitting).

### Step 2.2: Assign ownership and county to management units
- Raster-sample the ownership raster at each management unit centroid (or zonal majority for larger units).
- Assign county via parcel `CNTYNAME` field already in the unit polygons.
- Assign FVS stand linkage: spatial join management units to TreeMap pixels → inherit `stand_cn`, `stand_id`, forest type, and baseline trajectory.
- **Output**: `data/interim/management_units_5co/units_with_attributes.gpkg` with columns: `unit_id`, `county`, `owner_group`, `forest_type`, `area_ha`, `stand_cn`, `stand_id`, `baseline_volume_year0`, `baseline_volume_year50`.
- **Verify**: every forested unit has an owner group and county; area totals by county match AOI.

### Step 2.3: Build the management unit × FVS stand crosswalk
- Many management units will share the same FVS stand (same TreeMap plot imputed across multiple pixels).
- Build a crosswalk: `unit_id → stand_cn, stand_id, pixel_acres_in_unit`.
- This enables running FVS once per unique stand and distributing results to units.
- **Output**: `data/interim/management_units_5co/unit_stand_crosswalk.parquet`.

---

## Phase 3: Management regime library

### Step 3.1: Define regime templates
- Define 4-6 FVS keyword templates as parameterized text (extending `keyword_builder.py`):
  1. **no_management** — already have this (baseline)
  2. **clearcut** — harvest all trees at a target year, optionally replant
  3. **thinning_from_below** — remove a target BA percentage at a target year
  4. **shelterwood** — partial harvest + removal cut after regeneration
  5. **selection_harvest** — periodic partial removals
  6. **pine_plantation_rotation** — site prep, plant, thin, clearcut on rotation (industrial)
- Each template parameterized by: harvest year, intensity (BA% removed, TPA target), regeneration method.
- **Output**: `pipeline/s4_fvs/regime_templates.py` with `render_keyfile(stand, regime, params)`.

### Step 3.2: Assign default regimes by ownership × forest type
- Simple deterministic mapping:
  - Federal/state → conservative (selection or no harvest)
  - Family forest → light thinning
  - Corporate → pine plantation rotation (if pine) or clearcut (if hardwood)
  - Riparian buffer units → **no harvest, ever** (no entry of any kind, no buffer class exempted). Assigned by geometry, so it overrides any ownership/forest-type rule above it. Buffers are still projected and reported as their own polygons — see [`methodology-directions.md`](methodology-directions.md) item 2.
- **The full mapping now lives in [`config/management_regimes.yaml`](../config/management_regimes.yaml)**, with the reasoning in [`management-regimes-by-owner.md`](management-regimes-by-owner.md). It carries all **seven** Harris forest classes separately (per `PLAN.md` §3c) rather than the four-way `PUBLIC_OWNERS` collapse the sketch above implies, plus each class's eligible regime set and its key in the TPO and LAMPS owner vocabularies.
- **Output**: `pipeline/s3_management/regime_assignment.py` with `assign_regime(unit_attrs) -> (regime_name, params)`. The module still hardcodes its own copy of the mapping; `tests/test_config.py` asserts it agrees with the config until the loader lands.

**Superseded by config, 2026-08-03.** The mapping above is now declared in
[`config/management_regimes.yaml`](../config/management_regimes.yaml) rather than written into the module, and
it carries two things this step did not: an **owner class** resolved from the Harris raster
against the parcel layer (`config/ownership_policy.yaml`, seven classes — the corporate
class is split into industrial vs. other-corporate), and an **eligible menu** of 2-3
prescriptions per owner class alongside the single default, so the Phase 4 scheduler has
something to choose among. State pine now defaults to a restoration thin and local
government to no management; federal and family are unchanged. See
[`docs/config-policy.md`](../docs/config-policy.md).

---

## Phase 4: Trajectory library and simulated-annealing scheduling

### Step 4.1: Assign ownership class to each unit
- Dominant-owner vote over the Harris raster within each unit footprint, with a confidence threshold (e.g. >70% of pixels). Sub-threshold units are **excluded and logged** — a reviewable list, never a silent drop.
- **Verify**: every retained unit has one ownership class; the excluded list is written and counted.

### Step 4.2: Enumerate and generate the trajectory library
- For every unit, expand its eligible set from `config/prescriptions.yaml` into concrete prescriptions (grids expand as a cartesian product), then render one FVS keyfile per `(unit, prescription)`.
- Run each as **one continuous FVS simulation — no restart barrier**. Runs are independent, so this is embarrassingly parallel; use the concurrent worker pattern proven in `research/restart_fidelity/parallel_demo.py`.
- Cache on a content hash of `(tree list, site attributes, prescription)`. Dedup is a cache, never a reporting decision.
- Budget: target **6–12 trajectories per unit**; the 5-county pilot (order 10⁴ units) is then order 10⁵ runs, a one-time cost per library version.
- **Output**: `trajectory_index` (one row per trajectory — the scheduler's working set) and `trajectory_cycles` (one row per trajectory × cycle) in DuckDB/Parquet. Built from raw `FVS_Summary2` using the view vocabulary in [`duckdb-iterative-coupling-cells.md`](duckdb-iterative-coupling-cells.md).
- **Verify** (library integrity, on every build): ≥2 trajectories for every non-riparian unit and exactly 1 for every riparian unit; exactly `n_cycles + 1` state rows per trajectory after duplicate removal rows are collapsed, with no NaNs in objective columns; unit layer and library cover each other in **both** directions; every prescription is a member of its class's eligible set.

### Step 4.3: Build the simulated-annealing scheduler
- Core logic in `pipeline/s3_management/harvest_scheduler.py`, alongside the existing greedy allocator:
  1. Load `trajectory_index` and the TPO caps (`config/tpo_targets.yaml`).
  2. Seed the initial solution from the greedy oldest-first allocator.
  3. Iterate: propose a move (single-stand reassignment / adjacency-block reassignment / period swap), score the resulting plan, accept by Metropolis, cool geometrically.
  4. Return the best plan plus its quality report.
- Constraint split — **absolutes are structural, targets are priced**:
  - Structural (unrepresentable, so unselectable): riparian no-entry, minimum harvest age, reserve status, operability.
  - Priced as penalties: TPO caps by total/county/owner group, even flow within an ownership class, adjacency and green-up, maximum contiguous opening size, treatment budget.
- Parameters live in `config/projection.yaml` under `harvest.annealing`, `harvest.objective`, and `harvest.penalties`. `T₀` is calibrated at run start to a target acceptance rate rather than hardcoded.
- **Verify**: determinism under a fixed seed; monotone best-so-far objective; structural constraints honoured exactly; final objective ≥ the greedy baseline.

### Step 4.4: Report the plan (a plan is not a result without this)
- Simulated annealing gives no optimality guarantee, so every plan ships with: the objective value; the **full constraint-violation vector** per dimension per cycle; an **objective-specific relaxation bound** (per-stand only for separable objectives, aggregate-preserving for coupled even-flow forms, or explicitly unavailable); the **greedy and random baselines**; and the **objective spread across seeds**.
- A plan that does not beat greedy is a finding about the search, and must be reported as one rather than tuned until it goes away.
- **Output**: `selected_plan.parquet` (`unit_id` → `trajectory_id`) + `scheduler_report.json`.

### Step 4.5: Compare the selected plan against the baseline
- Join the plan to `trajectory_cycles`; compute volume removed, residual standing inventory, and growth response against the no-management baseline.
- Summarize by county, owner group, forest type; check age-class distribution through time and opening-size distribution against green-up rules.
- **Output**: notebook `notebooks/Scheduled_vs_Baseline_5co_FL.ipynb`.

---

## Phase 5: Iteration and scaling

### Step 5.1: Scenario and sensitivity analysis
- **This is where the architecture pays off.** A new objective, a new weight set, or a different TPO constraint level is a re-run of Step 4.3 — seconds to minutes — not a re-run of FVS. Only a change to the *eligible sets or parameter grids* forces a library rebuild.
- Vary TPO constraint levels (all years vs. 2013-2024 average); test single- vs. multi-constraint scenarios; document which constraint binds first (county may bind before owner group).
- Vary objective weights and report the efficient frontier between harvest volume and retained carbon.

### Step 5.2: Scaling path
- Document what changes for statewide Florida (county count, unit count, library size, compute time).
- The dominant cost moves from scheduler runtime to **library generation**: run count is `units × prescriptions per unit`, and grid growth is multiplicative. Keeping grids small and justified is the scaling lever.
- Other bottlenecks: vector overlay performance, raster sampling, and holding `trajectory_index` in memory at statewide scale.

---

## Implementation order (recommended)

1. **Step 1.1** — parse TPO spreadsheet → config  *(small, unblocks everything)*
2. **Step 1.2** — load FVS baseline trajectories + linkage → Parquet  *(small)*
3. **Step 4.1 / 2.2** — assign ownership class to units via raster sampling  *(medium, needs ownership raster)*
4. **Step 1.3** — compute standing inventory by constraint dimensions  *(medium)*
5. **Step 2.1** — bring `sketch_management_units.py` to main, run 5 counties  *(medium)*
6. **Step 2.3** — build unit × stand crosswalk  *(medium)*
7. **Step 3.1** — prescription templates  *(medium)* ✅ done
8. **Step 3.2** — ownership class → eligible set  *(small; pure function, synthetic fixtures)*
9. **Step 4.2** — enumerate and generate the trajectory library  *(large; the FVS-dependent step)*
10. **Step 4.3** — simulated-annealing scheduler  *(large, core deliverable)*
11. **Step 4.4** — quality report  *(medium; do not defer — it is what makes step 4.3 reviewable)*
12. **Step 4.5** — compare against baseline  *(medium)*

Steps 1–8 and 10–11 can be implemented and verified without running FVS: the scheduler is testable end to end against a synthetic library with a known optimum. Only step 9 needs the Windows GUI or a working Linux FVS runtime.

---

## Key design decisions (confirmed)

1. **Ownership decides the menu, not the meal.** Ownership class selects a *set* of eligible prescriptions; the scheduler selects within it. This is the core change from the deterministic one-regime-per-unit rule.
2. **Constraint hierarchy**: Test each constraint level independently first to understand individual effects, then combine. The scheduler supports both modes.
3. **Absolutes are structural, targets are priced.** A penalty the search can pay is the right model for a volume cap and the wrong model for a no-harvest buffer.
4. **Greedy is retained** as the annealer's initial solution *and* as a reported baseline — not as dead code.
5. **Stand age aggregation**: A management unit may span multiple TreeMap pixels, each imputed to a different FIA plot with a different stand age. Compute `unit_age = sum(stand_age_i × pixel_acres_i) / sum(pixel_acres_i)`. Still needed — it drives the greedy seed and the eligibility screens.
6. **FVS runtime**: Continue with Windows GUI handoff (proven path). Investigate Docker-based fvs2py as a stretch goal — it matters more now, since library generation is the bulk of the compute.
7. **Management unit granularity**: Keep parcel-based units from `sketch_management_units.py` for the prototype. Raster-based segmentation is a future improvement.
8. **Time step**: FVS 5-year cycles as the scheduling time step. Matches the natural FVS output unit and the projection config.

See [`methodology-directions.md`](methodology-directions.md) for the 2026-07-27 advisor-meeting follow-ups that touch this plan. Item 1 (tree-list aggregation) is resolved by the weighted-union initialization described in [`trajectory-library-and-annealing.md`](trajectory-library-and-annealing.md) §4; item 2 (riparian buffers as separate unmanaged-but-growing units) is preserved and strengthened by Step 3.2 above.
