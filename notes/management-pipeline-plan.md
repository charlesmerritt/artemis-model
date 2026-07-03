# Management Pipeline Plan — From No-Management Baseline to Constrained Harvest Simulation

Build a spatially explicit harvest scheduling prototype for the 5-county Florida AOI that uses the completed FVS no-management baseline as standing inventory, TPO reports as harvest volume constraints, and ownership/county boundaries as constraint dimensions, then runs managed FVS simulations per management unit.

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
  - Riparian buffer units → no harvest or very light thinning
- **Output**: `pipeline/s3_management/regime_assignment.py` with `assign_regime(unit_attrs) -> (regime_name, params)`.

---

## Phase 4: Constrained harvest scheduling prototype

### Step 4.1: Build the harvest scheduling engine
- Core logic in `pipeline/s3_management/harvest_scheduler.py`:
  1. Load management units with attributes and baseline inventory.
  2. Load TPO volume targets (annual or multi-year average).
  3. For each time step (5-year FVS cycle):
     - Compute available volume per unit = standing volume from FVS trajectory.
     - Select units for harvest based on regime assignment + priority (oldest stand age first).
     - Simulate harvest: compute volume removed per unit.
     - Check constraints: total, by county, by owner group, by county × owner group.
     - If over target, reduce harvest (drop lowest-priority units) until within constraint.
     - If under target, add more units (if available).
  4. Output: per-unit harvest schedule (unit_id, cycle, regime, volume_removed).
- **Verify**: scheduled harvest volumes are within TPO constraints for all four constraint levels.

### Step 4.2: Generate managed FVS keyfiles from the schedule
- For each unique `(stand_cn, regime, params)` combination in the schedule, render a FVS keyfile using the regime templates.
- Reuse `generate_smoke_keyfiles.py` pattern but with management keywords.
- **Output**: `data/interim/fvs/managed_keyfiles/manifest.csv` + per-stand keyfile directories.

### Step 4.3: Run managed FVS simulations
- Run through Windows FVS GUI (same handoff pattern as baseline).
- Alternatively, investigate Docker-based fvs2py runtime if available.
- **Output**: `data/interim/fvs/managed_outputs/FVSOut.db` + trajectory CSVs.

### Step 4.4: Compare managed vs. baseline trajectories
- Load both trajectory sets.
- Compute differences: volume removed, residual standing inventory, growth response.
- Summarize by county, owner group, forest type.
- **Output**: notebook `notebooks/Managed_vs_Baseline_5co_FL.ipynb` with summary tables and plots.

---

## Phase 5: Iteration and scaling

### Step 5.1: Sensitivity analysis
- Vary TPO constraint levels (all years vs. 2013-2024 average).
- Test single-constraint vs. multi-constraint scenarios.
- Document how constraints interact (county constraint may bind before owner group constraint).

### Step 5.2: Scaling path
- Document what changes for statewide Florida (county count, stand count, compute time).
- Identify bottlenecks: FVS runtime (trajectory library approach vs. per-stand runs), vector overlay performance, raster sampling.
- The trajectory library approach (run FVS once per unique `(plot_id, regime, si_bin)`, paint to pixels) becomes essential at scale.

---

## Implementation order (recommended)

1. **Step 1.1** — parse TPO spreadsheet → config  *(small, unblocks everything)*
2. **Step 1.2** — load FVS baseline trajectories + linkage → Parquet  *(small)*
3. **Step 2.2** — assign ownership to stands via raster sampling  *(medium, needs ownership raster)*
4. **Step 1.3** — compute standing inventory by constraint dimensions  *(medium)*
5. **Step 2.1** — bring `sketch_management_units.py` to main, run 5 counties  *(medium)*
6. **Step 2.3** — build unit × stand crosswalk  *(medium)*
7. **Step 3.1** — define regime templates  *(medium)*
8. **Step 3.2** — assign regimes by ownership × forest type  *(small)*
9. **Step 4.1** — build harvest scheduling engine  *(large, core deliverable)*
10. **Step 4.2** — generate managed keyfiles  *(medium)*
11. **Step 4.3** — run managed FVS  *(external dependency on Windows FVS)*
12. **Step 4.4** — compare managed vs. baseline  *(medium)*

Steps 1-8 can be implemented and verified without running FVS again. Steps 9-12 require the managed FVS run, which depends on the Windows GUI or a working Linux FVS runtime.

---

## Key design decisions (confirmed)

1. **Constraint hierarchy**: Test each constraint level independently first to understand individual effects, then combine. Build the scheduler to support both modes.
2. **Harvest priority**: Oldest stand age first. Must compute area-weighted average stand age across all FIA plots (TreeMap pixels) within each management unit. This requires the unit×stand crosswalk (Step 2.3) plus stand age from FVS trajectory cycle 0.
3. **Stand age aggregation**: A management unit may span multiple TreeMap pixels, each imputed to a different FIA plot with a different stand age. Compute `unit_age = sum(stand_age_i × pixel_acres_i) / sum(pixel_acres_i)` for each unit.
4. **FVS runtime**: Continue with Windows GUI handoff (proven path). Investigate Docker-based fvs2py as a stretch goal for automation.
5. **Management unit granularity**: Keep parcel-based units from `sketch_management_units.py` for the prototype. Raster-based segmentation is a future improvement.
6. **Time step**: Use FVS 5-year cycles as the scheduling time step. This matches the natural FVS output unit and the projection config.
