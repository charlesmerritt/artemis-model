# TreeMap-to-FVS Workflow from `/mnt/d/TreeMap_Chaz`

## Why this matters

`/mnt/d/TreeMap_Chaz` contains a working R-based prototype for taking TreeMap pixels, finding the FIA plots behind those pixels, preparing FIADB/FVS SQLite inputs, running FVS Online/OnLocal, and linking projected stand outputs back to TreeMap raster values. This is directly relevant to ARTEMIS initialization and FVS trajectory-library design.

## Duplicate script status

Compared root scripts against `scripts/` on 2026-06-01:

- `02_subset_FIA_SQLite_multistateR.R` through `06_build_TreeMap_FVS_linkage.R` are byte-identical in root and `scripts/`.
- `01_subset_TreeMap_5county.R` exists only in the root of `/mnt/d/TreeMap_Chaz`.
- `scripts/1_FL_FIA_TreeMap_comparison.R` exists only in `scripts/`.
- `_info.txt` differs:
  - root `_info.txt` is links/resources: TreeMap 2022 archive, raster gateway, Vibrant Planet, FVS training.
  - `scripts/_info.txt` is a short workflow outline: prepare FIADB for FVS, run FVS, then build linkage.

## Script workflow

### `01_subset_TreeMap_5county.R`

Clips TreeMap to Baker, Columbia, Hamilton, Suwannee, and Union counties using GADM county boundaries. It counts raw integer raster `TM_ID` values, joins to the TreeMap VAT, and writes per-`TM_ID` attributes including `PLT_CN`, pixel count/acres, `FORTYPCD`, `BALIVE`, `TPA_LIVE`, and `CARBON_L`.

Key gotcha: comments/title say TreeMap 2022, but the active path is `RDS-2025-0031/Data/TreeMap2020_CONUS.tif`. Some output labels still say `TreeMap2022`. Treat the 2020 vs 2022 version as ambiguous unless rerun with explicit paths.

Important terra gotcha: TreeMap VAT is auto-loaded by `terra::rast()`, but `freq()` must be run on `as.int(tm_county)`; otherwise terra may return active category strings such as `ForTypName` instead of integer `TM_ID` values.

Observed outputs:

- `output2020/FL_5county_TreeMap_TMIDs.csv`: 688 rows.
- `output/FL_5county_TreeMap_TMIDs.csv`: 693 rows, likely from a different run/version.

### `02_subset_FIA_SQLite_multistateR.R`

Searches 15 state FIADB SQLite databases for the `PLT_CN`s assigned to the five-county TreeMap pixels. TreeMap can impute plots from outside Florida, so Florida-only FIADB is insufficient.

State search list: FL, GA, AL, SC, TN, MS, NC, AR, KY, VA, TX, OK, WV, MO, LA. Documentation says matched states were FL, GA, AL, SC, MS, TX, LA.

For each state with matches, it creates a subset SQLite database and keeps:

- plot-linked tables filtered by `PLT_CN`/`PLOT_CN` or `PLOT.CN`.
- population tables filtered by `STATECD`, because rFIA needs design/variance context.
- reference tables copied in full.

Outputs include per-state subset DBs plus found/unmatched summaries and `FL_5county_TMID_PLT_lookup.csv`.

### `03_build_FVS_input_db.R`

Builds a narrower FVS input database from the per-state subset DBs. It uses FIADB's FIA2FVS-ready tables:

- `FVS_STANDINIT_COND`
- `FVS_STANDINIT_PLOT`
- `FVS_TREEINIT_COND`
- `FVS_TREEINIT_PLOT`
- `FVS_PLOTINIT_PLOT`
- `FVS_GROUPADDFILESANDKEYWORDS`

It assigns all five-county plots to FVS Southern variant `SN`.

Observed artifact `output/FVS_5county_input.db` has only 3 tables: `FVS_GROUPADDFILESANDKEYWORDS`, `FVS_STANDINIT_COND`, and `FVS_TREEINIT_COND`. It appears older/narrower than the consolidated DB from script `05`.

Extra gotcha: script ends with a manual patch:

```sql
UPDATE FVS_STANDINIT_COND SET INV_PLOT_SIZE = 0.041800
```

Do not port blindly; verify why `INV_PLOT_SIZE` needed correction before applying in ARTEMIS.

### `04_tag_FVS_groups_multistate.R`

Updates full state FIADB SQLite DBs in place to append `StudyArea=5county` to `GROUPS` in `FVS_STANDINIT_COND` and `FVS_STANDINIT_PLOT` for matched TreeMap `PLT_CN`s. This enables FVS Online selection by group.

Precision gotcha: FIA control numbers can exceed R double precision. The script reads `PLT_CN` as character and uses SQLite coercion for matching. In Python, keep `PLT_CN`, `CN`, `Stand_CN`, and `StandID` as strings unless exact integer handling is guaranteed.

### `05_consolidate_FIA_SQLite.R`

Best candidate to port into ARTEMIS. It consolidates all affected state FIADB databases into one DB that works for both rFIA and FVS. Observed output:

- `output2020/FIA_5county_consolidated.db`
- 84 tables.
- `FVS_STANDINIT_COND`: 688 rows.
- `FVS_STANDINIT_PLOT`: 688 rows.
- `FVS_TREEINIT_COND`: 25,789 rows.
- `FVS_TREEINIT_PLOT`: 25,789 rows.
- `FVS_PLOTINIT_PLOT`: 2,809 rows.

Join rules captured by the script:

- `PLOT.CN = PLT_CN`.
- Standard FIADB plot-linked tables use `PLT_CN`.
- FVS condition tables use `STAND_CN = COND.CN`, where `COND.PLT_CN = PLOT.CN`.
- FVS plot tables use `STAND_CN = PLOT.CN = PLT_CN`.
- Population tables stay at state level for rFIA variance estimation.
- Reference tables and `FVS_GROUPADDFILESANDKEYWORDS` are copied once.

The script also tags `FVS_STANDINIT_COND` and `FVS_STANDINIT_PLOT` with `StudyArea=5county`.

### `06_build_TreeMap_FVS_linkage.R`

Builds the lookup needed to map FVS outputs back to pixels:

`TreeMap VALUE/TM_ID -> PLT_CN -> COND/PLOT STAND_CN -> FVS STAND_ID`

Outputs:

- `output2020/TreeMap_FVS_linkage.csv`: 688 rows.
- `output2020/TreeMap_FVS_linkage.db`, table `TreeMap_FVS_Linkage`.

FVS output join pattern:

1. Attach/open FVS output DB.
2. Attach/open `TreeMap_FVS_linkage.db`.
3. Join FVS output tables by `StandID` to `STAND_ID_COND` or `STAND_ID_PLOT`.
4. Use `VALUE` to reclassify or join back to TreeMap raster pixels.

The script notes `FVS_Summary2` and `FVS_Carbon` as likely output tables.

### `scripts/1_FL_FIA_TreeMap_comparison.R`

Standalone QA/validation script. Compares FIA design-based estimates from rFIA with TreeMap 2022 pixel-weighted summaries for Florida. Also decomposes TreeMap/FIA differences into area misallocation vs within-type attribute bias. Useful ideas for ARTEMIS validation:

- compare `BALIVE`, `TPA_LIVE`, and live aboveground carbon.
- summarize by `FORTYPCD`.
- compute pixel-weighted plot distribution and effective sample size (ESS) to detect over-concentration of imputed plots.
- area-scaling by FIA forest-type acres can correct area allocation, but not within-type attribute bias.

## FVS run evidence in `TreeMap_Chaz`

### Successful-looking FVS run

`/mnt/d/TreeMap_Chaz/Proc_TreeMap2000_fvs/FVSOut.db` appears to be the useful FVS output:

- 688 `FVS_Cases`.
- `Variant = SN` for all cases.
- `Version = FS2025.4`.
- `Groups = All_FIA_Plots`.
- `FVS_Summary2`: 4,992 rows.
- `FVS_Carbon`: 4,304 rows.
- `FVS_Error`: 196 warnings, mostly `FVS09 WARNING: PLOT COUNTS DO NOT MATCH DATA ON THE DESIGN RECORD; DESIGN RECORD DATA USED`.

The generated key file `Proc_TreeMap2000_fvs/376908e0-fd61-48b5-a26b-0f0ac70857f2.key` shows the run used `FVS_StandInit_Plot` / `FVS_TreeInit_Plot` SQL:

```sql
SELECT *
FROM FVS_StandInit_Plot
WHERE Stand_CN = '%Stand_CN%'
```

and:

```sql
SELECT *
FROM FVS_TreeInit_Plot
WHERE Stand_CN ='%Stand_CN%'
```

This differs from script docs recommending condition-level tables as default. Decide condition vs plot before porting.

The key used:

- `NumCycle 8`.
- default `TimeInt 10`, with one `TimeInt` override for cycle 3.
- database outputs for `Summary2`, tree list/cut list, FIAVBC, carbon, fuels/fire, stats, and regional reports.

This is not yet aligned with ARTEMIS locked scope of 50 years and 5-year FVS cycles.

### Failed FVS run

`/mnt/d/TreeMap_Chaz/FVSOut.db` is not useful as a projection result:

- only `FVS_Cases` and `FVS_Error` tables.
- one case.
- errors: `FVS01 ERROR: INVALID KEYWORD WAS SPECIFIED`.

Do not use this root `FVSOut.db` as evidence of successful growth projections.

## Synthesis for ARTEMIS scope

ARTEMIS should port the stable ideas, not the whole R workflow verbatim.

Recommended pipeline slice:

1. Read TreeMap 2022 raster and VAT.
2. Clip to project extent/AOI in EPSG:5070.
3. Count raw integer `TM_ID` values; avoid categorical/VAT display names.
4. Build `treemap_tmids` table with `TM_ID`, `PLT_CN`, pixel count/acres, forest type, basal area, TPA, carbon.
5. Search national/multistate FIADB SQLite for all `PLT_CN`s, not just Florida.
6. Build consolidated FIADB/FVS input DB with exact join rules from script `05`.
7. Preserve identifiers as strings or exact integers; never round/truncate FIA control numbers.
8. Generate an explicit FVS run plan for Southern variant `SN`, 50-year horizon, 5-year cycles.
9. Run FVS locally or through a reproducible wrapper; avoid manual FVS Online steps for production.
10. Parse `FVS_Cases`, `FVS_Summary2`, `FVS_Carbon`, and other outputs into a trajectory library.
11. Join trajectories back through `StandID -> STAND_ID_COND/PLOT -> PLT_CN -> TM_ID -> raster pixels`.
12. Later, aggregate or paint pixel trajectories to management units.

## Open questions before implementation

- Should ARTEMIS use condition-level FVS tables (`FVS_STANDINIT_COND`) or plot-level tables (`FVS_STANDINIT_PLOT`)? R docs recommend condition-level; observed successful FVS run used plot-level.
- How should TreeMap/FIA inventory years be reconciled with TreeMap circa 2022? The FVS key starts each stand at FIA `INV_YEAR`, which ranges across years. ARTEMIS may need to grow plots to 2022 before starting the 50-year projection.
- Should the prototype's manual `INV_PLOT_SIZE = 0.041800` patch be retained? Needs validation against FIA2FVS docs and current FIADB schema.
- Which FVS interface is production target: command-line local FVS binaries, rFVS, or FVS Online/OnLocal export? Current project wants reproducible pipeline/HPC, so manual Online steps are not enough.
- How should warnings like `FVS09 PLOT COUNTS DO NOT MATCH DATA ON THE DESIGN RECORD` be triaged? They occurred in 196 successful-run cases.
- Need decide which TreeMap version is authoritative in `/mnt/d/TreeMap_Chaz` artifacts. Docs/scripts mix TreeMap2020 and TreeMap2022 labels.
