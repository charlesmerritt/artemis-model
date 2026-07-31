# R2 bucket index — `r2:artemis-r2/data`

Catalog of the object-storage mirror of the `/mnt/d` workstation drive: **5,499
objects, 351 GB**. Everything here is reachable without the drive —
`/mnt/d/<path>` is `r2:artemis-r2/data/<path>` — and `pipeline/data_access.py`
resolves declared paths against whichever source answers. See the header of
[`config/data_paths.yaml`](../config/data_paths.yaml) for access commands.

Sizes are decimal GB from object metadata, captured 2026-07-31:

```bash
rclone lsf -R --fast-list --format "pst" r2:artemis-r2/data/<folder>
```

## Pipeline inputs

Declared in `config/data_paths.yaml`; the config key is the last column.

| Folder | Size | Objects | What it is | Key |
|---|--:|--:|---|---|
| `TreeMap-2022/` | 5.16 GB | 8 | TreeMap 2022 CONUS imputed FIA plot-ID raster (30 m) with tree table, VAT, and data dictionary | `raw.treemap_2022` |
| `SQLite_FIADB_ENTIRE/` | 70.70 GB | 1 | Entire FIA database as one SQLite file, including the FVS-ready STANDINIT/TREEINIT tables | `raw.fia_sqlite` |
| `RDS-2025-0045/` | 5.15 GB | 10 | Forest ownership raster circa 2022, plus its overview pyramid | `raw.ownership` |
| `LF2022_EVT_CONUS/` | 4.19 GB | 25 | LANDFIRE 2022 Existing Vegetation Type raster and spatial metadata | `raw.landfire.evt_tif` |
| `LF2022_VCC_CONUS/` | 2.23 GB | 25 | LANDFIRE 2022 Vegetation Condition Class raster, same layout | `raw.landfire.vcc_tif` |
| `US SE Streams - FINAL/` | 6.51 GB | 988 | EPA NHDPlus 2022 snapshot, per-state stream geodatabases | `raw.nhd.fl_gdb` |
| `US SE Waterbodies Final/` | 18.03 GB | 178 | Southeast waterbodies/streams gdb; two tables carry 13 of the 18 GB | `raw.nhd.se_gdb` |
| `All_FL_Parcels/` | 7.34 GB | 69 | Statewide Florida parcels with land-use class — a single 7.1 GB gdb table | `raw.parcels.fl_all` |
| `FL_5_Co_Parcels.gdb/` | 0.08 GB | 69 | Five-county parcel subset the management-unit sketcher reads | `raw.parcels.fl_5co` |
| `SE_rds100k/` | 1.30 GB | 67 | Southeast roads gdb (MTFCC, RTTYP) plus the MTFCC code reference PDF | `raw.roads.se_gdb` |
| `BdyAdm_LSRS_AdministrativeForest.gdb/` | 0.02 GB | 63 | USFS administrative forest boundaries | `raw.usfs_admin.gdb` |
| `county_p010g.shp_nt00934/` | 0.05 GB | 8 | USGS national county polygons, 2014 vintage | `raw.counties.p010g_shp` |
| `us_eco_l3/`, `us_eco_l4/` | 0.04, 0.10 GB | 7 each | EPA Level III/IV ecoregions; the `_no_st` files are not split at state lines | `raw.ecoregions.us_eco_l*_shp` |
| `us_eco_l3_state_boundaries/`, `us_eco_l4_state_boundaries/` | 0.05, 0.11 GB | 7 each | The same ecoregions, split at state lines | `raw.ecoregions.*_state_boundaries_shp` |
| `Artemis_project_fvs_copy_no_management/` | 1.12 GB | 261 | FVS Online project for the no-management run: `FVS_Data.db`, `FVSOut.db`, three summary CSVs, 249 report PNGs | `raw.Artemis_project_fvs_copy_no_management` |
| `Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx` | 30 KB | 1 | TPO harvest-target workbook the scheduler parses (bucket root, not a folder) | `raw.tpo_guidance.xlsx` |

The FVS project folder is the one name that differs between the two sources: on
the drive it is `Artemis_project_fvs_copy`. `data_paths.yaml` records the rename.

## Repository data mirror

| Folder | Size | Objects | What it is |
|---|--:|--:|---|
| `Artemis_data/` | 1.50 GB | 44 | The repo's gitignored `data/` tree: `interim/` (`clearcut_ag`, `management_units_pilot`, `management_units_smoke_union`, `no_management_fl5co_fvs_output`, `similarity_finder`, `treemap_county_summary`) and `processed/no_management_fl5co_rasters`. Maps to `<repo>/data/`, not to `/mnt/d`. |

Two things it does **not** contain: `treemap_2022_fl.tif`, so the Step-1 clip
tests still skip until the clip is run, and any bytes for
`interim/florida_boundary_5070.gpkg`, which is present but zero-length.

## Referenced by notes and research, not by `data_paths.yaml`

| Folder | Size | Objects | What it is |
|---|--:|--:|---|
| `TreeMap_Chaz/` | 79.59 GB | 314 | Largest folder — the R prototype workspace: numbered scripts `01`–`06`, per-state FIADB subsets (GA 5.9 GB, AL 3.9 GB), `output/` and `output2020/` TM_ID→PLT_CN crosswalks, and the TreeMap 2020 (`RDS-2025-0031`) and 2022 (`RDS-2025-0032`) CONUS rasters. See `notes/treemap-fvs-workflow.md`; the 2020-vs-2022 vintage trap lives here. |
| `USGS-13-arcs-DEM/` | 30.19 GB | 75 | USGS 3DEP 1/3 arc-second tiles covering Florida plus an 18 GB EPSG:5070 mosaic; the terrain input named in `research/mgmt_units/BRIEF.md`. |
| `tl_2022_us_state/` | 0.02 GB | 7 | Census TIGER 2022 state boundaries — what the clearcut notebooks reach for through the dead `data/raw → /mnt/d` symlink (`notes/notebooks.md`). |
| `TreeMap-2022_Metadata_Fileindex/` | 3 MB | 3 | HTML/XML metadata for the TreeMap 2022 archive (`notes/treemap-methodology.md`). |

## Not referenced anywhere in the repo

Adjacent or prior work, kept for provenance. Nothing in `pipeline/`, `tests/`,
`notebooks/`, `research/`, or `notes/` reads any of these.

| Folder | Size | Objects | What it is |
|---|--:|--:|---|
| `ml_data/` | 29.62 GB | 9 | Zipped side projects: stand delineation, tree-count segmentation, Whitehall Planet imagery and stand data, a titanic CSV set |
| `directedStudy-treecountseg/` | 21.18 GB | 284 | TreeCountSegHeight deep-learning code and outputs; a single 21 GB data zip is nearly all of it |
| `TreeMap-Vintage/` | 10.28 GB | 4 | Zipped TreeMap 2016 and 2020 releases with their metadata indexes |
| `OSMSouth.gdb/` | 2.86 GB | 112 | OpenStreetMap extract for the South as a file geodatabase |
| `Bark Images (UGA V1)/` | 2.06 GB | 2,765 | 2,764 labeled bark photographs (UGA, April 2026) and one label CSV |
| `athens_naip_2023/` | 1.58 GB | 9 | Three NAIP tiles over Athens, GA, with sidecar metadata |
| `USA Soils Map Units NRCS Polygon - ZIP ONE/` | 0.24 GB | 54 | NRCS soil map-unit polygons in a `Default.gdb` |
| `states_p010g.shp_nt00938/` | 0.01 GB | 13 | USGS national state polygons, companion to the county set |

Loose files at the bucket root, same status: `osm_data.gpkg` (24.33 GB),
`north-america-latest.osm.pbf` (19.14 GB), `LF2024_EVT_CONUS.zip` (3.83 GB),
`FL_5_Co_FIA.zip` (2.00 GB), `FL_5_Co_Streams.zip` (1.6 MB). The two OSM
extracts alone are 12% of the bucket.

## Refreshing this index

```bash
rclone lsf r2:artemis-r2/data --dirs-only
rclone size r2:artemis-r2/data/<folder>
```

`--fast-list` matters: a recursive listing without it walks prefix by prefix and
takes minutes per folder instead of seconds.
