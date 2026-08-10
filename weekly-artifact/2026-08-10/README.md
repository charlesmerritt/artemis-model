# Weekly artifact — 2026-08-10

## Artifact

**The constrained harvest schedule for the five-county north-Florida pilot** —
`notes/management-pipeline-plan.md` Phase 4.1, the plan's own "core deliverable".
This is the first time the scheduler has been run on real data: the previous
weekly artifact (`weekly-artifact/2026-07-19/`) shipped the FVS-painted
basal-area rasters, and `pipeline/s3_management/harvest_scheduler.py` landed
afterwards (commit `165c8a0`, Phase 4.1) with unit tests only — synthetic
fixtures, never a real landscape.

| File | What it is |
|---|---|
| `harvest_schedule.csv` | The schedule. One row per (scenario × unit × harvest event): unit, cycle, event year, county, owner group, forest type, acres, regime, stand age, per-acre merch volume, removable volume, whether it was harvested, and which constraint blocked it if not. 46,348 rows = 11,587 unit-events × 4 constraint scenarios. |
| `schedule_summary_by_cycle.csv` | Per scenario × cycle: demand, scheduled volume, units harvested, five-county cap, cap utilisation. |
| `schedule_summary_by_dimension.csv` | Per scenario × cycle × county and × owner group: demand, scheduled, cap, utilisation. |
| `units_by_county_owner_regime.csv` | The attributed landscape: unit counts and acres by county × ownership class × assigned regime. |
| `harvest_schedule.png` | Four-panel render of the three CSVs (demand vs. schedule; county dimension; owner dimension; unmet demand by blocking constraint). |
| `make_schedule.py` | The driver that produced all of the above (see "How to regenerate"). |

**Why this artifact.** `PLAN.md` and the management-pipeline plan both frame
ARTEMIS's destination as a spatially explicit, constrained harvest schedule; the
plan lists Step 4.1 as the core deliverable and everything upstream as feeding
it. Every modelling decision here is made by committed repository code —
`tpo_targets` (Phase 1.1), `regime_assignment` (3.2), `regime_templates` (3.1),
`harvest_scheduler` (4.1), and the painter's crosswalk loader. The full test
suite passes in this environment (`uv run pytest tests/ -q` → 147 passed, 21
skipped; the skips are the external-data-drive tests).

## Headline results

**The landscape, attributed** (TreeMap 2022 five-county raster × county polygons
× Harris et al. 2025 ownership, 30 m pixels):

- 4,922,147 of 4,923,989 forested pixels (99.96%) fall inside the five county
  polygons — 1,094,660 ac against the 1,095,070 ac in
  `FL_5county_TreeMap_summary.csv`.
- Of those, **925,098 ac (5,240 units, 676 of 693 FVS stands)** carry a real
  ownership class and enter the schedule. Family forest 566,517 ac ·
  corporate/other private 183,254 ac · federal 140,418 ac · local 26,354 ac ·
  state 8,555 ac.
- **169,562 ac (15.5%) are dropped**: the Harris product calls them non-forest
  (152,092 ac), unknown (16,548 ac), or water (922 ac) where TreeMap calls them
  forest. That disagreement is a real open question, not a rounding error — see
  "Caveats" below.

**The schedule.** The regime library asks for 2,193 M cuft of removals across the
five cycles in which it schedules cuts; the TPO caps let 941 M cuft through
(42.9%) under all three constraint dimensions combined.

| Scenario (active caps) | Unit-events harvested | Volume scheduled | Share of demand |
|---|---:|---:|---:|
| `total_only` | 7,998 / 11,587 | 1,188 M cuft | 54.2% |
| `county_only` | 8,180 / 11,587 | 1,144 M cuft | 52.2% |
| `owner_only` | 6,701 / 11,587 | 949 M cuft | 43.3% |
| `all_combined` | 6,800 / 11,587 | 941 M cuft | 42.9% |

- **The owner-group cap is by far the tightest.** It alone rejects nearly as much
  volume as all three caps together. Cycles 4 (2038–2042) and 8 (2058–2062) are
  pure `selection_harvest` cycles, which the Phase 3.2 rule assigns only to
  public owners; public demand is ~4.5× the combined `Federal (NF)` +
  `Other public` cap, so those cycles fill to 8.0% of the five-county total cap
  and no further.
- **The county caps bind in the two harvest waves.** Seven county-cycles sit at
  100.0% of cap: Baker, Columbia and Union in cycle 2 (2028–2032, the
  thin-from-below wave) and Baker, Columbia, Hamilton and Suwannee in cycle 6
  (2048–2052, the clearcut + plantation-rotation wave).
- **Blocked volume, all constraints combined:** county 641 M cuft · owner group
  379 M cuft · five-county total 233 M cuft.
- **The regime library is spiky, not even-flow.** It concentrates demand into
  2032 (708 M cuft) and 2052 (1,017 M cuft) and asks for nothing at all in five
  of the ten cycles. Even-flow scheduling is not something the current
  deterministic assignment produces — it would have to be imposed.

## What the driver contributes (and what it does not)

The scheduler consumes a units table that Phase 2.3 (the unit × FVS-stand
crosswalk) does not yet provide, so `make_schedule.py` builds one. Two decisions
are the driver's, and both are stated in its docstring:

1. **Scheduling unit = TM_ID × county × ownership class.** The finest partition
   of the pilot that is attributable today and joinable to the completed FVS
   baseline. These are *not* the parcel-derived management units from
   `sketch_management_units.py` — those still have the unresolved sliver problem
   and no stand crosswalk. When Phase 2.3 lands, only `build_units()` changes.
2. **Removable volume = `proportion` × merch cuft/ac × acres**, with
   `proportion` taken from the regime's own `ThinDBH` operations. For
   DBH-windowed thins this is an **upper bound**, because the window excludes the
   large trees that carry most merchantable volume.

## Caveats a reader should carry

- **Removals are not fed back into standing volume.** Volumes come from the
  *no-management* FVS baseline, so a stand cut in 2032 still shows its
  unharvested 2052 volume. Closing that loop is Phase 4.3 (the managed FVS run);
  this schedule is the allocation that feeds it.
- **No riparian exclusion yet.** `SMZ_Pct` is 0 for every unit because no buffer
  layer is joined, so the absolute no-entry riparian rule in
  `regime_assignment` never fires. `notes/methodology-directions.md` item 2 is
  the outstanding work.
- **Owner-group mapping to TPO groups is an assumption:** family + corporate →
  `Private`; federal → `Federal (NF)`; state + local + tribal → `Other public`.
  TPO's `Federal (NF)` is specifically National Forest, which is narrower than
  the Harris federal class.
- **Scale check on the caps.** Standing merchantable volume in the pilot is
  ~1.63 B cuft (area-weighted from the FVS baseline: 1,491 cuft/ac over
  1,094,660 ac, mean BA 83 sq ft/ac — which matches the painter's reported
  year-0 mean exactly). A five-year TPO cap of 360 M cuft is therefore ~22% of
  standing merch volume per cycle, which is high enough to be worth confirming
  that TPO roundwood output and FVS merchantable cubic feet are the same
  quantity before these caps are treated as calibrated.
- The TPO workbook spells the county "Suwanee"; the driver maps the source
  spelling "Suwannee" onto it, as `tpo_targets`' docstring warns.

## R2 inputs pulled

Only the inputs the run reads, from bucket `r2:artemis-r2` (bucket `data/` maps
to the repo's `/mnt/d`):

| R2 key | Local path | Size |
|---|---|---|
| `data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv` | `data/interim/no_management_fl5co_fvs_output/` | 2.97 MB |
| `data/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx` | `data/raw/` | 30 KB |
| `data/TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv` | `data/interim/treemap_link/` | 64 KB |
| `data/TreeMap_Chaz/output/FL_5county_TreeMap_summary.csv` (reconciliation check only) | `data/interim/treemap_link/` | <1 KB |
| `data/TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif` | `data/interim/treemap5co/` | 7.51 MB |
| `data/county_p010g.shp_nt00934/countyp010g.{shp,shx,dbf,prj}` | `data/interim/counties/` | 49.7 MB |
| `data/RDS-2025-0045/Data/US_forest_ownership.tif` | **not downloaded** — read remotely | 3.87 GB on R2 |

The ownership raster is 3.87 GB and is never copied. It is a tiled, LZW-compressed
GeoTIFF, so the driver opens it through GDAL `/vsis3` (pointed at the same R2
endpoint and credentials rclone uses) and pulls only the tiles under the pilot
AOI via a `WarpedVRT` onto the TreeMap grid — a ~29-second windowed read of the
0.6°×1.0° AOI out of a CONUS raster. **No downloaded data is committed**
(everything lands under gitignored `data/`).

## Exact commands

```bash
# inputs (rclone remote `r2` is preconfigured via RCLONE_CONFIG_R2_* env vars)
rclone copy r2:artemis-r2/data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv \
  data/interim/no_management_fl5co_fvs_output/
rclone copy "r2:artemis-r2/data/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx" data/raw/
rclone copy r2:artemis-r2/data/TreeMap_Chaz/output/ data/interim/treemap_link/ \
  --include "FL_5county_TreeMap_TMIDs.csv" --include "FL_5county_TreeMap_summary.csv"
rclone copy r2:artemis-r2/data/TreeMap_Chaz/FiveFloridaCounties/ data/interim/treemap5co/ \
  --include "TreeMap2022_CONUS_5FlCntys.tif"
rclone copy r2:artemis-r2/data/county_p010g.shp_nt00934/ data/interim/counties/ \
  --include "countyp010g.{shp,shx,dbf,prj}"

# the run
uv run python weekly-artifact/2026-08-10/make_schedule.py
```

The repo's Phase 1.1 CLI was also run directly to confirm the caps this driver
loads in-process are the ones the module writes:

```bash
uv run python -m pipeline.s3_management.tpo_targets \
  --xlsx data/raw/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx \
  --out data/interim/tpo_targets.yaml
```

## Dependencies

No new dependencies. The committed `uv.lock` environment was used as-is:
Python 3.14.0rc2, pandas 3.0.3, geopandas 1.1.3, rasterio 1.5.0 (GDAL with
`/vsis3`), matplotlib 3.10.9, openpyxl, PyYAML. `uv sync` reproduces it.

## How to regenerate

1. Stage the R2 inputs with the `rclone copy` commands above, and export the
   `RCLONE_CONFIG_R2_*` credentials (the driver reads them to build GDAL's
   `/vsis3` environment for the ownership raster).
2. `uv sync`.
3. `uv run python weekly-artifact/2026-08-10/make_schedule.py`.

The driver caches the pixel attribution to
`data/interim/schedule_units_attributed.csv`; delete that file to force the
raster work (county rasterisation + the remote ownership read) to re-run. Output
is deterministic — no sampling, no random seeds.
