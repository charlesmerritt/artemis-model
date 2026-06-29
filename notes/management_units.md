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

Added `pipeline/s3_management/sketch_management_units.py` as the first production-style script for draft management units. It processes by Florida county so statewide parcels are never loaded all at once; clips parcels, roads, NHD flowlines, NHD waterbodies, and LANDFIRE EVT; builds a forest mask from the EVT VAT; intersects forested parcels; erases Florida BMP buffers, water, and a small road buffer; optionally splits large polygons with a simple 40 ha fishnet; and writes per-county GeoPackage outputs plus CSV summaries.

Useful commands:

```bash
uv run python -m pipeline.s3_management.sketch_management_units --pilot-five-county --dry-run
uv run python -m pipeline.s3_management.sketch_management_units --county-fips 125 --no-split-large --save-qa --output-dir data/interim/management_units_smoke_union
uv run python -m pipeline.s3_management.sketch_management_units --all-florida
```

Smoke test run for Union County (`--county-fips 125 --no-split-large --overwrite --save-qa`) completed successfully. It read 6,960 clipped parcels, 1,997 roads, 2,530 streams, 2,950 waterbodies, and produced 17,020 candidate polygons before large-unit splitting.

## Next implementation steps

- Inspect the QA GeoPackages in `data/interim/management_units_pilot/` and `data/interim/management_units_smoke_union/`.
- Decide whether road buffer should be 3 m, 5 m, or tied to road class.
- Improve BMP classification if NHDPlus stream order/channel width fields are available elsewhere.
- Decide whether the fishnet split for polygons `> 40 ha` is acceptable for draft statewide units or whether large units should wait for raster segmentation.
- Add terrain raster once staged locally.
- Implement merge-to-best-neighbor logic for `< 2 ha` slivers after reviewing pilot outputs.
