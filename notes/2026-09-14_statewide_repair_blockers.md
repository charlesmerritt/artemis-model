# What is blocking statewide (FL) repaired TreeMap + ownership rasters

Diagnosis note · branch `diag/statewide-repair-blockers` · 2026-09-14

## TL;DR

Nothing is blocked on *data* — TreeMap 2022, LF2016/2022/2024 EVT, the Harris
ownership raster, `All_FL_Parcels` (6.8 GiB, all 67 counties) and SQLite FIADB
are all in `r2:artemis-r2/data/` and reachable with the `.env` credentials.
The blockers are (a) the hole-repair pipeline is hardwired to the five-county
AOI, (b) the repair's decision points (scope, labels, treelists) were never
closed, and (c) several correction stages on the critical path are decided but
**unbuilt**. The team's own deck lists these as open decisions; Linear tracks
them as Todo.

## Blockers, in order

### 1. The hole-repair pipeline only knows the 5-county AOI
- `pipeline/s1_initial_state/stratify_treemap_holes.py` derives the hole
  universe from `Masked_Change_FL_AOI_16_22` — an ArcGIS export already clipped
  to the 5-county polygon. No statewide hole mask exists (statewide one is
  derivable: TreeMap nodata footprint, but the module must switch to it).
- `pipeline/s1_initial_state/embed_holes.py` hardcodes `AOI_BOUNDS_5070`
  (~4.4k × 3.5k px ≈ 15.6M px). Florida is ~30–40× that. The stratifier reads
  whole-AOI object-dtype EVT *name* arrays into memory — statewide that is
  tens of GB. Needs tiling (raster_windows gives the rounding rule, not a
  tiler).
- Earth Engine side: strips are sized for the AOI against the 50 MB/request
  ceiling; a statewide apply is a large but mechanical EE-batching job.

### 2. The classifier is trained, gated, and validated on AOI labels only
k = 6 folds, anchor thresholds, and the 0.903 balanced accuracy are all from
5-county labelled points. `docs/treemap-raster-correction/presentation.html`
(slide 21) lists "Five counties or statewide?" and "S1+S2 only (52,979 ac) vs
full S3 (204,216 ac)?" as explicitly open decisions. The named highest-value
next validation step — representative NAIP hand-labels — has not happened, so
statewide extrapolation of the Stage A/B thresholds is unvalidated.

### 3. Recovered patches have no treelist (repair is not yet "repaired")
The 75,831 ac add-back mask identifies the forest but carries no TM_ID. The
donor-plot + FVS-regeneration plan (slide 21; Linear "Establish treelist
imputation procedure for corrected rasters") is not built. Without it the
statewide raster cannot feed FVS, which is the point of the repair.

### 4. LANDFIRE Annual Disturbance 1999–2023 is not on the drive or R2
It would give harvest year per pixel as a lookup, collapsing most of Phases 2
and 3a (stand assignment + regrow-to-2022). Its absence leaves the slow path as
the only path.

### 5. Critical-path corrections decided but unbuilt (slide 21)
- Erase-layer acre loss (riparian/SMZ buffers) — decided, unbuilt.
- Plot-keyed regime smear — design settled, unbuilt.
- Sliver merge rule — segmentation was proven insufficient (Union County
  experiment); merge-to-best-neighbour is on the critical path, not built.
- Inventory-year reconciliation (INV_YEAR 1997–2021 vs circa-2022 raster) —
  untouched.
- Per-type attribute bias — `r/07_FL_FIA_TreeMap_comparison.R` exists and has
  never been run in-repo (R side, uncommitted outputs).

### 6. EVT correction at state scale is still Todo
Linear: "Correct Landfire EVT rasters" (Todo, no description). The
Vector-Guided-Raster-Correction notebook is the tool, but only the AOI's three
confused classes are decided; statewide class list unconfirmed.

### 7. Ownership raster: statewide data exists, the refinement path isn't state-ready
- Harris `US_forest_ownership.tif` is CONUS — no data blocker.
- "Use longest shared border to reassign non-forest ownerships in the NWOS
  raster" — Done (2026-09-01).
- But `config/ownership_policy.yaml` DOR_UC table is `verified: false` — it was
  transcribed without the drive mounted and never audited against real parcels
  (`owner_classes --audit-parcels`).
- The pipeline was built against `FL_5_Co_Parcels.gdb`; statewide refinement
  needs `All_FL_Parcels`/`Florida_Parcels_LO_Class.gdb` (present in R2, 6.8 GiB)
  wired into `config/data_paths.yaml` and the audit run against it.
- `parcel_owner_summary.txt/.xlsx` vanished from R2 (only an Office lock file
  remains); config says "re-upload from the drive" — and the drive is not
  mounted on this machine (see 8).

### 8. Environment: `/mnt/d` is not mounted here
All data must come from R2. `pipeline/data_access.py` gives the R2 fallback
only to callers that use it; `stratify_treemap_holes.py` still hardcodes
`DRIVE = Path("/mnt/d")`. Any statewide run on this box needs the drive back,
or the remaining hardcoded `/mnt/d` reads routed through data_access.

### 9. The team's own gate
Linear "Check estimates for five county work before scaling up" is still Todo —
volume estimates for the 5-county area are the declared precondition to
scaling. Related in-progress: "Correct rasters at state scale" (mask → binary
classification → validate), "Run Leto on state scale", "Test updated data
sources on Leto".

## Shortest path to a statewide map

1. Replace the ArcGIS hole raster with a derived statewide hole mask
   (TreeMap nodata ∩ land) and tile the stratifier.
2. Close the three open scope decisions (S1+S2 vs S3; synthetic TM_ID in the
   VAT vs separate layer; statewide extent) — they gate everything downstream.
3. EE statewide embedding apply with tiled downloads (mechanical).
4. Build treelist imputation for recovered patches (donor ring + FVS
   regenerate) — the largest unbuilt piece.
5. Land ownership: run the parcel audit against `All_FL_Parcels`, flip
   `verified: true`, wire the state parcel gdb into data_paths.
6. Pull or restore LANDFIRE Annual Disturbance to shortcut harvest-year
   inference.

---

**Update (2026-09-15).** Blocker 7's parcel items and shortest-path step 5 are void: the
parcel refinement, its DOR_UC table and `--audit-parcels` were removed. Owner classes are
exactly the Harris RDS-2025-0045 forest classes (`config/ownership_policy.yaml`), so ownership
needs no parcel audit; the repair of the Harris layer is what remains.
