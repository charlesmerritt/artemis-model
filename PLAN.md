# Eastern US Forest Projection Pipeline — v1 Build Plan

> **Architecture (adopted 2026-08-06).** ARTEMIS builds a **library of candidate
> trajectories for every stand**, where the stand's **ownership class** determines which
> management prescriptions are eligible for it. FVS runs once per `(stand, prescription)`
> pair, offline and without restart barriers. A **harvest scheduler then uses simulated
> annealing** to select one trajectory per stand subject to volume, flow, adjacency, and
> reserve constraints. Simulation enumerates what each stand *could* do; the scheduler
> decides what each stand *will* do.
>
> Read [`notes/trajectory-library-and-annealing.md`](notes/trajectory-library-and-annealing.md)
> first — it is the design of record, and §3c/§4 below are its build-plan form.
>
> **Guiding references** — consult before changing §3c, §4, or §5. See
> [`docs/references/README.md`](docs/references/README.md).
> **`CLIMATE-FVS`** (Diaz et al. 2015, Ecotrust) is the end-to-end precedent: the same
> batch-simulate-then-anneal design, applied to western Oregon BLM lands, with published
> code for both halves. **`LAMPS`** (Bettinger & Lennette et al.) supplies the eligibility
> and adjacency/green-up machinery Diaz et al. did not need.

## Scope notes for the agent
- **In scope:** deterministic, pixel-level forward projection using FVS Southern variant, initialized from TreeMap 2022 + FIA tree lists, with management selected by constrained optimization over a precomputed trajectory library.
- **Out of scope (v1):** natural disturbance overlays (hurricane, SPB, fire, ice), climate-modified growth, stochastic Monte Carlo replicates, uncertainty quantification.
- **Target resolution:** 30m pixels.
- **Target extent:** Florida first (FIPS 12); expand to full eastern US once pipeline is validated.
- **Target horizon:** 50 years, 5-year FVS cycles.

---

## 0. Project scaffolding (do this first)
- Define the spatial extent precisely: list of state FIPS codes or a bounding polygon; commit as `config/extent.geojson`.
- **CRS — decided.** Everything is **EPSG:5070, NAD83 / Conus Albers** (ArcGIS: `NAD_1983_Contiguous_USA_Albers`): every raster, every vector, every output. Equal-area and in metres, so acres and hectares come straight from geometry. Snap grid is the TreeMap 2022 affine `[30, 0, -2361585, 0, -30, 3177435]` — note the origin is half a pixel off the round 30 m grid, so exports must pass `crsTransform=`, never `scale=`. Declared once in `config/projection.yaml`, read through `pipeline/spatial_ref.py`, hardcoding blocked by test. Do not substitute `ESRI:102008` (North America Albers, parallels 20/60 — off by kilometres) or `EPSG:6350` (NAD83(2011) — off by under a metre, still breaks the snap grid); both look right on a map.
- Set up storage: Zarr or Cloud-Optimized GeoTIFF for raster cubes; Parquet for tabular FIA joins and FVS outputs; PostGIS optional for vector ops.
- Establish a chunking convention (e.g., HUC8 or 1° tiles) so nothing has to be processed CONUS-wide in memory.
- **Compute stack:** Google Earth Engine (raster acquisition, clipping, terrain/climate derivatives, LCMS, segmentation inputs) + local workstation (FIA SQL joins, FVS runs, Python pipeline, Zarr/Parquet assembly) + campus HPC (FVS trajectory library at scale, once pipeline is proven locally). Do not architect for HPC until a clean local job exists.
- **Parallelism:** GEE native parallelism for raster ops; GNU parallel or Python `multiprocessing` for local FVS runs; SLURM array jobs when promoted to HPC.

---

## 1. Initial state layer

### 1a. TreeMap acquisition and display
- Download **TreeMap 2022 CONUS** (Houtman et al. 2025) from the USFS Research Data Archive (doi:10.2737/RDS-2025-0032), not the 2016 version.
- Clip to project extent; reproject to project CRS; verify pixel alignment.
- Confirm the band of interest is the FIA plot identifier (`tm_id` or equivalent — check data dictionary).
- Display in ArcGIS / QGIS as sanity check; confirm forest mask matches expectation.
- Output: `treemap_2022_clipped.tif` (single-band raster of plot IDs).

### 1b. FIA join and FVS-ready tree lists
- FIA source: **full CONUS FIA SQLite DB** (already downloaded locally). Query directly via SQL — no DataMart API calls needed.
- Join TreeMap plot IDs to FIA tables on `PLT_CN`. Verify join rate — expect >95% match; investigate misses.
- Build the FVS input format. Two paths:
  - Easier: use **FIA2FVS** (USFS-distributed tool) to convert FIA records into FVS-ready `.db` files.
  - Manual: build SQLite databases matching the **FVS-Ready DB schema** documented in the Open-FVS source tree.
- Validate every unique plot has the FVS-required fields: site index, slope, aspect, elevation, forest type code, region/variant code. Flag and patch missing values.
- Output: `fvs_input.db` (one row per unique TreeMap plot ID) + `pixel_to_plot.parquet` (pixel → plot lookup).

### 1c. Optional cross-check
- Sample N pixels, derive biomass from the inherited FIA tree list, compare against **BIGMAP** total aboveground biomass at the same pixels. Document agreement / systematic bias. This is the cheapest validation you can do before running anything forward.

---

## 2. Per-pixel site attributes

### 2a. Soils
- Acquire **POLARIS** via GEE community catalog (gee-community-catalog.org/projects/polaris). Pull depth, AWC, clay, sand, pH. Already 30m — reproject and snap to TreeMap grid only.
- If SSURGO is required for agency reasons, rasterize via `gSSURGO` 30m grids.
- Reproject to project CRS, snap to TreeMap grid.

### 2b. Terrain
- Acquire **3DEP** 1/3 arc-second DEM (10m) from USGS, resample to 30m matching the TreeMap grid.
- Derive slope (%), aspect (degrees), elevation (m) as separate rasters.
- Optionally derive TPI, TWI for site quality modeling.

### 2c. Climate normals
- Acquire **PRISM** 30-year normals (1991-2020): mean annual temperature, mean annual precipitation, growing season length, frost-free days.
- Resample from PRISM native 800m to 30m via bilinear; document the scale mismatch in metadata.

### 2d. Per-pixel site index
- **Default path:** inherit site index from the TreeMap-imputed FIA plot (FIA's `SITETREE` table gives species-specific site index per plot).
- **Better path:** fit a site index regression from FIA `SITETREE` values against soil + terrain + climate covariates, predict per-pixel SI for the dominant species. This gives smoother SI gradients and avoids inheriting the plot's exact value across thousands of pixels assigned to it.
- Output: `site_index.tif` keyed to dominant species or species group.

### 2e. Stack and chunk
- Assemble all site rasters into a single chunked Zarr store aligned to TreeMap.
- Output: `site_attributes.zarr` with bands `soil_awc`, `clay_pct`, `slope`, `aspect`, `elev`, `tmean`, `precip`, `site_index`.

---

## 3. Management and ownership layers

### 3a. Stand boundary delineation
- Inputs: LCMS land cover, TIGER roads, NHD streams/rivers, LANDFIRE EVT (Existing Vegetation Type), ownership boundaries (if available).
- Approach: segmentation rather than naive intersection. Use either:
  - **eCognition-style multi-resolution segmentation** on the stacked raster (open-source: `scikit-image` `felzenszwalb` or `slic`, or `OTB` segmentation modules).
  - Or **GRASS GIS `i.segment`** for region-growing segmentation, which handles raster inputs natively and scales better than vector intersection.
- Constrain stand sizes (min ~2 ha, max ~40 ha typical for the Southeast).
- Validate against a few hand-digitized reference stands.
- Output: `stands.gpkg` (polygons) and `stand_id.tif` (raster of stand IDs).

### 3b. Variable-width stream buffers
- Pull NHD flowlines for the project extent. Use NHDPlus HR if available, NHD Medium otherwise.
- Classify by NHD `FCode` and Strahler order:
  - Headwater / ephemeral / intermittent → 30 ft buffer
  - Perennial small stream → 50 ft buffer
  - Larger perennial stream → 100 ft buffer
  - River / waterbody adjacency → 200 ft buffer
- BMP rules are **state-specific**. For Florida v1: use Florida Forest Service BMP Manual (2020 edition). Buffer widths:
    - Intermittent / ephemeral → 35 ft each side
    - Perennial < 15 ft wide → 50 ft each side
    - Perennial ≥ 15 ft wide → 75 ft each side
    - Lakes and ponds → 75 ft
  - Store rules as `config/bmp_rules.yaml` keyed by state FIPS; add additional states at expansion time.
- Output: `riparian_buffer.tif` (categorical: buffer class per pixel).
- **Implemented 2026-08-03:** buffers are **retained**, not erased. `sketch_management_units`
  builds the BMP buffer layer and uses it to partition the eligible forest into
  `unit_class = "managed"` and `unit_class = "riparian"` units, each riparian unit carrying
  its `buffer_class`. `sliver_merge` then treats `unit_class` as a hard constraint: a 35–75 ft
  buffer is below the 5-acre minimum stand size almost everywhere, so merging across the line
  would put unharvestable acres inside a harvest unit. Area is accounted in
  `area_accounting.csv`, with permanently-excluded acres on their own lines.

### 3c. Ownership assignment and prescription eligibility
- **Ownership assignment per pixel:**
  - Source: **Harris, Caputo & Butler (2025)** — *Forest ownership in the conterminous United States circa 2022: distribution of seven ownership types.* USFS Research Data Archive. doi:10.2737/RDS-2025-0045.
  - Native resolution: **30m** — pixel-perfect alignment with TreeMap 2022; reproject and snap only, no resampling of class values.
  - Vintage: **circa 2022** — temporally co-registered with TreeMap 2022. These two datasets were designed to be used together.
  - Nine raster values: `unknown_forest`, `non_forest`, `water`, `family_forest`, `corporate_forest`, `tribal_forest`, `federal_forest`, `state_forest`, `local_forest`.
  - `non_forest` and `water` pixels masked from FVS pipeline entirely.
  - Each of the seven forest ownership classes gets its own prescription library (no collapsing).
  - Output: `ownership_class.tif` (9-value categorical, reprojected to EPSG:5070 snapped to TreeMap grid).

- **Ownership assignment per stand:**
  - A management unit spans many pixels and can straddle an ownership boundary. Assign by **dominant-owner vote with a confidence threshold** (e.g. >70% of the unit's pixels): below threshold, exclude the unit and **log it**. Excluded stands are a reviewable list, never a silent drop.
  - Output: `ownership_class` on the unit table — the key that selects the unit's prescription library.

- **Prescription eligibility (ownership decides the menu, not the meal):**
  - Ownership class maps to a **set** of eligible prescription families and their parameter grids, not to a single regime. Authoritative mapping: [`config/prescriptions.yaml`](config/prescriptions.yaml).
  - `no_management` is in every non-riparian library — a stand must always be allowed to grow untreated, or a binding volume cap has no feasible answer.
  - Riparian units (BMP stream-management zone by geometry) get a library of **exactly one** trajectory, `no_management`. No entry, ever, enforced by the absence of an alternative rather than by a constraint the search could violate.
  - Eligibility screens (minimum harvest age, reserve status, operability) **remove** prescriptions at build time and never add any. A library that screens down to `{no_management}` is a valid outcome and must be logged.
  - Public classes exclude clearcut in v1; the tribal and unknown-ownership sets are conservative placeholders pending a documented source.

- **LCMS harvest evidence (calibration, not generation):**
  - Training data: per-pixel LCMS Change product 1985-2024, filtered to "Tree Removal".
  - Features: stand age (from TreeMap plot), forest type group, ownership class, county, year, time since last disturbance.
  - **Role change.** The forward harvest schedule now comes from the scheduler (§4d), so a fitted `P(harvest | features)` model is no longer the generator. It becomes the **observed-behaviour target** the selected plan is checked against — harvest rates and age distributions by ownership class, county, and year.
  - **Development order: growth first, harvest second.** Validate FVS growth trajectories against FIA remeasurements before evaluating any schedule. This isolates growth model error from scheduling error.
  - Output: observed harvest-rate tables by `(ownership, county, year, age class)`, used in §3d and §5.

### 3d. Hindcast validation of the scheduler
- Hold out the most recent 10 years of LCMS (2015-2024).
- Build the trajectory library from the 2015 state of the landscape and run the scheduler with the explicitly selected 2013-2024 TPO average.
- Compare the selected plan against observed LCMS Tree Removal:
  - Total area harvested per year, per state, per ownership class
  - Spatial pattern agreement (Cohen's kappa or AUROC at pixel level)
  - Age distribution of harvested pixels
- Document systematic bias. Disagreement localizes to one of two inspectable places — the **eligible prescription sets** (wrong menu) or the **objective weights** (wrong preferences) — which is the diagnostic advantage over a fitted probability surface.

---

## 4. FVS execution pipeline — trajectory library and scheduling

### 4a. FVS wrapping
- Install **Open-FVS** (the actively maintained open-source FVS). Confirm Southern variant is available; some Atlantic states need other variants — document which variant per state.
- Build a Python wrapper that:
  - Reads a tree list + site attributes + management keyword file
  - Calls the FVS binary
  - Parses the output cycle reports into a tidy dataframe
- Use `pyFVS` or `rFVS` if you'd rather not build from scratch; both wrap the binary cleanly.

### 4b. Management regime keyword files
- Define ~6-10 regimes as parameterized FVS keyword templates:
  - No management
  - NIPF light (occasional partial harvest)
  - Industrial pine plantation (site prep, plant, thin, clearcut on rotation)
  - Industrial hardwood / mixed
  - Public conservative management
  - Riparian (**no entry, ever**; still grown and reported as unique buffer polygons — see `notes/methodology-directions.md`)
- Each regime gets selected per pixel by a deterministic function of `(ownership, forest type, riparian buffer class, stand age)`.
- **Owner class → regime is specified in `config/management_regimes.yaml`** (reasoning: `notes/management-regimes-by-owner.md`): one default regime and one eligible regime set per ownership class from §3c, all seven carried separately, with riparian geometry overriding ownership. That config also crosswalks the seven classes to the three TPO owner groups used as volume caps and to the LAMPS minimum-harvest-age groups.
- Output: `regimes/*.key` templates + `regime_assignment.py` (the function).
- **Implemented as config, 2026-08-03:** the library is `config/management_regimes.yaml` (8
  prescriptions, all rendered through the verified `ThinDBH` templates in
  `pipeline/s4_fvs/regime_templates.py`), and each owner class declares 2-3 *eligible*
  prescriptions plus one default — the default is what `regime_assignment.py` assigns, the
  menu is what the §4c trajectory library gets built for and the scheduler chooses among.
  Regeneration after a stand-replacing entry is a fixed tree list
  (`config/fallback_treelists.yaml`), not a `PLANT`/`NATREGEN` keyword. See
  `docs/config-policy.md`.

### 4c. Trajectory library construction
The core of the architecture. **One library per stand, its contents determined by ownership class.**

- **Stand = management unit polygon**, not FIA plot. The unit's tree list is the weighted union of its constituent plots' lists (`build_fvs_inputs.py::build_tree_init`: each donor tree kept intact, its `TPA` scaled by the plot's area share). See `notes/terminology.md`.
- Enumerate `(stand, prescription)` pairs: for each stand, expand its ownership class's eligible set from `config/prescriptions.yaml`, apply the riparian override and the eligibility screens.
- Run FVS once per pair — 50-year horizon, 5-year cycles, **one continuous run, no restart barrier**. Runs are independent, so this is embarrassingly parallel across processes/nodes; no synchronization between stands is required.
- Cache on a content hash of `(tree list, site attributes, prescription)` so identical inputs are not re-run. Dedup is a cache, never a reporting decision — every polygon keeps its own identity in the outputs.
- Budget: target **6-12 trajectories per stand**. The five-county pilot (order 10⁴ units) is then order 10⁵ FVS runs — a one-time cost per library version, not a per-scenario cost. Grid growth is multiplicative; treat grid size as a standing budget question.
- Store two tables:
  - `trajectory_index` — one row per trajectory: `trajectory_id`, `stand_id`, `prescription_id`, `ownership_class`, `county`, `area_ac`, `unit_class`, per-cycle harvest volume, precomputed objective terms. This is the scheduler's working set and must fit in memory.
  - `trajectory_cycles` — one row per `(trajectory_id, cycle)`: BA, TPA, QMD, SDI, volume, biomass, removals, carbon pools when enabled. Joined only after selection.
- Integrity checks on every build: every non-riparian stand has ≥2 trajectories, every riparian stand exactly 1 (`no_management`), every trajectory has exactly `n_cycles + 1` state rows after duplicate removal rows are collapsed (initial state plus one endpoint per cycle), the unit layer and the library cover each other in both directions, and every prescription is a member of its ownership class's eligible set.
- Output: `fvs_trajectory_library.parquet` / DuckDB (`trajectory_index`, `trajectory_cycles`).

### 4d. Harvest scheduling by simulated annealing
- **Decision variable:** one choice `x_s` per stand from its library `L_s`. With ~10⁴ stands and ~8 trajectories each, the space is astronomically large — hence a heuristic, and hence the requirement to *report* search quality rather than assume it.
- **Objective — four forms** after `CLIMATE-FVS`: `maximize`, `minimize`, `evenflow` (minimize the standard deviation of a metric across periods), and `evenflow_target` (minimize variation around a target, which may be a value or a range and may vary over time). Each carries a weight; the binding harvest target is weighted well above the rest so the scheduler hits it first and optimizes the others within that constraint. Evaluating a whole landscape plan is a lookup and a sum, not an FVS run — which is what makes search affordable at all.
- **TPO figures are an `evenflow_target`, not a hard ceiling.** They derive from observed historical removals, so undershooting a county target is as much a finding as overshooting it. The baseline selects the `2013_2024` period explicitly in `config/projection.yaml`; `all_years` remains available only as an explicit sensitivity scenario.
- **Keep targets dimensioned by county and owner group.** Diaz et al. set theirs globally and their scheduler shifted harvest between BLM Districts to hit the landscape total — an artifact they flag against actual BLM practice. Report per-dimension outcomes, not just the total.
- **Constraint split.** Policy absolutes are made **unrepresentable** (riparian no-entry and eligibility screens are enforced by library construction, so the search cannot select them — the same device Diaz et al. used for stream buffers, wilderness, and Critical Habitat). Spatial constraints are **priced** as penalties: adjacency and green-up, maximum contiguous opening size, treatment budget. These come from `LAMPS`; the Diaz et al. scheduler carried no spatial constraint at all, so its code cannot supply them.
- **Moves:** a mixture of single-stand reassignment, whole adjacency-block reassignment (single-stand moves stall under a green-up penalty), and period swaps between comparable stands.
- **Acceptance and cooling:** Metropolis acceptance; geometric cooling; `T₀` calibrated at run start to a target initial acceptance rate rather than hardcoded. Parameters in `config/projection.yaml` under `harvest.annealing`.
- **Initial solution:** seed from the greedy oldest-first allocator in `pipeline/s3_management/harvest_scheduler.py`, which is retained for this purpose and as a reported baseline.
- **Reproducibility:** one documented seed; same seed + same library + same weights ⇒ identical plan. Record seed, cooling schedule, and objective weights in `versions.lock`.
- **Required quality report** (a plan is not a result without it): objective value; the full constraint-violation vector per dimension per cycle; an objective-specific relaxation bound (per-stand only for separable objectives; aggregate-preserving for `evenflow` / `evenflow_target`, or explicitly unavailable); the greedy and random baselines; and the spread across seeds.
- Output: `selected_plan.parquet` (`stand_id` → `trajectory_id`) + `scheduler_report.json`.

### 4e. Painting the selected plan
- Join the selected plan to `trajectory_cycles`, then to pixels through the unit × TreeMap crosswalk.
- Write outputs to a per-pixel × per-cycle Zarr store.
- Chunk by HUC8 or tile; aggregate to summary statistics (county, ownership, state) on the fly.
- Per-acre densities paint directly; **totals require × pixel acres** (900 m² = 0.2224 ac). See `notes/treemap-methodology.md`.
- Output: `projection_cube.zarr` with dimensions `(pixel, cycle, attribute)`.

---

## 5. Validation (needed before any product is published)

### 5a. Growth validation (validate growth before evaluating any schedule)
- **Hindcast against FIA remeasurements.** Initialize from an older FIA panel (e.g., 2010-2014 measurements), run the pipeline forward, compare against the most recent panel (2018-2022) at the plot level. Report bias and RMSE for BA, TPA, QMD, volume by species group.
- **Cross-check against BIGMAP.** Compare projected year-0 biomass against BIGMAP 2014-2018 biomass at the pixel level; verify TreeMap+FIA initialization is consistent with an independent product.
- **Spatial pattern check.** Aggregate to county, compare against FIA EVALIDator estimates for the same counties.
- **Sensitivity probe.** Re-run with perturbed site index (±10%) and document output sensitivity; informs whether site index uncertainty matters before deciding to invest in better SI modeling.

### 5b. Library integrity (cheap; run on every library build)
- Every non-riparian stand has ≥2 trajectories; every riparian stand has exactly 1, and it is `no_management`.
- After collapsing duplicate FVS removal rows, every trajectory has exactly `n_cycles + 1` state rows (the initial state plus one endpoint per cycle), with no gaps and no NaNs in any objective column.
- Unit layer and library cover each other **in both directions** — a stand silently missing from the library is a stand the scheduler cannot manage, and it will not announce itself.
- Every prescription in the library is a member of its ownership class's eligible set in `config/prescriptions.yaml`.

### 5c. Scheduler validation
- **Determinism.** Same seed + same library + same weights ⇒ identical plan.
- **Search behaviour.** Monotone best-so-far objective; final objective ≥ the greedy baseline on the pilot. A plan that does not beat greedy is a finding about the search and must be reported as one.
- **Constraint accounting.** Structural constraints honoured exactly; priced constraints reported as a violation vector per dimension per cycle, against a stated tolerance.
- **Optimality gap.** Choose the bound through the objective's declared strategy. Separable maximization objectives may use `Σ_s max_{x∈L_s} value(x)`; coupled `evenflow` and `evenflow_target` objectives require a relaxation that preserves their per-period, per-dimension aggregate term while removing spatial penalties. If no validated relaxation is implemented, report the gap as unavailable.
- **Seed spread.** Report objective spread across restarts; a wide spread means the search has not converged, whatever the best run shows.

### 5d. Landscape plausibility
- **The selected prescription mix, by ownership class** — a headline result, not a diagnostic. Diaz et al. report exactly this (their Figure 16) as the primary read on scheduler behaviour.
- **Per-dimension outcomes**, by county and owner group. A plan that hits the landscape total by reallocating harvest between counties is invisible in an aggregate figure.
- Harvested area per cycle per ownership class against TPO targets and the LCMS observed record (§3d).
- Age-class distribution through time. A plan that liquidates the oldest classes in cycle 1 and flatlines is satisfying its constraints and failing forestry.
- Opening-size distribution against the green-up rules.

---

## 6. Output products and packaging

- **The trajectory library is itself a product**, not an intermediate: `trajectory_index` + `trajectory_cycles`. It is what makes an alternative scenario cheap — a new objective or a new constraint set is a re-run of §4d, not a re-run of FVS.
- **The selected plan**: `selected_plan.parquet` (`stand_id` → `trajectory_id`) plus `scheduler_report.json` carrying the objective value, violation vector, optimality-gap bound, baselines, and seed spread. The plan is not publishable without the report.
- Per-cycle, per-pixel state cube: `projection_cube.zarr`.
- Headline rasters at year 0, 25, 50: BA, biomass, dominant species, and — if carbon is re-enabled — **all five IPCC carbon pools**: aboveground live, belowground live, dead wood, forest floor, soil organic carbon. Library runs are barrier-free, so the measured restart corruption does not apply; `carbon_extension` is nonetheless still `false` pending an explicit scope decision (see `config/projection.yaml`).
- Aggregated summary tables: by county, by ownership, by forest type group. Riparian buffers report as their own polygons, never dissolved into neighbouring units.
- Documentation: data dictionary, methods writeup, validation report — **all required for peer review**.
- Reproducibility: pin all input dataset versions (TreeMap 2022, LCMS v2024.10, PRISM normals 1991-2020, FIA evaluation cycle) **plus the library version, scheduler seed, cooling schedule, and objective weights**; commit a `versions.lock` file. All steps must be reproducible from pinned inputs with no manual intervention.

---

## Open project decisions
1. Exact state list / boundary for "eastern US"?
2. Compute environment for library generation at scale — local, HPC, cloud? (Run count is `stands × prescriptions`; §4c.)
3. Does the project have FIA database credentials or use public DataMart downloads?
4. **The v1 objective** — NPV, volume, carbon, or a stated weighting? This is the scenario definition, not a tuning parameter.
5. **Parameter-grid resolution per prescription family** — how many rotation ages and thin timings genuinely change the answer? Library cost is multiplicative in this.
6. **Even-flow scope** — per ownership class, per county, or landscape-wide; non-declining or within a ± band?
7. **Tribal and unknown-ownership eligible sets** — both are conservative placeholders in `config/prescriptions.yaml` and need a documented source before publication.

Settled since the first draft of this plan: ownership granularity is the **seven Harris et al. (2025) forest classes**, uncollapsed (§3c); BMP/riparian rules are **state-specific**, keyed by FIPS in `config/bmp_rules.yaml` (§3b).
