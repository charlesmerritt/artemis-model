# Weekly artifact — 2026-08-24

## Artifact

**The Florida BMP riparian layer, carved into the pilot landscape as stands in its own
right** — 11,155 acres of stream-management zone lifted out of the scheduling landscape as
6,602 grow-only riparian stands, and the trajectory library re-enumerated against what is
left.

Every weekly artifact since 2026-08-10 has carried the same caveat, in the same words:

> **No riparian exclusion yet.** `SMZ_Pct` is 0 for every unit because no buffer layer is
> joined, so the absolute no-entry riparian rule in `regime_assignment` never fires.
> `notes/methodology-directions.md` item 2 is the outstanding work.

The rule is declared as executable policy in `config/management_regimes.yaml` and
`regime_assignment` asserts its `absolute` flag on import, so the repository has been
carrying a load-bearing rule that had never met a real stream. This artifact runs it.

**The model, stated plainly.** A BMP buffer is not an attribute of the stand it happens to
fall in. It is set by the stream, so it **cuts** every stand it crosses, and the corridor
it carves out becomes a stand of its own — grown on the same cycles as everything else,
never entered, addressable in its own right. The stands it crossed are truncated at its
edge. Nothing in that sentence is a share or a threshold: a stand is riparian or it is not,
and the geometry decides. `SMZ_Pct` is the derived label (100 inside, 0 outside) that lets
the existing override read the geometry; it is not a dial. This is what
`sketch_management_units.py` already does for parcel polygons (`unit_class = "riparian"`,
retained rather than erased, with `sliver_merge.py` forbidden from absorbing them). What
had never been measured is what the carve does to the *scheduling* landscape.

| File | What it is |
|---|---|
| `riparian_stand_mechanic.png` | The carve on real geometry — real parcels × forest, the buffer drawn across them, the corridor standing alone with the boundaries erased inside it. |
| `riparian_map.png` | The AOI map: forested land inside a stream-management zone across the five counties, plus the densest 3 km window at buffer resolution. |
| `riparian_overlay.png` | Four-panel render of the summary CSVs. |
| `riparian_stands.csv` | **The layer.** One row per riparian stand: id, corridor, county, owner class, acres, plots drawn on, majority TreeMap plot and its `PLT_CN`. 6,602 rows. |
| `riparian_corridors.csv` | One row per contiguous corridor before the ownership/county cut: acres, units cut, plots, owner classes, counties spanned. 6,092 rows. |
| `corridor_readings.csv` | The corridor layer counted five ways — as drawn, where it must be cut, and what it comes to as stands. |
| `corridor_size_distribution.csv` · `corridor_units_cut.csv` | Corridor size bands, and how many scheduling units each corridor cuts. |
| `riparian_decision_space.csv` | The riparian half of the carved decision space: every riparian stand with its one option. |
| `smz_by_unit.csv` | The measured SMZ acreage of each pre-carve scheduling unit — the raw material for the carve. |
| `smz_by_county.csv` · `smz_by_owner.csv` · `smz_by_forest_branch.csv` · `smz_by_buffer_class.csv` | Riparian acres and share, cut four ways. |
| `library_riparian_delta.csv` | The decision space before and after the carve. |
| `make_riparian_overlay.py` · `make_corridors.py` | The drivers. The overlay reads `riparian_stands.csv`; each script bootstraps whatever cache the other one owns if it's missing, so either can run first on a clean checkout. |
| `make_map.py` · `make_mechanic.py` · `make_figure.py` | The three renders. `make_figure.py` needs only the committed CSVs. |

**Why this artifact.** It is the oldest standing caveat in the weekly series and the one
piece of declared policy in `config/` that no run had ever exercised. It is not a repeat:
`2026-07-19` and `2026-08-03` shipped FVS-painted basal-area rasters, `2026-07-26` the
harvest-scheduling analysis figures, `2026-08-10` the constrained schedule, and
`2026-08-17` the enumerated trajectory library. This week carves the geometry those all
assumed away and hands the annealer a landscape with the no-entry stands already in it.

**Not fabricated.** Every modelling decision belongs to committed repository code:
`sketch_management_units.classify_stream_fcode` (NHD FCode → BMP class),
`build_riparian_buffer_layer` (the disjoint, priority-ordered buffer layer),
`feet_to_meters`, `MIN_UNIT_AREA_HA`, `regime_assignment._is_riparian`,
`eligible_prescriptions`, `forest_type_branch`, `owner_classes.classify_owner`, and
`paint_fvs_to_raster.load_crosswalk`. Buffer widths come from `config/bmp_rules.yaml`, the
override prescription from `config/management_regimes.yaml`. Three checks make the carve
auditable: the pre-carve attribution reproduces `weekly-artifact/2026-08-10/make_schedule.py`
**exactly** (5,240 units, 676 of 693 FVS stands, 925,098 acres — asserted in the driver);
the carve conserves area to under an acre (also asserted); and with the riparian layer
switched off the enumeration reproduces last week's library exactly (15,747 rows, 3,788
runs). `uv run pytest tests/ -q` → **798 passed, 10 skipped**; `ruff check` clean.

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
ownership screen.

**The carve makes 6,602 riparian stands, and it cuts a lot of stands to get them.**

| Reading | Stands | Acres | |
|---|---:|---:|---|
| Contiguous corridors, as drawn | 6,092 | 11,155 | one stand per contiguous run of riparian forest |
| …spanning >1 ownership class | 452 | 3,080 | must be cut — a stand cannot straddle two owners |
| …spanning >1 county | 37 | 259 | must be cut — TPO caps are per county |
| **Riparian stands (corridor × county × owner)** | **6,602** | **11,155** | the layer as it enters the scheduler |
| …below `MIN_UNIT_AREA_HA` (2 ha = 4.94 ac) | 6,112 | 6,047 | slivers by construction |
| Corridors drawing on >1 TreeMap plot | 4,143 | 10,542 | **not** a cut — `assign_plt_cn` imputes from the area-weighted plot mix |

Corridors are small and long: median **0.67 ac**, largest 70 ac, 69% of riparian acres in
corridors of 2–50 ac. And they are not politely contained — only 1,937 corridors (605 ac)
sit inside a single scheduling unit, while **270 corridors cut more than ten units each**
(3,662 ac). That is the quantitative form of the picture: the buffer ignores the unit
fabric entirely.

**What the carve costs the decision space:**

| | Stands | Library rows | FVS runs | No-entry acres | Acres with ≥1 cutting option |
|---|---:|---:|---:|---:|---:|
| Before (2026-08-17 baseline) | 5,240 | 15,747 | 3,788 | 0 | 925,098 |
| After the carve | 11,831 | 22,317 | 3,782 | 11,155 | 913,943 |

The carved landscape is 5,229 upland remainders plus 6,602 riparian stands — eleven units
disappear entirely, having been wholly inside a buffer.

**The FVS batch barely moves: 3,788 → 3,782 runs.** Riparian stands need no new runs at
all, because `no_management` is universally eligible and every stand already carries that
run; the six that vanish are `(plot, prescription)` pairs whose last eligible unit was
carved away. The cost of riparian protection lands on the objective, not the compute —
11,155 acres leave the harvestable base, concentrated where the streams are. Union County
loses 2.63% of its forest against Suwannee's 0.81%; family forest carries 7,504 ac (67%) of
the riparian acres against federal land's 891 ac; hardwood-branch forest is 1.87% riparian
against pine's 0.68%, which is the expected signal — bottomland hardwood sits along water.

## Three things a reader should take to the code

1. **`perennial_large` is unreachable.** `config/bmp_rules.yaml` declares a 75 ft class for
   perennial streams ≥15 ft wide, keyed on "FCodes 46006, Strahler order 3+". But
   `classify_stream_fcode` maps *every* 46006 to `perennial_small`, with the comment
   "defaulting to small for conservative buffer" — and 50 ft is the *less* protective
   choice, so the rationale is inverted. No stream in the pilot receives the widest buffer,
   and 11,155 ac is a lower bound. Strahler order is not read from NHD at all.
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
   than buffering them. Joining `US SE Waterbodies Final` is the obvious next increment.

## A note on `overrides.riparian.min_value`

The config exposes the rule as a 50% threshold on `SMZ_Pct`. Under the model above that
number can never matter: every stand in the carved landscape is 100 or 0, so any cutoff
strictly between them behaves identically, and `docs/config-policy.md` says as much —
`sketch_management_units` sets `SMZ_Pct = 100.0` on riparian units precisely so the
override "fires through the already-tested path". The threshold is a switch that reads a
label, not a modelling decision, and the label should be derived from `unit_class` rather
than tested against a cutoff. Worth noting because it is *not* harmless on a landscape of
mixed units: the pre-carve scheduling units are pixel classes, and evaluated as shares only
30 of 5,240 of them — 18.9 acres — would ever reach 50%. Deleting the knob in favour of a
`unit_class == "riparian"` predicate is a config-schema change to `regime_assignment`, out
of scope for an artifact PR, but it is the honest end state.

## Caveats

- **The stand geometry is pixel-derived, not polygon-derived.** Corridors are 8-connected
  runs of 30 m TreeMap pixels, and the upland remainders are still
  `TreeMap plot × county × ownership class` minus their buffered acres, because the Phase
  2.3 unit × stand crosswalk does not exist yet. The acres and the no-entry set are exact;
  the stand *boundaries* will change when polygon units land. `make_mechanic.py` shows the
  polygon version on real parcels for one window, which is where this is heading.
- **A riparian stand's tree list is the majority plot.** `riparian_stands.csv` carries the
  majority TreeMap plot and its `PLT_CN`; the real pipeline would run `assign_plt_cn` and
  impute from the area-weighted mix of all plots inside the stand. Every stand resolved to
  a `PLT_CN` — none is orphaned.
- **6,112 riparian stands are below the repo's own 2 ha minimum unit area.** That is
  inherent, not a defect: a 50 ft strip is a sliver by construction, and
  `notes/methodology-directions.md` item 2 is explicit that `sliver_merge` must never
  dissolve them into neighbours. It does mean the riparian layer roughly triples the stand
  count for 1.2% of the acreage, which is the same shape as the LETO run's "31% of stands
  for 2.5% of area" and is worth planning for.
- **Grow-only is asserted, not simulated.** Riparian stands get `no_management` here. Their
  trajectories still have to be produced by the FVS batch like any other stand.
- **Pixel-centre membership.** A 30 m pixel is in the SMZ when its centre is (rasterio's
  default). At aggregate the bias is −0.23% against the vector area.
- **Ownership disagreement is unchanged.** The 169,562 ac (15.5%) that Harris calls
  non-forest/unknown/water where TreeMap calls it forest are dropped before any of this,
  and 874 ac of SMZ goes with them; a further 2,327 ac of buffer sits on TreeMap pixels
  that are not forest at all.

## R2 inputs pulled

Only the inputs the run reads, from bucket `r2:artemis-r2` (bucket `data/` maps to the
repo's `/mnt/d`). **No downloaded data is committed** — everything lands under gitignored
`data/`.

| R2 key | Local path | Size |
|---|---|---|
| `data/US SE Streams - FINAL/US SE Streams - FINAL/Streams By State/nhdplus_epasnapshot2022_fl.gdb` | `data/interim/nhd/` | 93 MB |
| `data/FL_5_Co_Parcels.gdb` | `data/interim/parcels/` | 76 MB |
| `data/Lowe_TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif` | `data/interim/treemap5co/` | 7.2 MB |
| `data/Lowe_TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv` | `data/interim/treemap_link/` | 64 KB |
| `data/Lowe_TreeMap_Chaz/output/FL_5county_TreeMap_summary.csv` | `data/interim/treemap_link/` | <1 KB |
| `data/county_p010g.shp_nt00934/countyp010g.{shp,shx,dbf,prj}` | `data/interim/counties/` | 48 MB |
| `data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv` | `data/interim/no_management_fl5co_fvs_output/` | 2.9 MB |
| `data/RDS-2025-0045/Data/US_forest_ownership.tif` | **not downloaded** — read remotely | 3.87 GB on R2 |

The NHD geodatabase is the path `config/data_paths.yaml` already names (`nhd.fl_gdb`); only
the AOI's 9,982 flowlines are read out of its 417,354, and the parcels are read only over
the 900 m mechanic window. The Harris ownership raster is never copied — the driver opens
it through GDAL `/vsis3` against the same R2 endpoint and reads only the tiles under the
pilot AOI via a `WarpedVRT` onto the TreeMap grid (~33 s).

## Exact commands

```bash
# inputs (rclone remote `r2` is preconfigured via RCLONE_CONFIG_R2_* env vars)
rclone copy "r2:artemis-r2/data/US SE Streams - FINAL/US SE Streams - FINAL/Streams By State/nhdplus_epasnapshot2022_fl.gdb" \
  data/interim/nhd/nhdplus_epasnapshot2022_fl.gdb
rclone copy r2:artemis-r2/data/FL_5_Co_Parcels.gdb data/interim/parcels/FL_5_Co_Parcels.gdb
rclone copy r2:artemis-r2/data/Lowe_TreeMap_Chaz/FiveFloridaCounties/ data/interim/treemap5co/ \
  --include "TreeMap2022_CONUS_5FlCntys.tif"
rclone copy r2:artemis-r2/data/Lowe_TreeMap_Chaz/output/ data/interim/treemap_link/ \
  --include "FL_5county_TreeMap_TMIDs.csv" --include "FL_5county_TreeMap_summary.csv"
rclone copy r2:artemis-r2/data/county_p010g.shp_nt00934/ data/interim/counties/ \
  --include "countyp010g.shp" --include "countyp010g.shx" \
  --include "countyp010g.dbf" --include "countyp010g.prj"
rclone copy r2:artemis-r2/data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv \
  data/interim/no_management_fl5co_fvs_output/

# the run — either order works, each driver bootstraps what the other needs on a clean
# checkout (make_corridors builds the buffer cache if missing; make_riparian_overlay runs
# make_corridors if riparian_stands.csv is missing). Overlay-first is the cheaper order:
# it avoids rebuilding the buffer layer a second time (see the Caveats note below).
uv run python weekly-artifact/2026-08-24/make_riparian_overlay.py   # the carved decision space
uv run python weekly-artifact/2026-08-24/make_corridors.py          # the riparian stand layer (already built if it ran above)
uv run python weekly-artifact/2026-08-24/make_mechanic.py           # riparian_stand_mechanic.png
uv run python weekly-artifact/2026-08-24/make_map.py                # riparian_map.png
uv run python weekly-artifact/2026-08-24/make_figure.py             # riparian_overlay.png
```

## Dependencies

No new dependencies. The committed `uv.lock` environment was used as-is: Python 3.14,
pandas, geopandas (pyogrio/GDAL for the file geodatabases), rasterio (GDAL with `/vsis3`),
shapely, scipy (connected-component labelling), matplotlib, PyYAML. `uv sync` reproduces it.

## How to regenerate

```bash
uv sync
# stage the R2 inputs with the rclone commands above, then run the five scripts. Order
# between make_riparian_overlay.py and make_corridors.py doesn't matter — each bootstraps
# the other's cache if it's missing — but overlay-first avoids rebuilding the buffer layer
# a second time (see Caches below).
```

Caches, all gitignored: `data/interim/smz_pixels_attributed.csv` (pixel attribution),
`data/interim/owner_grid_treemap.npy` (the warped ownership grid),
`data/interim/riparian_corridor_labels.npy` (corridor labels), and
`data/interim/smz_{buffers,streams}_5070.gpkg` (buffer and flowline geometry). Delete them
to force the ownership warp and the buffer build to re-run. Output is deterministic — no
sampling, no random seeds; both figure windows are chosen by deterministic argmax (densest
SMZ box for the map, most-units-cut corridor for the mechanic).

Figure colours are the Okabe–Ito-derived categorical set, validated with the dataviz
palette checker (light surface `#fcfcfb`): lightness band, chroma floor, CVD separation
(worst adjacent pair ΔE 9.6 deutan) and normal-vision floor all pass; the low-contrast
hues carry direct value labels, and every plotted number is also in a committed CSV.
