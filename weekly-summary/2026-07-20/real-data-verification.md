# Real-data verification — sliver resolution + TPO parser

Both Phase-1 modules were verified against the **real** project data pulled from the
Cloudflare R2 bucket `artemis-r2` (S3 API via boto3; the data drive is not mounted in the
build sandbox). Nothing here used synthetic stand-ins.

## TPO parser (`pipeline/s3_management/tpo_targets.py`)

Source: `data/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx` (R2).

**The real workbook is not a tidy table** — it is hand-formatted with a title/URL, merged
multi-row headers, and the targets in a small summary block whose two data rows are tagged
by an `"Assuming … averaged"` note. My first parser assumed a clean table and was **wrong**;
it was rewritten to anchor on those note cells. Verified output (all TPO years averaged,
cubic feet/yr):

| County | all_years | 2013–2024 |
|---|--:|--:|
| Baker | 11,755,875 | 11,451,200 |
| Columbia | 17,798,687.5 | 19,725,500 |
| Hamilton | 15,329,437.5 | 16,211,500 |
| Suwanee* | 18,466,937.5 | 22,474,700 |
| Union | 8,703,625 | 7,642,900 |
| **All five counties** | **72,054,562.5** | 77,505,800 |

Owner groups (all_years): Federal (NF) 1,770,000 · Other public 3,969,937.5 · Private
66,314,312.5 · **All owners 72,054,250**. Owner and county grand totals reconcile (~72.05M).
These match `notes/management-pipeline-plan.md` exactly. Parsed config committed to
`config/tpo_targets.yaml`. A real-file test (`test_parse_real_tpo_workbook_matches_known_totals`)
runs when the workbook is present and skips in CI.

*Source spells it "Suwanee" (one n); downstream joins to parcels (CNTYNAME "SUWANNEE") must
account for that.

## Sliver resolution (`pipeline/s3_management/sliver_merge.py`)

Source: `.../management_units_smoke_union/12125_union/candidate_management_units.gpkg` (R2) —
the real Union County layer: **17,020 polygons, 14,870 slivers < 5 ac, 68,240 ac, EPSG:5070**.

**Real data exposed a defect the synthetic tests could not:** shared-boundary merge alone
left **6,716 residual slivers**, because the naive layer's stream/road/water erase separates
thousands of fragments from every unit by a thin gap — they share no boundary. Results on
the real layer for each candidate policy:

| Policy | Units | Slivers left | Area retained | Notes |
|---|--:|--:|--:|---|
| **drop — LETO delineation (chosen default)** | **2,150** | **0** | **86.24%** | clean single-part; median 15.2 ac |
| merge + nearest fallback | 2,442 | 0 | 99.995% | area-conserving but **64.6% multipart** |
| shared-boundary merge only | 9,124 | 6,716 | 99.995% | incomplete |

Figure: `sliver_resolution_union.png`.

### Decision (2026-07-21): follow the LETO style
Per Chaz — **default policy is `drop`**, matching LETO's own delineation step
(`multipart_to_singlepart_and_delete_small`): explode multipart, then eliminate sub-5-acre
slivers, leaving clean single-part stands. The ~14% of area under those slivers is **not
lost to the model** — it is recovered downstream by LETO's *second* script, which imputes
tree lists for tree-less/edge units from the nearest runnable unit (`GenerateNearTable`), a
separate FVS-input step still to be built. The area-conserving `merge` policy (with
nearest-unit fallback) remains available via `--policy merge` for cases where a gap-free,
area-conserving layer is wanted, at the cost of multipart units.

## FVS-input build — the LETO CSV pipeline (`pipeline/s3_management/assign_plt_cn.py`, `pipeline/s4_fvs/build_fvs_inputs.py`)

Ports LETO's `assign_plt_cn` + `LETO_CSV_PIPELINE` into the geopandas/pandas stack. Because
Chaz's FVS-ready per-plot tables already exist (`FL_FVS_TREEINIT_PLOT.csv`, keyed by
`STAND_CN` = PLT_CN), the pipeline joins those directly instead of re-reading raw FIA
`TREE.csv` + a species crosswalk. Verified end-to-end on the real state-zero Union County
units (2,150) using real R2 inputs:

- **Weighting** (`assign_plt_cn`): rasterised the 2,150 units onto the real TreeMap 5-county
  grid (`TreeMap2022_CONUS_5FlCntys.tif`), mapped TreeMap value → PLT_CN via
  `FL_5county_TMID_PLT_lookup.csv`. Result: **2,147/2,150 units weighted, 24,221 rows, 272
  PLT_CNs, weights sum to 1.0 per unit, ~11.3 donor plots/unit** (~10 s). The 3 unweighted
  units have no TreeMap forest pixel.
- **Build** (`build_fvs_inputs`): filtered to plots ≥ 5% weight (LETO `MIN_PLT_WEIGHT`),
  joined the real `FL_FVS_TREEINIT_PLOT.csv` (676,981 tree rows, 30,635 plots), scaled each
  tree's TPA by plot weight, and imputed tree lists for the 3 empty units from their nearest
  runnable unit (LETO `GenerateNearTable`). Result: **2,150 FVS stands, 413,213 tree rows,
  all units covered** — 2,147 direct + 3 nearest-imputed (87 imputed tree rows), ~192 tree
  rows/stand.

This produces per-unit `FVS_StandInit.csv` / `FVS_TreeInit.csv` — the initial (state-zero)
stand condition for every management unit, ready for FVS. Remaining toward harvest
scheduling: the regime library (Phase 3) and the constrained scheduler (Phase 4).
