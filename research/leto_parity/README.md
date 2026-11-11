# LETO parity audit: initial-state pipeline vs aauslander480/Leto

Ground truth is [aauslander480/Leto](https://github.com/aauslander480/Leto) at `13bcf13`
(moved from `d2b92ee` on 2026-11-09; see the last section). `scripts/leto_upstream_changes.py`
lists what upstream has changed since the pin: run it at the start of every LETO ticket.
The repo itself says `LETO_v1_project/` is the active implementation. The `scripts/` tree is
legacy and kept for reference. v1's `AGENTS.md` makes the **verified Maine baseline**
(`configs/maine_test.py`, `configs/maine_verified_baseline.json`) the source of truth, and
says regional values such as buffers and FVS settings belong in configuration.

Our CA port (`pipeline/leto_ca.py`) came from the legacy
`Leto/scripts/Cellular_automata/02_segment_treemap.py`, not from v1. The CA core matches v1 line
for line: seeding, cost function, synchronous boundary reassignment, connectivity, oversized
split, small merge, and similar merge. Everything around the core differs. Paths below are
relative to the LETO repo (`LETO_v1_project/leto/…`) or to this repo.

## Differences

| # | Stage | LETO v1 (truth) | ARTEMIS today | Effect |
|---|---|---|---|---|
| 1 | 2 small stands | After the similar merge, `small_stands.remaining_small_stand_action` (over the legacy `segmentation` key, else `retain`; lowercased, stripped, one of four: `config.py:96-136`) picks the branch (`stage2_segmentation.py:2134-2166`). `force_merge` runs `merge_small_segments(..., maximum_cells=None)`: ownership-compatible, no size cap; isolated fragments with no neighbour are **kept**. `retain` keeps every segment. `delete` zeroes them (`delete_small_segments`) and logs the segments, cells and acres. Each branch then splits disconnected segments and renumbers. AGENTS.md: "Prefer preserving acreage over deleting stands." | Matches for `force_merge`, `retain` and `delete` (#168): `leto_ca.small_stand_action` reads the key as upstream does, `resolve_small_stands` returns `delete`'s counts, and the `leto` stage writes them to `qa/leto/area_accounting.csv` as `deleted_small_stands`, `deleted_small_stand_cells` and `deleted_small_stand_acres`. Florida states `force_merge`. `proximity_merge` is ported (#169): see "Proximity merge". | Explained the FL5 run's 1.3-ac median parent stand before `force_merge`. Differences: see "Small-stand gaps". |
| 2 | 2 CA config | Maine-verified: `maximum_iterations` 40, `convergence_threshold` 0.01, `shared_edge_bonus` **0.2**, `similar_merge_min_shared_edges` **2** | `DEFAULT_CFG`: 100, 0.001, 0.1, 1 (the legacy values plus our own tightening) | Different stands. The 100/0.001 values are ours alone; neither LETO version uses them. |
| 3 | 1 attributes | STDAGE, FORTYPCD, BALIVE from the dominant FIA COND (`COND_STATUS_CD=1`, max `CONDPROP_UNADJ`, CONDID tie-break). TPA and QMD from live FIA TREE rows (`STATUSCD=1`, `DIA>0`, `TPA_UNADJ>0`) in that condition. The TreeMap RAT is a fallback only (`stage1_rasters.py:123-351, 397-650`). | FORTYPCD, BALIVE, QMD, TPA_LIVE from the TreeMap VAT; only STDAGE from COND (`cellular_automata._attribute_grids`, `02_build_attributes.py`) | Different feature rasters on every pixel. |
| 4 | 1 gaps | A missing attribute is filled from the **nearest assigned cell** (`distance_transform_edt`), and provenance is written to QA rasters (`stage1_rasters.py:742-763`) | Missing STDAGE is filled with the landscape **median** | Wrong fill rule. |
| 5 | 1 storage | Rasters are stored rounded: STDAGE and FORTYPCD as integers; BALIVE, QMD, TPA in tenths, then divided by 10 on read (`stage1_rasters.py:778`, `stage2:1991-1995`) | Full float precision | Small numeric differences in the CA cost. |
| 6 | 2 riparian | A **vector** split. Buffers come from NHDPlus HR **flowline + NHDArea + NHDWaterbody**, each by FCode rule. They are dissolved into one footprint; stand polygons are intersected with and erased by it, exploded to single parts, and given **geodesic** acres (`stage2:1757-1950`). No minimum-area deletion after the split. | Raster burn of **flowlines only** from `FL_5_Co_Streams` (NHDPlus 2022). `03_ca_segment.py` uses the pixel-centre test; the production adapter uses `all_touched` with a uniform 35 ft. | Missing lake/pond and river-area buffers; riparian acreage is snapped to cells. |
| 7 | 2 FL buffers | v1 rules are regional. The legacy script carries the Florida first-pass rules: flowline 35/35/75 ft; waterbody 35 ft intermittent, 75 ft perennial; area 35/75 ft, complex channels 75 ft (`scripts/…/02_segment_treemap.py:125-150`). Maine uses 45 ft perennial only. | Flowline rules match the legacy FL values. No waterbody or area rules. FCode 55800 (artificial path) at 75 ft is our addition. | Waterbody and area rules are missing; 55800 is not in LETO. |
| 8 | 3 donor weights | Weights are computed per **parent SEG_ID** (`build_weights_table`). Every MU piece of that parent inherits the parent's weights (`expand_parent_weights_to_management_units`, `stage3_crosswalk.py:612-701`). | Per-MU pixel shares (`02_segment.py`, `03_ca_segment.py`) | A riparian strip gets a different tree list from its parent stand. |
| 9 | 4 donor filter | Drops donors under 5% **but keeps the largest donor** when all fall below. Renormalises per stand (`donor_weights.py:7-35`). | Drops them all; the unit loses every donor (`leto_initial_state.filter_and_normalize_weights`, `build_fvs_inputs.filter_and_renormalize_weights`) | Stands become tree-less, then go to imputation. |
| 10 | 4 tree list | FIA **TREE** table: `STATUSCD=1` across every condition on the plot, no seedlings. `TREE_COUNT = TPA_UNADJ × WEIGHT`; SPCD goes through `FVS_SpeciesCrosswalk.xls` / `EasternSpeciesTranslator` (`SN_Mapped_To`); HT falls back to ACTUALHT; CR becomes CRRATIO (`stage4_inventory.py:618-967`). | `leto_initial_state.py` follows this. The 08-24 experiment and `build_fvs_inputs.py` use `FVS_TREEINIT_PLOT` instead (subplot-count `TREE_COUNT`, seedling rows, HISTORY codes). | Two pipelines disagree with each other; one matches LETO. |
| 11 | 5 sampling design | `BASAL_AREA_FACTOR=-1`, `INV_PLOT_SIZE=1`, `BRK_DBH=0`, `NUM_PLOTS=1`, `NONSTK_PLOTS=0`: `TREE_COUNT` is TPA (`stage5_database.py:prepare_stands`). One plot per stand (`PLOT_ID=1`). | Donor-plot design copied from `FVS_STANDINIT_PLOT` (BAF −24, 300, 5, 4) | Same expansion when every donor shares one design, but not what LETO writes. |
| 12 | 4 stand age | `AGE = floor(STDAGE_MEAN + 0.5)` of the parent segment's CA attributes (`pipeline.py:92-96`) | Not checked in this audit | To verify. |
| 13 | 4 imputation | A stand with no usable live trees takes the tree list of the nearest runnable stand (ArcGIS `GenerateNearTable`, `PLANAR`, any owner or class, no radius), tagged `IMPUTED_NEAREST` (`stage4:974-1202`) | `impute_nearest_runnable` / `impute_missing_tree_rows` exist; their rules are not yet compared | To verify. |
| 14 | 2 ownership | Harris raster nearest-resampled onto the grid; nodata → Unknown(0); NonForest/Water (1, 2) → Unknown (`stage2:1510-1624`) | Same | Matches. |
| 15 | 1 extent | The valid footprint is every valid TreeMap cell inside the study-area clip | The valid footprint is every valid TreeMap cell; the improved rasters are already county-clipped | Matches. Add-back young plots are an ARTEMIS extension that LETO does not have. |

## Open choices (owner: user)

- **CA parameters for Florida.** LETO's verified values are Maine's (0.2 edge bonus, 2 shared
  edges, 40 iterations at 1%). The Florida-era legacy script used 0.1 and 1. v1 says
  thresholds are regional configuration, and no Florida v1 config exists.
- **Riparian geometry.** Matching LETO exactly means vector polygon splitting with geodesic
  acres, plus NHD area and waterbody inputs. Is NHDPlus HR Area on R2? `US SE Waterbodies
  Final/` holds waterbodies.

## Verification against LETO's own baseline

LETO ships a Maine baseline: 429,977 valid cells → 12,168 parent segments → 19,662 management
units → 19,244 direct and 418 imputed FVS stands. Re-running the Maine five-county inputs
through our port and matching these counts is the parity test LETO's own AGENTS.md prescribes.
It needs `ME_5_Co_Dissolve`, the ME/MA/NH/NY FIA TREE and COND CSVs, and NHDPlus HR for Maine.

## Status (2026-10-05)

**Fixed and pinned by `tests/test_leto_parity.py`:**

- **#1, #2:** `leto_ca.DEFAULT_CFG` is the Maine baseline; `SmallStandAction.FORCE_MERGE` runs after the similar merge; seeds round like LETO.
- **#3–5:** `pipeline/leto_attributes.py` is Stage 1 (FIA first, RAT fallback, nearest fill, tenths storage).
- **#6–7:** `pipeline/leto_riparian.py` does the vector split with geodesic acres, under the Florida first-pass flowline, waterbody and area rules.
- **#8–9:** `pipeline/leto_donors.py` provides parent-segment weights and keeps the largest donor. Both production filters (`leto_initial_state`, `build_fvs_inputs`) now use it.

`experiments/2026-10-05_fl5-harvest-deck` runs this path end to end on FL5:

- 69,398 parent stands, 106,935 management units, 25,440 riparian units (36.1k ac).
- Acreage conserved to 0.1 ac.
- 43,437 parents stay under 5 ac (31.9k ac). 21,331 have no neighbour and 22,106 border only other owners, so LETO keeps both groups.

**Open:**

- **Area buffers:** NHDArea is not staged, so FCode 55800 is buffered as a proxy (#7).
- **Production adapter:** `segmentation/cellular_automata.py` still uses VAT attributes, median age fill and a raster riparian burn, so it needs rewiring onto the three modules.
- **Tree lists and design:** `build_fvs_inputs.py` and the 08-24 experiment still build from `FVS_TREEINIT_PLOT` under the subplot design (#10–11).
- **Not yet compared:** age (#12) and nearest-stand imputation (#13).
- **Baseline replay:** the Maine replay remains the end-to-end parity test.

## Next session: brief (user decisions, 2026-10-05)

**Decisions:**

- **Small stands:** keep every stand LETO keeps (superseded by ADR 0005, 2026-11-09). Do not drop or merge any small stand that `force_merge` leaves.
- **Single-pixel speckle:** cleaning it is allowed as an ARTEMIS post-processing step after LETO, never inside it. Settle its definition before building it: which raster (inputs or the final MU layer) and what counts as speckle. The candidates are the 22,723 one-cell parent stands in the FL5 run.
- **Stage 4–5:** follow LETO v1 exactly, as cited below.

**Build test-first, in this order:**

1. **Stage 4 tree list** (`stage4_inventory.build_tree_list`)
   - Live FIA TREE rows (`STATUSCD == 1`), all conditions on the plot.
   - `TREE_COUNT = TPA_UNADJ × WEIGHT`.
   - SPCD zero-padded to 3 digits, then mapped through `FVS_SpeciesCrosswalk.xls` sheet `EasternSpeciesTranslator` (`FIA CODE` → `SN_Mapped_To`).
   - HT falls back to ACTUALHT; CR becomes CRRATIO.
   - Drop rows missing SPECIES/DIAMETER/TREE_COUNT or with TREE_COUNT ≤ 0.
   - Sort by SEG_ID, PLT_CN, TREE_ID_FIA; TREE_ID is sequential within each stand.
   - Write the missing-species QA (`QA_Missing_FVS_Species*.csv`).
2. **Stage 4 imputation** (`impute_missing_tree_lists`)
   - A stand with no usable live trees copies the full tree list of the nearest runnable stand.
   - Distance is ArcGIS `GenerateNearTable` PLANAR polygon-to-polygon edge distance, closest 1, no radius, any owner or class.
   - Ties break on (distance, donor SEG_ID).
   - Tag rows `TREE_SOURCE = IMPUTED_NEAREST` with `DONOR_STAND_ID` and `NEAR_DIST`.
   - Plus the QA tables in `imputation_qa.py`.
3. **Stage 4 stand table** (`build_final_stands`, `pipeline._prepare_stands_dataframe`)
   - `AGE = floor(STDAGE_MEAN + 0.5)`; `INV_YEAR = 2022`.
   - The representative PLT_CN is the largest retained donor.
   - `_validate_final_inventory`: one row per STAND_ID, every stand has trees, no orphan trees.
4. **Stage 5 database** (`stage5_database.prepare_stands`, `prepare_trees`)
   - Stand fields: `STAND_CN = STAND_ID`, `GROUPS = "All_Stands"`, `LOCATION = MU_ID`.
   - Sampling design: `BASAL_AREA_FACTOR -1`, `INV_PLOT_SIZE 1`, `BRK_DBH 0`, `NUM_PLOTS 1`, `NONSTK_PLOTS 0`.
   - Tree fields: `PLOT_ID 1`, `STANDPLOT_ID = STAND_ID_1`.
   - Grouped databases by DB_GROUP: the owner type, or "Riparian".
   - FVS variant SN; FL state code 12.
5. **Production adapter.** Point `segmentation/cellular_automata.py` and `s4_fvs/build_fvs_inputs.py` at `leto_attributes`, `leto_riparian`, `leto_donors` and the new Stage 4–5 code. Retire `FVS_TREEINIT_PLOT` and the subplot design. Update or replace the tests that pin the old behaviour, e.g. `test_missing_stand_age_falls_back_to_the_aoi_median`.
6. **Re-run FL5** (`experiments/2026-10-05_fl5-harvest-deck/01–03`) and refresh slide 2 of https://claude.ai/artifact/5L9JQs5wvjHkQ5H7GVr2kf.

**Inputs:**

- `experiments/2026-08-24_leto-ca-forest-viz/work/staged/`: `FIA_5county_consolidated.db` (TREE and COND), `nhd_waterbody_fl5.gpkg`, `FL_5_Co_Streams`.
- Species crosswalk: `FVS_SpeciesCrosswalk.xls` at the R2 bucket root (`r2:artemis-r2/data/FVS_SpeciesCrosswalk.xls`, `data/index.md`), which `ProductionDataPaths` expects at `<drive>/FVS_SpeciesCrosswalk.xls`. This machine has no rclone remote or `R2_*` env vars, so configure R2 before step 6.
- LETO clone: `git clone https://github.com/aauslander480/Leto`, at the pin (`13bcf13`).

### Progress (2026-10-05, session 2)

- **Step 1 done:** `pipeline/leto_inventory.build_tree_list`, pinned by `tests/test_leto_inventory.py` (10 tests). It returns `TreeList(trees, missing_species, missing_species_by_stand, metrics)`; the caller writes the QA CSVs.
- **Correction to step 3:** LETO's realised AGE is pandas `.round()` (half to even) of the acre-weighted `STDAGE_MEAN`, not `floor(x + 0.5)`. `stage3.read_stand_polygons` sets AGE first, so `pipeline._prepare_stands_dataframe` skips its floor branch. FL5 `02_segment.py` uses floor+0.5 and needs to change too.
- **Step 2 notes:** port `imputation_qa.py` near verbatim (it is pure pandas). Adjacent polygons tie at distance 0. ArcGIS's choice among them is unspecified, so break ties on the lowest donor MU_ID (`STRtree.query_nearest(all_matches=True)`). The imputed rows keep the donor's PLT_CN and get a fresh TREE_ID in donor order.
- **Step 4 notes:** the blank `FVS_Data.db` is not in the repo. Write `FVS_StandInit`/`FVS_TreeInit` with `to_sql`, as `04_fvs_run.py` does; `regime_templates.render_keyfile` takes `stand_table`/`tree_table`. DB_GROUP = OWN_TYPE, or "Riparian" for riparian units (`stage2.finalize_database_groups`).
- **Speckle:** recommended rule (not yet confirmed): only after LETO, on the final MU layer, merge a one-cell parent stand into the neighbour with the same owner and riparian status that shares its longest edge. Deferred until after step 6.
- **Steps 2–4 done:**
  - `leto_inventory.impute_missing_tree_lists`, `prepare_stands`, `build_final_stands` and `validate_final_inventory`.
  - `pipeline/leto_imputation_qa.py`: LETO's module with `as_id_series` for plot IDs.
  - `pipeline/leto_database.py`: `SamplingDesign`, `database_groups`, and `build_databases`, one SQLite file per DB_GROUP.
  - Tests: `tests/test_leto_inventory.py`, `test_leto_imputation_qa.py` and `test_leto_database.py` (28 in total).
- **Step 5 done (user chose "LETO exactly"):**
  - `build_fvs_inputs.build_leto_fvs_inputs` and the CLI run `leto_inventory.build_inventory`, then `leto_database.build_databases`. They read raw FIA TREE from the FIADB and the species crosswalk.
  - The bounded ladder and the `FVS_TREEINIT_PLOT` builders stay importable for dated experiments but are off the production path. `--ladder-report` remains as a comparison.
  - `leto_initial_state.prepare_direct_tree_rows` and `impute_missing_tree_rows` delegate to `leto_inventory`; the old legacy-pinning tests in `test_s1_leto_parity.py` now expect v1 output.
  - The CA adapter's attributes come from `leto_attributes`, with the VAT as fallback and the nearest-cell fill. The median fill and `load_stand_age_by_plot` are gone; `load_leto_attribute_sources` replaces them.
  - FL5 `02_segment.py` AGE now rounds half to even.
- **Still open in step 5:** the CA adapter still burns flowline-only riparian cells, then `attribute_management_units`. Moving it to `leto_riparian.split_stands` changes the shared S1 contract (`SMZ_Pct`, raster MU labels). It also needs NHD waterbody/area inputs and parent-segment donor weights (`leto_donors`) the adapter does not receive. This needs its own story.
- **Step 6 blocked on R2 credentials** for the species crosswalk.

## Pin moved to `13bcf13` (2026-11-09, #161)

- **No behaviour change since `d2b92ee`.** The 10 upstream commits are docs (`README.md`,
  `docs/`), generic placeholder paths in the modules, `configs/maine_test.py` and
  `run_leto.py`, an MIT licence, and a verbatim move of the donor filter from
  `pipeline._filter_weights` to `donor_weights.filter_weights`. The table's line numbers are at
  `13bcf13`, as are the legacy script's (78618d6 moved them up 7 lines).
- **Blank FVS database.** Upstream now ships the template stage 5 copies,
  `LETO_v1_project/data/templates/FVS_Data_Blank.db`. It holds four tables:
  `FVS_StandInit`, `FVS_TreeInit`, `FVS_PlotInit` and `FVS_GroupAddFilesAndKeywords`.
  `leto_database` still writes only the first two, into a fresh SQLite file.
- **Small-stand gaps (closed by #168 and #169).** `retain`, `delete` and
  `proximity_merge` already existed at `d2b92ee`, unchanged at `13bcf13`
  (`stage2_segmentation.py:2148-2166`, `small_stands.plan_proximity_merges`).
  - Done: `leto_ca.small_stand_action` reads `small_stands.remaining_small_stand_action`, then
    the legacy `segmentation` key, then `retain`, lowercases and strips it, and rejects
    anything outside upstream's four actions with upstream's message (`config.py:96-136`). The
    `leto` stage takes the region's action through it.
  - Done: `leto_ca.delete_small_segments` is upstream's verbatim and returns the deleted segment
    and cell counts. `resolve_small_stands` returns them beside the labels, and `segment` puts
    them in `info["deleted_small_stands"]`. Per county, the stage logs them with their acres
    and writes them to the area accounting (`deleted_small_stands`,
    `deleted_small_stand_cells`, `deleted_small_stand_acres` = cells × cell acres). Deleted
    cells are in no parent stand, so unit acres still equal parent-stand acres.
  - Other callers of `leto_ca.segment` (the `cellular_automata.py` adapter, dated experiments)
    pass an action straight into `SmallStandAction` and default to `force_merge`; only the
    `leto` stage reads upstream's key.
  - Order: every action runs after the similar merge, then splits disconnected segments and
    renumbers, as upstream's branches do.
  - Every region in `config/leto.yaml` states its action; Florida's is `force_merge`, as
    upstream's verified Maine config. `tests/test_leto_stage.py` checks no region relies on
    upstream's `retain` default.
  - Differences. Upstream logs its "remaining undersized-segment diagnostic" (no neighbour, size-blocked,
    otherwise unmerged) before the action; ours does not. Deleted acres are planar cell acres,
    as upstream's log reports them, while parent and unit acres are geodesic.
- **Upstream tests.** `tests/test_leto_upstream.py` runs upstream's `test_weight_math.py` (1)
  and `test_imputation_qa.py` (3) verbatim against `leto_donors` and `leto_imputation_qa`.
  `test_small_stands.py` (4) and `test_management_units.py` (6) run verbatim against
  `leto_small_stands` and `leto_management_units` (#169). `test_config.py` waits for the
  regional LETO config's port.
- **Divergences found while porting them.** The ported tests caught only the first; the
  rest came from reading the two modules against upstream.
  - Fixed: `leto_donors.filter_weights` required a `key`. It now defaults to `SEG_ID`.
  - Fixed: it kept donor-plot rows with no `PLT_CN` or key, so a plot-less row could become
    a unit's only donor plot. It now drops them first, as upstream does.
  - Recorded: upstream also requires `STAND_ID` and drops rows missing it. Ours groups on
    `key` alone, because `leto_initial_state` and `build_fvs_inputs` pass `MU_ID` frames.
  - Recorded: `leto_imputation_qa` reads IDs through `as_id_series`, not `astype(str)` and a
    strip-".0" cleanup. A float-damaged control number raises, and a float `STAND_ID` 1.0
    reads as "1". The ported tests pass unchanged.

## Proximity merge (#169)

- **Ported verbatim.** `pipeline/leto_small_stands.py` (`plan_proximity_merges`,
  `aggregate_management_unit_weights`) and `pipeline/leto_management_units.py`
  (`assign_final_mu_ids`, `assign_mu_parts`, the chain QA, `validate_final_units`) are upstream's
  modules at the pin; `tests/test_leto_upstream.py` runs their tests unchanged.
- **The geometry around them** (`pipeline/leto_proximity.py`) follows `stage2_segmentation.
  apply_proximity_merge` and `spatial_management_units.finalize_management_outputs`: every
  parent stand's acres, Harris code, dominant forest type and mean age, its neighbours within
  `maximum_distance_meters`, then one unit ID per proximity group and compatibility class
  after the riparian split, its pieces numbered as parts. Limits are the region's
  `small_stands` keys (upstream's names and defaults, `config.py`), read into enums.
- **Donor weights.** Each unit's are its parent stands' combined by its pieces' acres
  (upstream's `stage3_crosswalk` path); its representative plot is the largest, ties to the
  lower TreeMap value. Under the LETO layout these weights and the acre-weighted age feed
  stage 4, as upstream's per-MU weights do.
- **Differences.**
  - Under `proximity_merge` the CA output is split into connected segments and renumbered,
    as for `retain`; upstream's branch skips both, so a disconnected segment would be one
    parent there and its parts separate parents here.
  - The planner reads a parent's acres as its cells x cell acres, as upstream's segment
    summary does; units' `Acres` are geodesic, as elsewhere.
  - Distances are planar metres in the project CRS (EPSG:5070 Albers); upstream's near table
    is geodesic. Candidate pairs are taken from the small parents only, the only sources
    upstream's planner reads.
  - Compatibility classes are `MGMT_CLASS` (our `unit_class`), `DB_GROUP`, `OWN_CODE` and
    `RIPARIAN`; ARTEMIS never makes `MGMT_TYPE`, `OWN_TYPE` or `WETLAND`.
  - With proximity off, upstream keeps `BIO_MU_ID` = `MU_ID` per piece, as we do; our
    `MU_ID`s number the pieces by parent stand then riparian flag.
  - A unit of one parent stand keeps that parent's weights, traits and age exactly, where
    upstream's combination re-weights them by acres (equal up to float rounding).
  - Upstream dissolves units and then writes acre-weighted biology per unit; ours keeps the
    representative plot's attributes (the plot layout) and computes the acre-weighted traits
    only for the speckle cost and the LETO layout's age.
- **Not ported:** upstream's check that dissolving keeps acreage. A unit's `Acres` is the sum of
  its pieces' geodesic acres, by construction; `validate_final_units` runs on every dissolve.
- **After LETO.** The speckle merge (ADR 0005) measures a whole multipart unit against the
  minimum unit area. A unit that takes in speckle keeps its own row, weights and age; the
  `segments` layer records each piece's LETO unit (`leto_unit_id`) and its unit now.
