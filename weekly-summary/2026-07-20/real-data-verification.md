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
thousands of fragments from every unit by a thin gap — they share no boundary. Fixed by
adding a **nearest-unit fallback** (mirroring LETO's `GenerateNearTable` nearest-runnable
assignment). Results on the real layer:

| Policy | Units | Slivers left | Area retained | Notes |
|---|--:|--:|--:|---|
| shared-boundary merge only | 9,124 | 6,716 | 99.995% | incomplete |
| **merge + nearest fallback** | **2,442** | **0** | **99.995%** | median 16.7 ac, min 5.02 ac; **64.6% multipart** |
| drop (LETO prototype) | 2,150 | 0 | 86.24% | loses 9,390 ac; clean single-part geometry |

Runtime: ~13 s for the full county. Figure: `sliver_resolution_union.png`.

### Open question for review
The nearest fallback makes the map **complete and area-conserving**, but **64.6% of units
become multipart** (a main body plus detached pieces attached to the nearest unit). FVS
treats a multipart unit as one stand, but it is spatially messy. Options to weigh:
(a) accept multipart; (b) cap the nearest-attachment distance and drop the rest; (c) keep
isolated pieces as their own units with borrowed attributes (LETO script-2 style);
(d) drop (loses ~14% of area). Needs a call before this becomes the state-zero standard.
