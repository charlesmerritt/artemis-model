# LETO parity audit: initial-state pipeline vs aauslander480/Leto

Ground truth is [aauslander480/Leto](https://github.com/aauslander480/Leto) at `d2b92ee`.
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
| 1 | 2 small stands | After the similar merge, `remaining_small_stand_action="force_merge"` runs `merge_small_segments(..., maximum_cells=None)`: ownership-compatible, no size cap (`stage2_segmentation.py:2140-2153`). Isolated fragments with no neighbour are **kept**. AGENTS.md: "Prefer preserving acreage over deleting stands." | No remaining-small step at all (`leto_ca.segment`). `s3_management/sliver_merge.py` defaults to `drop`, the opposite of v1. Legacy `scripts/` deleted (`02_segment_treemap.py:1977`). | Explains the FL5 run's 1.3-ac median parent stand. Fix: add `force_merge`, retire `drop` as the default. |
| 2 | 2 CA config | Maine-verified: `maximum_iterations` 40, `convergence_threshold` 0.01, `shared_edge_bonus` **0.2**, `similar_merge_min_shared_edges` **2** | `DEFAULT_CFG`: 100, 0.001, 0.1, 1 (the legacy values plus our own tightening) | Different stands. The 100/0.001 values are ours alone; neither LETO version uses them. |
| 3 | 1 attributes | STDAGE, FORTYPCD, BALIVE from the dominant FIA COND (`COND_STATUS_CD=1`, max `CONDPROP_UNADJ`, CONDID tie-break). TPA and QMD from live FIA TREE rows (`STATUSCD=1`, `DIA>0`, `TPA_UNADJ>0`) in that condition. The TreeMap RAT is a fallback only (`stage1_rasters.py:123-351, 397-650`). | FORTYPCD, BALIVE, QMD, TPA_LIVE from the TreeMap VAT; only STDAGE from COND (`cellular_automata._attribute_grids`, `02_build_attributes.py`) | Different feature rasters on every pixel. |
| 4 | 1 gaps | A missing attribute is filled from the **nearest assigned cell** (`distance_transform_edt`), and provenance is written to QA rasters (`stage1_rasters.py:742-763`) | Missing STDAGE is filled with the landscape **median** | Wrong fill rule. |
| 5 | 1 storage | Rasters are stored rounded: STDAGE and FORTYPCD as integers; BALIVE, QMD, TPA in tenths, then divided by 10 on read (`stage1_rasters.py:778`, `stage2:1997-2001`) | Full float precision | Small numeric differences in the CA cost. |
| 6 | 2 riparian | A **vector** split. Buffers come from NHDPlus HR **flowline + NHDArea + NHDWaterbody**, each by FCode rule. They are dissolved into one footprint; stand polygons are intersected with and erased by it, exploded to single parts, and given **geodesic** acres (`stage2:1763-1956`). No minimum-area deletion after the split. | Raster burn of **flowlines only** from `FL_5_Co_Streams` (NHDPlus 2022). `03_ca_segment.py` uses the pixel-centre test; the production adapter uses `all_touched` with a uniform 35 ft. | Missing lake/pond and river-area buffers; riparian acreage is snapped to cells. |
| 7 | 2 FL buffers | v1 rules are regional. The legacy script carries the Florida first-pass rules: flowline 35/35/75 ft; waterbody 35 ft intermittent, 75 ft perennial; area 35/75 ft, complex channels 75 ft (`scripts/…/02_segment_treemap.py:132-157`). Maine uses 45 ft perennial only. | Flowline rules match the legacy FL values. No waterbody or area rules. FCode 55800 (artificial path) at 75 ft is our addition. | Waterbody and area rules are missing; 55800 is not in LETO. |
| 8 | 3 donor weights | Weights are computed per **parent SEG_ID** (`build_weights_table`). Every MU piece of that parent inherits the parent's weights (`expand_parent_weights_to_management_units`, `stage3_crosswalk.py:616-705`). | Per-MU pixel shares (`02_segment.py`, `03_ca_segment.py`) | A riparian strip gets a different tree list from its parent stand. |
| 9 | 4 donor filter | Drops donors under 5% **but keeps the largest donor** when all fall below. Renormalises per stand (`pipeline.py:81-102`). | Drops them all; the unit loses every donor (`leto_initial_state.filter_and_normalize_weights`, `build_fvs_inputs.filter_and_renormalize_weights`) | Stands become tree-less, then go to imputation. |
| 10 | 4 tree list | FIA **TREE** table: `STATUSCD=1` across every condition on the plot, no seedlings. `TREE_COUNT = TPA_UNADJ × WEIGHT`; SPCD goes through `FVS_SpeciesCrosswalk.xls` / `EasternSpeciesTranslator` (`SN_Mapped_To`); HT falls back to ACTUALHT; CR becomes CRRATIO (`stage4_inventory.py:618-967`). | `leto_initial_state.py` follows this. The 08-24 experiment and `build_fvs_inputs.py` use `FVS_TREEINIT_PLOT` instead (subplot-count `TREE_COUNT`, seedling rows, HISTORY codes). | Two pipelines disagree with each other; one matches LETO. |
| 11 | 5 sampling design | `BASAL_AREA_FACTOR=-1`, `INV_PLOT_SIZE=1`, `BRK_DBH=0`, `NUM_PLOTS=1`, `NONSTK_PLOTS=0`: `TREE_COUNT` is TPA (`stage5_database.py:prepare_stands`). One plot per stand (`PLOT_ID=1`). | Donor-plot design copied from `FVS_STANDINIT_PLOT` (BAF −24, 300, 5, 4) | Same expansion when every donor shares one design, but not what LETO writes. |
| 12 | 4 stand age | `AGE = floor(STDAGE_MEAN + 0.5)` of the parent segment's CA attributes (`pipeline.py:114-118`) | Not checked in this audit | To verify. |
| 13 | 4 imputation | A stand with no usable live trees takes the tree list of the nearest runnable stand (ArcGIS `GenerateNearTable`, `PLANAR`, any owner or class, no radius), tagged `IMPUTED_NEAREST` (`stage4:974-1202`) | `impute_nearest_runnable` / `impute_missing_tree_rows` exist; their rules are not yet compared | To verify. |
| 14 | 2 ownership | Harris raster nearest-resampled onto the grid; nodata → Unknown(0); NonForest/Water (1, 2) → Unknown (`stage2:1516-1630`) | Same | Matches. |
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

- **Small stands:** keep every stand LETO keeps. Do not drop or merge any small stand that `force_merge` leaves.
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
- LETO clone: `git clone https://github.com/aauslander480/Leto`, at `d2b92ee`.

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
