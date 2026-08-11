# Management Unit Pilot Workflow

## Goal

Create reproducible candidate management units for standing timber in Florida, roughly analogous to timber stands. The first iteration is exploratory: overlay local parcel, road, stream/water, and forest mask inputs for a five-county pilot area; buffer features that should not be part of units; clean/flag slivers; and summarize how much manual or algorithmic work remains before creating a statewide layer.

## Decisions captured from user context

- Raw data source is `data/raw/`, which is a local symlink to the data drive (`/mnt/d`). Treat `data/raw` as canonical in notebooks and documentation.
- Start with the five-county parcel AOI in `data/raw/FL_5_Co_Parcels.gdb`, not statewide parcels.
- Restrict management units to forested/standing-timber areas.
- Use LANDFIRE EVT to exclude developed areas, water, and other non-forest land covers.
- Apply Florida BMP stream buffers from `config/bmp_rules.yaml`.
- Apply a very small road buffer to overcome road/parcel alignment artifacts.
- Use existing plan defaults for small polygons: minimum about 2 ha and target max about 40 ha. For this exploratory notebook, small polygons should be flagged/summarized and kept in QA outputs so we can decide whether to merge or discard later.

## Verified local inputs

- `data/raw` exists and is a symlink to `/mnt/d`.
- Five-county parcels: `data/raw/FL_5_Co_Parcels.gdb`, layer `FL_5_Co_Parcels`.
  - Feature count: 99,914.
  - CRS: EPSG:26917 (NAD83 / UTM zone 17N).
  - Counties represented: Columbia, Suwannee, Hamilton, Baker, Union, plus one null county record.
- Roads: `data/raw/SE_rds100k/SE_rds100k.gdb`, layer `SE_rds100k`.
  - CRS: EPSG:4326.
  - Key fields: `MTFCC`, `RTTYP`, `STATEFP`, `COUNTYFP`.
- Streams: `data/raw/US SE Streams - FINAL/US SE Streams - FINAL/Streams By State/nhdplus_epasnapshot2022_fl.gdb`, layer `nhdflowline_fl`.
  - CRS: EPSG:4269.
  - Key field: `fcode`.
- Waterbodies: `data/raw/US SE Waterbodies Final/US SE Streams 10.20.2023/US SE Streams/US SE Streams.gdb`, layer `NHDWaterbody_DissolveBoundaries1`.
  - CRS: EPSG:4269.
  - Key field: `fcode`.
- LANDFIRE EVT: `data/raw/LF2022_EVT_CONUS/LF2022_EVT_CONUS/Tif/LF2022_EVT_CONUS.tif`.
  - CRS: EPSG:5070.
  - Resolution: 30 m.
  - VAT fields include `EVT_LF`, `EVT_ORDER`, and `EVT_NAME`; forest mask can start with `EVT_LF == "Tree"` or `EVT_ORDER == "Tree-dominated"`.

## Missing or not yet verified

- Local terrain derivative/export was not found under `data/raw` by searching for common terrain/DEM/slope/3DEP names. The existing config treats 3DEP terrain as a GEE-only source, so a terrain export may still need to be produced before terrain can be summarized in the management-unit notebook.
- NHD stream layer exposes `fcode` in quick inspection but not stream order or channel width. Until those are available, BMP classes may need a conservative/simple mapping from FCode to buffer class.

## Notebook scope

The initial notebook should live in `notebooks/` and should:

1. Inventory/validate input paths and layer names.
2. Build the five-county AOI from parcels and reproject all vector data to EPSG:5070.
3. Clip roads, streams, waterbodies, and LANDFIRE EVT to the AOI.
4. Derive a LANDFIRE forest mask from the EVT VAT.
5. Build stream BMP buffers and a small road artifact buffer.
6. Create exploratory candidate polygons from forested parcel areas after buffer/water erasure.
7. Flag polygons `< 2 ha`, summarize area/count distributions and expected work remaining.
8. Save interim QA layers under `data/interim/management_units_pilot/`.

## Statewide script implementation

Added `pipeline/s3_management/sketch_management_units.py` as the first production-style script for draft management units. It processes by Florida county so statewide parcels are never loaded all at once; clips parcels, roads, NHD flowlines, NHD waterbodies, and LANDFIRE EVT; builds a forest mask from the EVT VAT; intersects forested parcels; erases water and a small road buffer; partitions the remainder into `managed` and grow-only `riparian` units; optionally splits large managed polygons with a simple 40 ha fishnet; and writes per-county GeoPackage outputs plus CSV summaries.

Useful commands:

```bash
uv run python -m pipeline.s3_management.sketch_management_units --pilot-five-county --dry-run
uv run python -m pipeline.s3_management.sketch_management_units --county-fips 125 --no-split-large --save-qa --output-dir data/interim/management_units_smoke_union
uv run python -m pipeline.s3_management.sketch_management_units --all-florida
```

## Riparian retention and area accounting (2026-08-03)

Implemented [`methodology-directions.md`](methodology-directions.md) item 2. BMP stream buffers are
no longer erased. The script now emits two unit classes into one layer:

- `unit_class = "managed"`, `unit_id` prefix `mu_` — forest available for a harvest regime.
- `unit_class = "riparian"`, `unit_id` prefix `rb_` — the forested part of a BMP stream buffer.
  Grown, never harvested, never dissolved into the managed units it abuts, and carrying
  `buffer_class` so summaries can be cut by class without merging the polygons.

Buffer classes overlap on the ground, so `build_riparian_buffer_layer` subtracts each more
protective class from the narrower ones (`perennial_large` > `perennial_small` >
`ephemeral_intermittent`). The retained rows are therefore disjoint and `buffer_class` is
unambiguous. Only NHD waterbodies and the 3 m road-artifact buffer remain erase-only, and they are
made disjoint from each other so their areas sum.

`area_accounting.csv` per county checks, against the *post-exclusion* area:

    Σ managed + Σ riparian == (forest mask ∩ parcels) − (waterbodies ∪ road buffer)

with the excluded acres on their own lines. The run logs an error if `balance_residual` drifts past
a relative 1e-6. Union County closes at 2.4e-11 ha.

Large-unit fishnet splitting applies to **managed units only**. The 40 ha target bounds an
operational harvest unit; riparian buffers are never entered, so splitting them would invent
geometry with no management meaning.

### Bugs the accounting line caught

Five pre-existing defects surfaced once the areas were reconciled — the script could not run
end-to-end before this. All are fixed, and each one is a case the check caught rather than a case
anyone noticed by eye:

1. **Every clipped layer was empty.** `gpd.read_file(..., mask=...)` was passed a bare shapely box.
   pyogrio interprets a CRS-less geometry in the *target layer's* CRS, and roads/streams/waterbodies
   are EPSG:4326/4269, so an EPSG:5070 box matched nothing. The mask is now a CRS-aware `GeoSeries`.
2. **The forest mask was empty.** The EVT test was a hardcoded `1000 <= value < 3000`, which is an
   older LANDFIRE vintage. LF2022 codes tree classes across a non-contiguous 4402–9722 span
   interleaved with herb/developed/agriculture, so any range test both misses forest and admits
   non-forest. Now reads `EVT_LF == "Tree"` from the VAT CSV (467 classes), as this note's input
   inventory always specified.
3. **`clean_geometries` silently deleted valid forest.** Its unconditional `buffer(0)` is sensitive
   to ring orientation, which OGC validity does not constrain. A *valid* Union County MultiPolygon
   with clockwise-wound rings had a whole 2,186 m² part reinterpreted as a hole and erased — the
   entire county area-balance residual came from that one polygon. Repair is now `make_valid`,
   applied only to rows that are actually invalid. Regression test pins the real geometry.
4. **Dissolving the erase layer segfaulted GEOS on Baker.** The first implementation built one
   county-wide `union_all()` of the NHD waterbody layer before differencing. Baker has 8,573
   waterbody features, mostly Osceola National Forest swamp, and the union died with SIGSEGV
   (exit 139). It is also unnecessary: `gpd.overlay(..., how="difference")` differences each row
   against only the erase rows a spatial-index query says it touches, and applies overlapping
   erase rows successively. The erase layer is now left **undissolved**. Union County's exclusion
   step went from ~2.5 min to ~2 s, with byte-identical areas.

5. **The fishnet split silently deleted whole polygons.** `split_large_geometry` kept a clipped
   result only if its `geom_type` was `Polygon` or `MultiPolygon`. Clipping a *multipart* polygon
   with a cell that fully contains one part while merely **touching** a detached part returns
   `GeometryCollection([Polygon, Point])` — which matched neither branch and was dropped whole,
   polygon included. It passed the `part.area > 0` guard on the way, because a collection's area is
   the sum of its polygonal parts. Columbia County lost **23.07 ha** this way across 512 split
   units. Clipped results now go through `polygon_parts`, which recurses into any multipart
   geometry and keeps every Polygon, discarding only genuinely zero-area debris. A regression test
   reproduces the exact 38.75 ha loss.

**Never dissolve a county-scale erase layer.** If a future change needs the exclusion geometry as
one shape, it needs a chunked or tiled union, not `union_all()`.

**Never match on `geom_type` to filter overlay/clip output.** Any GEOS operation on multipart input
can return a `GeometryCollection`; extract polygonal parts instead. Defects 3 and 5 are both this
mistake in different clothing, and both deleted forest without a warning.

### How the exclusion areas are measured

Waterbodies and the road buffer overlap each other, so their areas cannot simply be added. They are
erased **one class at a time** and each class is credited with exactly the area it was first to
remove. The lines then telescope to the total drop by construction — no union, no double counting,
and `excluded_road_buffer` reads as "road-artifact area that was not already water".

`PILOT_COUNTIES` also listed `089` (Nassau, zero parcels in the AOI) instead of `121` (Suwannee,
31,081 parcels), and Suwannee was missing from `county_name_map` — which meant an unmapped county
ran *unfiltered* rather than failing. Both fixed; an unmapped FIPS now refuses to run.

## Next implementation steps

- **Decide how NHD swamp/marsh should be treated** — see [`methodology-directions.md`](methodology-directions.md) item 5. This is the largest open number in the pilot.
- Inspect the QA GeoPackages in `data/interim/management_units_5co/<fips>/qa/`.
- Decide whether road buffer should be 3 m, 5 m, or tied to road class.
- Improve BMP classification if NHDPlus stream order/channel width fields are available elsewhere.
  Today no stream classifies as `perennial_large`, because `classify_stream_fcode` has no stream
  order or channel width to work from — every FCode 46006 reach is treated as `perennial_small`
  (50 ft instead of 75 ft). The 75 ft class is wired through and will populate as soon as the
  attribute exists.
- Decide whether the fishnet split for polygons `> 40 ha` is acceptable for draft statewide units or whether large units should wait for raster segmentation.
- Add terrain raster once staged locally.
- Decide whether parcel preference is worth keeping in `sliver_merge.py` — it is correct but
  barely fires on this data (see below).

## Resolved: `sliver_merge.py` merges to the best neighbour, within `unit_class`

The merge policy in `pipeline/s3_management/sliver_merge.py` now picks a sliver's neighbour by
ranking candidates on **`(same parcel, shared boundary length)`**, restricted to candidates of the
sliver's own `unit_class`. Isolated fragments still fall through to the nearest same-class unit
(LETO `GenerateNearTable` style); parcel preference deliberately does *not* apply there, because
with no shared edge distance is the only trustworthy signal.

**`unit_class` is a hard constraint, not a preference.** This closes the gap flagged when riparian
retention landed: merging across the line would put unharvestable buffer acres inside a harvest
unit and destroy the managed/riparian partition. It is not a theoretical risk — measured on the
Union County layer *before* the constraint, **5,634 of 14,327 first-pass merges (39.3%) crossed the
line**, near-symmetrically in both directions. Inputs with no `unit_class` column (the pre-riparian
layers) are treated as one implicit class, so old outputs still merge as they used to.

### Verified on Union County (`data/interim/management_units_smoke_union/12125`)

20,253 singlepart polygons (17,900 slivers) → **2,695 units, 0 slivers remaining, min 5.00 ac**:

- **0 assignments cross `unit_class`.**
- Per-class area conserved: managed `6.2e-05`, riparian `2.3e-06` relative. The residual is
  `unary_union(...).buffer(0)` cleanup in the dissolve, not lost polygons.

### Parcel preference is correct but rarely applicable here

Only **164 of 12,131 slivers (1.4%)** have a same-parcel *and* same-class boundary neighbour at
all. Where the signal exists it bites — it redirects **73 of those 164 (44.5%)** away from the
longest-edge pick — but the net effect on the county is 73 of 12,131 first-pass assignments (0.6
points, 99.2% → 98.6% cross-parcel). The reason is structural: fragments are already clipped to
parcels, so a sliver only gets a same-parcel neighbour when that parcel's forest was split by an
erased road or stream buffer. Keep or drop the rule on principle, not on its measured effect.
