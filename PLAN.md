# Eastern US Forest Projection Pipeline — v1 Build Plan

## Scope notes for the agent
- **In scope:** deterministic, pixel-level forward projection using FVS Southern variant, initialized from TreeMap 2022 + FIA tree lists, with calibrated management from LCMS.
- **Out of scope (v1):** natural disturbance overlays (hurricane, SPB, fire, ice), climate-modified growth, stochastic Monte Carlo replicates, uncertainty quantification.
- **Target resolution:** 30m pixels.
- **Target extent:** Florida first (FIPS 12); expand to full eastern US once pipeline is validated.
- **Target horizon:** 50 years, 5-year FVS cycles.

---

## 0. Project scaffolding (do this first)
- Define the spatial extent precisely: list of state FIPS codes or a bounding polygon; commit as `config/extent.geojson`.
- Pick a single CRS for all rasters (recommend EPSG:5070, CONUS Albers Equal Area) and a snap grid aligned to TreeMap.
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

### 3c. Ownership and harvest behavior model
- **Ownership assignment per pixel:**
  - Source: **Harris, Caputo & Butler (2025)** — *Forest ownership in the conterminous United States circa 2022: distribution of seven ownership types.* USFS Research Data Archive. doi:10.2737/RDS-2025-0045.
  - Native resolution: **30m** — pixel-perfect alignment with TreeMap 2022; reproject and snap only, no resampling of class values.
  - Vintage: **circa 2022** — temporally co-registered with TreeMap 2022. These two datasets were designed to be used together.
  - Nine raster values: `unknown_forest`, `non_forest`, `water`, `family_forest`, `corporate_forest`, `tribal_forest`, `federal_forest`, `state_forest`, `local_forest`.
  - `non_forest` and `water` pixels masked from FVS pipeline entirely.
  - Each of the seven forest ownership classes treated as its own class in the harvest model (no collapsing).
  - Output: `ownership_class.tif` (9-value categorical, reprojected to EPSG:5070 snapped to TreeMap grid).

- **Harvest model fitting:**
  - Training data: per-pixel LCMS Change product 1985-2024, filtered to "Tree Removal" class for harvest events.
  - Features: stand age (from TreeMap plot), forest type group, ownership class, county, year, time since last disturbance.
  - Model: multinomial logit (Simons-Legaard et al. style) or gradient-boosted classifier predicting P(harvest event in year t | features). Treat clearcut vs partial cut as separate classes if you can distinguish them from LCMS magnitude or post-event recovery slope.
  - Fit separately by ownership class — industrial behavior is qualitatively different from NIPF.
  - **Development order: growth first, harvest second.** Validate FVS growth trajectories against FIA remeasurements before layering in the harvest model. This isolates growth model error from harvest scheduling error.
  - **Forward application method: pseudo-deterministic (approach C).** Draw harvest schedule once per pixel at initialization using a fixed, documented random seed. Reproducible and spatially explicit. Document seed in `versions.lock`.
  - Output: `harvest_model.pkl` plus per-ownership predicted annual harvest probability raster `p_harvest_by_year_ownership.zarr`.

### 3d. Hindcast validation
- Hold out the most recent 10 years of LCMS (2015-2024) from training.
- Run the harvest model forward on the 2015 state of the landscape; predict harvest events 2015-2024.
- Compare predicted harvest rates against observed LCMS Tree Removal:
  - Total area harvested per year, per state, per ownership class
  - Spatial pattern agreement (Cohen's kappa or AUROC at pixel level)
  - Age distribution of harvested pixels
- Document systematic bias; iterate on features if needed.

---

## 4. FVS execution pipeline (missing from your list — needed for an end-to-end agent)

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
  - Riparian (thin only or no entry, depends on buffer class)
- Each regime gets selected per pixel by a deterministic function of `(ownership, forest type, riparian buffer class, stand age)`.
- Output: `regimes/*.key` templates + `regime_assignment.py` (the function).

### 4c. Trajectory library construction
- Identify unique combinations of `(FIA plot ID, regime, site index class)` across the eastern US extent.
- Run FVS once per unique combination, 50-year horizon, 5-year cycles.
- Store outputs in a lookup table keyed by `(plot_id, regime, site_idx_bin)` → trajectory of stand attributes (BA, TPA, QMD, volume, biomass, carbon, species composition) per cycle.
- Output: `fvs_trajectory_library.parquet`.

### 4d. Per-pixel painting
- For every pixel, look up the trajectory matching its `(plot_id, regime, site_idx_bin)`.
- Write outputs to a per-pixel × per-cycle Zarr store.
- Chunk by HUC8 or tile; aggregate to summary statistics (county, ownership, state) on the fly.
- Output: `projection_cube.zarr` with dimensions `(pixel, cycle, attribute)`.

---

## 5. Validation (also missing — needed before any product is published)

- **Hindcast against FIA remeasurements.** Initialize from an older FIA panel (e.g., 2010-2014 measurements), run the pipeline forward, compare against the most recent panel (2018-2022) at the plot level. Report bias and RMSE for BA, TPA, QMD, volume by species group.
- **Cross-check against BIGMAP.** Compare projected year-0 biomass against BIGMAP 2014-2018 biomass at the pixel level; verify TreeMap+FIA initialization is consistent with an independent product.
- **Spatial pattern check.** Aggregate to county, compare against FIA EVALIDator estimates for the same counties.
- **Sensitivity probe.** Re-run with perturbed site index (±10%) and document output sensitivity; informs whether site index uncertainty matters before deciding to invest in better SI modeling.

---

## 6. Output products and packaging

- Per-cycle, per-pixel state cube: `projection_cube.zarr`.
- Headline rasters at year 0, 25, 50: BA, biomass, dominant species, and **all five IPCC carbon pools**: aboveground live, belowground live, dead wood, forest floor, soil organic carbon.
- FVS `CARBON` extension enabled in all keyword templates; all five pools parsed from cycle reports and stored in trajectory library.
- Aggregated summary tables: by county, by ownership, by forest type group.
- Documentation: data dictionary, methods writeup, validation report — **all required for peer review**.
- Reproducibility: pin all input dataset versions (TreeMap 2022, LCMS v2024.10, PRISM normals 1991-2020, FIA evaluation cycle), commit a `versions.lock` file. All steps must be reproducible from pinned inputs with no manual intervention.

---

## What the agent should ask before starting
1. Exact state list / boundary for "eastern US"?
2. Target ownership classification granularity — FIA OWNGRPCD (4 classes) or finer?
3. Are the BMP / riparian rules state-specific or use a single regional default?
4. Compute environment — local, HPC, cloud?
5. Does the project have FIA database credentials or use public DataMart downloads?
