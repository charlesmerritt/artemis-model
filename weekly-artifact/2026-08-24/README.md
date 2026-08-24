# Weekly artifact — 2026-08-24

## Artifact

**The Florida BMP riparian layer, joined to the pilot landscape for the first time** — a
measured `SMZ_Pct` for every scheduling unit, and the trajectory library re-enumerated
with `regime_assignment`'s absolute riparian override finally live.

Every weekly artifact since 2026-08-10 has carried the same caveat, in the same words:

> **No riparian exclusion yet.** `SMZ_Pct` is 0 for every unit because no buffer layer is
> joined, so the absolute no-entry riparian rule in `regime_assignment` never fires.
> `notes/methodology-directions.md` item 2 is the outstanding work.

The rule itself is not a sketch. `config/management_regimes.yaml` declares it as
executable policy — `overrides.riparian` with `field: SMZ_Pct`, `min_value: 50.0`,
`prescription: no_management`, `absolute: true` — and `regime_assignment` asserts the
`absolute` flag on import, so the repository has been carrying a load-bearing rule that
had never been evaluated against a single real stream. This artifact evaluates it.

| File | What it is |
|---|---|
| `riparian_map.png` | The map. Panel A: forested land inside a BMP stream-management zone across the five counties. Panel B: the densest 3 km window at buffer resolution, buffers over the flowlines that generated them. |
| `riparian_overlay.png` | Four-panel render of the summary CSVs — riparian share by county and by ownership class, where units sit against the 50% threshold, and what the override costs the decision space. |
| `smz_by_unit.csv` | The join. One row per scheduling unit: pixels, acres, SMZ pixels, SMZ acres, `SMZ_Pct`, stand age. 5,240 rows — the same units the 2026-08-10 schedule and the 2026-08-17 library were built from. |
| `smz_by_county.csv` · `smz_by_owner.csv` · `smz_by_forest_branch.csv` | Riparian acres and share, cut three ways. |
| `smz_by_buffer_class.csv` | Per BMP buffer class: SMZ acres, pixels, and the flowline features/km that generated them — including the classes that generated nothing. |
| `smz_unit_smz_distribution.csv` | Units and acres by `SMZ_Pct` band, against the 50% threshold. |
| `library_riparian_delta.csv` | The decision space under three readings: last week's baseline, the override on today's units, and the override on units split at the buffer edge. |
| `riparian_pieces.csv` | The 1,835 no-entry pieces under the geometric reading, with acres, county, owner class and stand. |
| `make_riparian_overlay.py` | The driver that produced every CSV. |
| `make_map.py` | Renders the map from the staged raster + the cached buffer geometry. |
| `make_figure.py` | Renders the four-panel figure from the committed CSVs alone — no R2 access needed. |

**Why this artifact.** It is the oldest standing caveat in the weekly series and the one
piece of declared policy in `config/` that no run had ever exercised. It is not a repeat:
`2026-07-19` and `2026-08-03` shipped FVS-painted basal-area rasters, `2026-07-26` the
harvest-scheduling analysis figures, `2026-08-10` the constrained schedule, and
`2026-08-17` the enumerated trajectory library. This week measures the geometry those all
assumed away, and hands the annealer a decision space with the no-entry acres marked.

**Not fabricated.** Every modelling decision belongs to committed repository code:
`sketch_management_units.classify_stream_fcode` (NHD FCode → BMP class),
`build_riparian_buffer_layer` (the disjoint, priority-ordered buffer layer),
`feet_to_meters`, `regime_assignment._is_riparian` (the threshold test),
`eligible_prescriptions`, `forest_type_branch`, `owner_classes.classify_owner`, and
`paint_fvs_to_raster.load_crosswalk`. Buffer widths come from `config/bmp_rules.yaml`, the
threshold and override prescription from `config/management_regimes.yaml`. The landscape
attribution reproduces `weekly-artifact/2026-08-10/make_schedule.py` **exactly** — 5,240
units, 676 of 693 FVS stands, 925,098 acres, asserted in the driver — and with `SMZ_Pct`
forced back to 0 the enumeration reproduces last week's library exactly: 15,747 rows,
3,788 FVS runs. The full suite passes in this environment (`uv run pytest tests/ -q` →
**798 passed, 10 skipped**).

## Headline results

**11,155 acres — 1.21% of the attributed pilot — are inside a Florida BMP
stream-management zone.**

| Buffer class | Width | Flowlines | Stream km | SMZ acres (attributed forest) |
|---|---:|---:|---:|---:|
| `perennial_small` | 50 ft | 4,067 | 1,480 | 8,995 |
| `ephemeral_intermittent` | 35 ft | 1,264 | 552 | 2,160 |
| `perennial_large` | 75 ft | 0 | 0 | 0 |
| *no BMP class* | — | 4,651 | 1,617 | — |

Vector buffer area is 14,389 ac; rasterised onto the TreeMap 30 m grid it is 14,356 ac
(−0.23%, so the grid is not materially biased against 10.7–22.9 m strips); 12,029 ac of
that falls on forested TreeMap pixels inside the counties, and 11,155 ac survives the
ownership screen that drops non-forest/water/unknown.

**The absolute override, as written, is a no-op on today's units.** The scheduling unit in
use is `TreeMap plot × county × ownership class` — an attribute class scattered across the
landscape, not a contiguous polygon — so its SMZ share is a landscape-wide average:

| `SMZ_Pct` band | Units | Acres |
|---|---:|---:|
| 0 (no SMZ pixel) | 3,405 | 62,899 |
| 0–5 | 1,404 | 821,715 |
| 5–10 | 222 | 31,859 |
| 10–25 | 140 | 8,493 |
| 25–50 | 39 | 113 |
| **≥50 — trips the override** | **30** | **18.9** |

1,835 units (35%) touch a buffer; 30 clear the 50% bar, and they total **18.9 acres**
(median 0.44 ac, median 2 pixels). Eleven units — 2.7 ac — lie entirely inside a buffer.
The rule fires on 0.002% of the landscape. That is a finding about the unit definition,
not about Florida's streams: a 50 ft strip along a stream never dominates a
plot-attribute class, and it always dominates the polygon that *is* the strip.

**So the driver reports the geometric reading too.** Splitting every unit at the buffer
edge — which is what the Phase 2.3 polygon delineation will produce, and what
`sketch_management_units` already does with `unit_class = "riparian"` — gives the number
that matters for planning:

| Scenario | Units | Library rows | FVS runs | No-entry acres | Acres with ≥1 cutting option |
|---|---:|---:|---:|---:|---:|
| `no_riparian` (2026-08-17 baseline) | 5,240 | 15,747 | 3,788 | 0 | 925,098 |
| `unit_mean` (today's units, mean share) | 5,240 | 15,688 | 3,781 | 18.9 | 925,079 |
| `smz_split` (split at the buffer edge) | 7,064 | 17,550 | 3,782 | 11,155 | 913,943 |

**Riparian exclusion costs the FVS batch almost nothing — 6 runs of 3,788.** The batch is
keyed on `(PLT_CN, prescription)` and stands are shared across many units, so removing
11,155 acres of harvest options removes essentially no simulation work. The cost lands on
the *objective*, not the compute: 11,155 acres leave the harvestable base, concentrated
where the streams are — Union County loses 2.63% of its forest against Suwannee's 0.81%,
and family forest carries 7,504 ac (67%) of the pilot's riparian acres against federal
land's 891 ac. Hardwood-branch forest is 1.87% riparian against pine's 0.68%, which is
the expected signal: bottomland hardwood sits along the water.

## Three things a reader should take to the code

1. **`perennial_large` is unreachable.** `config/bmp_rules.yaml` declares a 75 ft class for
   perennial streams ≥15 ft wide, keyed on "FCodes 46006, Strahler order 3+". But
   `classify_stream_fcode` maps *every* 46006 to `perennial_small`, with the comment
   "defaulting to small for conservative buffer" — and 50 ft is the *less* protective
   choice, so the rationale is inverted. No stream in the pilot receives the widest buffer,
   and the 11,155 ac here is a lower bound. Strahler order is not read from NHD at all.
2. **1,617 of 3,650 flowline km (44%) get no buffer**, because their FCode has no BMP
   class: 1,301 km of `55800` artificial path (the flowline traced *through* waterbodies
   and swamps), 261 km of `33600` canal/ditch, 56 km of pipeline/connector. Excluding
   canal/ditch is deliberate — though the config comment names FCode `49300` for that role,
   which is not the canal/ditch code — but artificial paths are the through-water
   connectors of exactly the wetland systems Florida BMPs protect, and dropping them
   silently is the largest single source of understatement here.
3. **No waterbody buffers.** `config/bmp_rules.yaml` declares a 75 ft `waterbody` class,
   but `build_riparian_buffer_layer` takes only streams; waterbodies enter
   `sketch_management_units` through `build_exclusion_layer`, which *erases* them rather
   than buffering them. Combined with (2), lakes and ponds contribute neither their own
   buffer nor their artificial paths. Joining `US SE Waterbodies Final` is the obvious
   next increment.

## Caveats

- **The unit is still not the Phase 2.3 management unit.** It is `TreeMap plot × county ×
  ownership class`, inherited from the 2026-08-10 driver, because the unit × stand
  crosswalk does not exist yet. `smz_split` is this artifact's *stand-in* for polygon
  units, not the delineation itself: it is exact on acres and on which acres are no-entry,
  and approximate on unit geometry (its pieces are pixel sets, so a "riparian unit" here
  may be several disconnected strips).
- **Pixel-centre membership.** A 30 m pixel is in the SMZ when its centre is, which is
  rasterio's default. At aggregate the bias is −0.23% against the vector area; for any
  single unit it can be a whole pixel either way.
- **The `unit_mean` scenario applies the config threshold literally.** Nothing here argues
  the 50% number is wrong. It argues that a share-of-unit threshold is only meaningful
  once units are contiguous polygons, and reports both readings rather than picking one.
- **Ownership disagreement is unchanged.** The same 169,562 ac (15.5%) that the Harris
  product calls non-forest/unknown/water where TreeMap calls it forest are still dropped
  before any of this, and 874 ac of SMZ goes with them. A further 2,327 ac of buffer sits
  on TreeMap pixels that are not forest at all.
- **Still enumeration, not simulation.** No FVS executable ran. The library counts what the
  batch would submit, as last week's did.

## R2 inputs pulled

Only the inputs the run reads, from bucket `r2:artemis-r2` (bucket `data/` maps to the
repo's `/mnt/d`). **No downloaded data is committed** — everything lands under gitignored
`data/`.

| R2 key | Local path | Size |
|---|---|---|
| `data/US SE Streams - FINAL/US SE Streams - FINAL/Streams By State/nhdplus_epasnapshot2022_fl.gdb` | `data/interim/nhd/` | 93 MB |
| `data/Lowe_TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif` | `data/interim/treemap5co/` | 7.2 MB |
| `data/Lowe_TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv` | `data/interim/treemap_link/` | 64 KB |
| `data/Lowe_TreeMap_Chaz/output/FL_5county_TreeMap_summary.csv` | `data/interim/treemap_link/` | <1 KB |
| `data/county_p010g.shp_nt00934/countyp010g.{shp,shx,dbf,prj}` | `data/interim/counties/` | 48 MB |
| `data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv` | `data/interim/no_management_fl5co_fvs_output/` | 2.9 MB |
| `data/RDS-2025-0045/Data/US_forest_ownership.tif` | **not downloaded** — read remotely | 3.87 GB on R2 |

The NHD geodatabase is the path `config/data_paths.yaml` already names (`nhd.fl_gdb`); only
the AOI's 9,982 flowlines are read out of its 417,354. The Harris ownership raster is never
copied — the driver opens it through GDAL `/vsis3` against the same R2 endpoint and reads
only the tiles under the pilot AOI via a `WarpedVRT` onto the TreeMap grid (~33 s).

## Exact commands

```bash
# inputs (rclone remote `r2` is preconfigured via RCLONE_CONFIG_R2_* env vars)
rclone copy "r2:artemis-r2/data/US SE Streams - FINAL/US SE Streams - FINAL/Streams By State/nhdplus_epasnapshot2022_fl.gdb" \
  data/interim/nhd/nhdplus_epasnapshot2022_fl.gdb
rclone copy r2:artemis-r2/data/Lowe_TreeMap_Chaz/FiveFloridaCounties/ data/interim/treemap5co/ \
  --include "TreeMap2022_CONUS_5FlCntys.tif"
rclone copy r2:artemis-r2/data/Lowe_TreeMap_Chaz/output/ data/interim/treemap_link/ \
  --include "FL_5county_TreeMap_TMIDs.csv" --include "FL_5county_TreeMap_summary.csv"
rclone copy r2:artemis-r2/data/county_p010g.shp_nt00934/ data/interim/counties/ \
  --include "countyp010g.shp" --include "countyp010g.shx" \
  --include "countyp010g.dbf" --include "countyp010g.prj"
rclone copy r2:artemis-r2/data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv \
  data/interim/no_management_fl5co_fvs_output/

# the run
uv run python weekly-artifact/2026-08-24/make_riparian_overlay.py   # CSVs
uv run python weekly-artifact/2026-08-24/make_map.py                # riparian_map.png
uv run python weekly-artifact/2026-08-24/make_figure.py             # riparian_overlay.png
```

## Dependencies

No new dependencies. The committed `uv.lock` environment was used as-is: Python 3.14,
pandas, geopandas (pyogrio/GDAL for the file geodatabase), rasterio (GDAL with `/vsis3`),
shapely, matplotlib, PyYAML. `uv sync` reproduces it.

## How to regenerate

```bash
uv sync
# stage the R2 inputs with the rclone commands above, then run the three scripts.
```

The driver caches its pixel attribution at `data/interim/smz_pixels_attributed.csv` and the
buffer/flowline geometry at `data/interim/smz_buffers_5070.gpkg` and
`data/interim/smz_streams_5070.gpkg` (all gitignored). Delete the CSV cache to force the
ownership warp and the buffer build to re-run. Output is deterministic — no sampling, no
random seeds; the map's zoom window is chosen by an integral-image argmax over SMZ pixel
density, with ties resolving to the north-westmost window.

Figure colours are the Okabe–Ito-derived categorical set, validated with the dataviz
palette checker (light surface `#fcfcfb`): lightness band, chroma floor, CVD separation
(worst adjacent pair ΔE 9.6 deutan) and normal-vision floor all pass; the low-contrast
hues carry direct value labels, and every plotted number is also in a committed CSV.
