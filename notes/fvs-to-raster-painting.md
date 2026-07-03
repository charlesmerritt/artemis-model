# Painting FVS Outputs Back to TreeMap Rasters

Reclassify the 5-county TreeMap raster so each pixel carries an FVS-projected
value for its stand. The mechanism is a pure pixel-value swap:

    raster TM_ID (pixel value)  ==  crosswalk `Value`
        -> crosswalk PLT_CN
        -> fvs_trajectory.stand_cn   (stand_cn == PLT_CN, plot-level)
        -> any FVS metric at a snapshot (e.g. basal_area)

## Script

`pipeline/s4_fvs/paint_fvs_to_raster.py` — run with `uv run python ...`.
Writes GeoTIFFs to `data/processed/no_management_fl5co_rasters/`
(`<metric>_<label>.tif`, EPSG:5070, 30 m, Float32, nodata −9999).
Core mapping is the pure `reclassify_by_key`, tested in
`tests/test_s4_paint_fvs_to_raster.py`.

## Inputs (all on `/mnt/d`, external drive — mount before running)

- FVS output (in-repo): `data/interim/no_management_fl5co_fvs_output/fvs_trajectory.csv`
  — 9,259 rows, **693 stands**, one row per stand × cycle, keyed by `stand_cn`.
- Crosswalk: `/mnt/d/TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv`
  — `Value` (TM_ID) → `PLT_CN`, 693 rows.
- Raster: `/mnt/d/TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif`
  — single band of TM_ID values, ~5.41M valid pixels.

## Version trap (resolved)

There are two TreeMap vintages on disk and they must not be mixed:

| Pairing | crosswalk rows | FVS stands covered | use? |
|---------|---------------|--------------------|------|
| **TreeMap 2022** (`output/...`, `FiveFloridaCounties/...5FlCntys.tif`) | 693 | **693/693** | **yes** |
| TreeMap 2020 (`output2020/...`, `clipped_TreeMap_2020.tif`) | 688 | 679/693 | no |

The script reports coverage for both and auto-selects the better one; 2022 gives
100% stand and 100% pixel coverage. The richer `output2020/TreeMap_FVS_linkage.csv`
(with STAND_ID etc.) is **2020-vintage (688 rows)** — do not pair it with the 2022
raster. For plain painting we don't need it anyway, since `stand_cn` in the
trajectory already equals `PLT_CN`.

## Snapshot keying gotcha

Stands have **different inventory start years (1997–2021)** but a **common
projection end year (2076)**. So:

- Absolute `calendar_year` mid-run is NOT a synchronized snapshot — e.g. 1997
  only matches the few stands that start then (~794 pixels).
- `years_since_start` is also ragged (0, 5, 6, 7, … 79) because the final cycle
  is truncated to land on 2076.
- The two anchors common to **all 693 stands**:
  - initial condition → `years_since_start == 0`
  - end of projection → `calendar_year == 2076` (== `traj.calendar_year.max()`)

The script paints both for `basal_area`. Change `METRIC` or add snapshots to
extend (other trajectory columns: `total_cuft`, `merch_cuft`, `sdi`, `qmd`,
`top_height`, `trees_per_acre`, …).

## Verified

- End-to-end spot checks exact: TM_ID 2623 → PLT_CN 17498047010478 →
  FVS yr0 BA 42.875 → all 1,385 pixels of that TM_ID = 42.87472.
- FVS year-0 basal area vs TreeMap's own `BALIVE`: corr **0.93**, means 76.3 vs
  72.6 — consistent, since FVS is initialized from these plots.

## Caveat / open

Output values are **per-acre stand attributes** (e.g. BA in sq ft/ac) painted
uniformly onto every pixel imputed to that plot — fine for mapping/quantiles,
but for area totals weight by pixel area (900 m² → acres), don't sum raw pixels.
Related: [[treemap-fvs-workflow]], [[management-pipeline-plan]],
[[duckdb-iterative-coupling-cells]].
