# ARTEMIS: Adaptive Regional Timber Ecosystem Modeling through Iterative Simulation

ARTEMIS is an active research prototype for reproducible, spatially explicit forest
projection. It links **TreeMap**, **FIA tree lists**, the **Forest Vegetation Simulator
(FVS)**, remotely sensed landscape data, and iterative management scenarios to model how
forest structure, timber volume, and carbon change through time.

The intended v1 extent is Florida. Current implementation and validation work concentrates
on a five-county north Florida pilot before statewide and eastern-US expansion.

## Modeling frame

| Dimension | Current direction |
|---|---|
| Spatial reference | **EPSG:5070 — NAD83 / Conus Albers** (ArcGIS: `NAD_1983_Contiguous_USA_Albers`). Everywhere, for every raster and vector. |
| Working grid | 30 m, snapped to the TreeMap 2022 affine `[30, 0, -2361585, 0, -30, 3177435]` |
| Growth model | FVS Southern (`SN`) variant |
| Projection horizon | Approximately 50 years, using FVS cycles |
| Initial forest state | TreeMap 2022 linked to FIA/FVS-ready tree lists |
| Management evidence | LCMS tree removal, ownership, parcels, roads, water, and Florida BMP constraints |
| Compute model | GEE for remote raster preparation; local Python/FVS for joins, simulation, painting, and validation |
| Reproducibility | `uv`, pytest, fixed inputs/configuration, and documented iteration |

See [`PLAN.md`](PLAN.md) for the target architecture. It is a build plan, not a claim that
every stage is implemented.

### Coordinate reference system

**Everything is EPSG:5070, NAD83 / Conus Albers.** Equal-area, metres, standard parallels
29.5/45.5, latitude of origin 23, central meridian −96. It is declared once in
[`config/projection.yaml`](config/projection.yaml) and read through
[`pipeline/spatial_ref.py`](pipeline/spatial_ref.py); no module hardcodes it, and a test
enforces that.

It is the native CRS of TreeMap 2022, LANDFIRE EVT, and the Harris ownership raster — all
30 m, pixel-co-registered, and carrying categorical values. Staying on 5070 makes the
raster work reproject-and-snap only: nothing categorical is ever resampled.

Do not substitute a similarly named Albers. `ESRI:102008` (North America Albers) uses
standard parallels 20/60 and is off by kilometres; `EPSG:6350` (NAD83(2011) Conus Albers)
is off by less than a metre and still breaks a 30 m snap grid. Both still render as a
recognisable map, which is why `spatial_ref.assert_project_crs` names them explicitly when
it catches one. The full list is under `spatial.crs_not` in the config.

```bash
uv run python -m pipeline.spatial_ref     # print the declaration and the confusables
```

## Current implementation

- **Config and policy:** ownership classes, the management-regime library, and the fixed
  fallback tree lists are declared in `config/ownership_policy.yaml`,
  `config/management_regimes.yaml`, and `config/fallback_treelists.yaml`, and resolved by
  `pipeline/s3_management/owner_classes.py`, `regime_assignment.py`, and
  `pipeline/s4_fvs/fallback_treelists.py`. See [`docs/config-policy.md`](docs/config-policy.md)
  for what each decides and what is still an assumption.
- **Management-unit delineation:** two steps, in order —
  `sketch_management_units.py` (parcels ∩ forest, minus water and road artefacts, then
  partitioned into `managed` and grow-only `riparian` units by the BMP buffer layer) →
  `sliver_merge.py` (resolve sub-5-acre stands, with `unit_class` as a hard constraint so
  buffer acres are never absorbed into a harvest unit). Buffers are retained rather than
  erased, so their acres stay in the projected landscape and keep their own polygon
  identity. Segmentation, road-buffer policy, and terrain integration remain under review.
- **FVS raster painting:** `pipeline/s4_fvs/paint_fvs_to_raster.py` maps stand-level FVS
  trajectories back to TreeMap pixels for initial and final snapshots. It requires external
  five-county trajectory, crosswalk, and raster files.
- **GEE acquisition:** `gee/scripts/` exports LCMS, POLARIS, PRISM, and terrain inputs.
- **Imagery and embeddings:** `pipeline/s5_imagery/` pulls NAIP over an extent vector layer,
  verifying per year that the mosaic actually covers it, and clusters Earth Engine embeddings
  inside versus outside an area of interest. `viewer/` publishes both to the PERSEUS map viewer
  through a collapsible side panel.
- **Exploratory workflows:** `notebooks/` contains TreeMap summaries, clearcut-versus-
  agriculture investigations, embedding-based AOI search, and an experimental FVS smoke
  workflow.
- **Validation:** pytest covers configuration, TreeMap clipping, management-unit sketching,
  FVS painting, and reusable notebook helpers.

Detailed findings, run history, unresolved decisions, and environment-specific gotchas live in
[`notes/`](notes/README.md).

## Quickstart

ARTEMIS currently requires Python 3.14 and uses [`uv`](https://docs.astral.sh/uv/).

```bash
# Dependencies, git hooks, the DuckDB sqlite extension, and a report of which data
# source is reachable. Idempotent; --check reports without changing anything.
./scripts/setup-env.sh

# Run the tracked test suite. The explicit path avoids scanning external data links.
uv run pytest tests/

# Enable the tracked git hooks: reject staged files larger than 99 MiB (pre-commit),
# re-resolve uv.lock after a merge (post-merge), and refuse to push a uv.lock that
# does not match pyproject.toml (pre-push).
git config core.hooksPath .githooks

# Enable the uv.lock merge driver declared in .gitattributes, which regenerates
# the lockfile from the merged pyproject.toml instead of merging it line by line.
# Git will not take a driver command from a tracked file, so each clone maps the
# name itself. Skipping this only costs you the occasional uv.lock conflict.
git config merge.uv-lock.name "regenerate uv.lock from the merged pyproject.toml"
git config merge.uv-lock.driver "scripts/merge-uv-lock.sh %O %A %B"

# Start Jupyter for exploratory workflows
uv run jupyter lab
```

The same bootstrap runs in the two other environments, so all three agree on what
"configured" means: [`Dockerfile`](Dockerfile) builds a portable image around it
(`docker build -t artemis . && docker run --rm -it artemis uv run pytest tests/ -q`),
and [`scripts/claude-code-env-setup.sh`](scripts/claude-code-env-setup.sh) is the
version-controlled copy of the Claude Code cloud environment's setup script. See
[`notes/claude-code-web-environment.md`](notes/claude-code-web-environment.md) for the
sandbox-specific constraints behind it.

For Earth Engine workflows, authenticate separately:

```bash
uv run earthengine authenticate
```

Most production data is intentionally not stored in Git. Local paths are declared in
[`config/data_paths.yaml`](config/data_paths.yaml) and currently assume an external `/mnt/d`
mount. Update that configuration for another workstation or HPC environment.

Where that drive is not mounted, the same data is available from the Cloudflare R2 bucket
`artemis-r2`, which holds it under a `data/` prefix — `/mnt/d/<path>` is
`r2:artemis-r2/data/<path>`. `rclone` takes its credentials from the `RCLONE_CONFIG_R2_*`
environment variables, so neither an `rclone.conf` nor a committed secret is involved:

```bash
rclone copyto r2:artemis-r2/data/<path> /mnt/d/<path>
```

[`pipeline/data_access.py`](pipeline/data_access.py) resolves a declared path against the
drive first and the bucket second, fetching on demand, which is how the data-dependent tests
run without the mount. Pipeline modules still open their declared paths directly, so stage
those files before running. [`data/index.md`](data/index.md) catalogs every folder in the
bucket — size, contents, and the config key that points at it — and the header of
[`config/data_paths.yaml`](config/data_paths.yaml) documents the layout and access commands.

## Runnable workflows

### Draft management units

```bash
# Inspect the five-county pilot without writing outputs
uv run python -m pipeline.s3_management.sketch_management_units \
  --pilot-five-county --dry-run

# Build Union County and save QA layers
uv run python -m pipeline.s3_management.sketch_management_units \
  --county-fips 125 --save-qa --overwrite
```

Then resolve slivers:

```bash
uv run python -m pipeline.s3_management.sliver_merge \
  --input  data/interim/management_units/12125/candidate_management_units.gpkg \
  --output data/interim/management_units/12125/management_units_state0.gpkg
```

`sliver_merge` merges within `unit_class`. A BMP buffer is 35–75 ft wide, so riparian units
are almost all below the 5-acre minimum stand size; letting them merge across the line would
put unharvestable acres inside a harvest unit and destroy the managed/riparian partition.

Statewide `--all-florida` processing is not implemented; it currently exits with status 1.
Use `--pilot-five-county` or run supported counties individually.

See [`pipeline/README.md`](pipeline/README.md) and
[`notes/management_units.md`](notes/management_units.md) before promoting draft polygons.

### Paint FVS trajectories to TreeMap

After staging the expected trajectory and matching TreeMap files, run:

```bash
uv run python -m pipeline.s4_fvs.paint_fvs_to_raster
```

The script chooses between candidate TreeMap vintages by coverage and writes initial and final
basal-area GeoTIFFs. Do not combine a TreeMap 2020 crosswalk with a TreeMap 2022 raster. See
[`notes/fvs-to-raster-painting.md`](notes/fvs-to-raster-painting.md) for snapshot semantics and
known data-version traps.

### Export remote raster inputs

See [`gee/README.md`](gee/README.md) for commands and authentication requirements.

### Pull NAIP imagery and cluster embeddings for an area of interest

```bash
# NAIP for every requested year, with per-year coverage of the extent verified
uv run python -m pipeline.s5_imagery.naip_acquire \
  --extent config/study_extent.geojson --aoi config/stands.geojson \
  --years 2019,2021,2023

# Embeddings across the extent, clustered and split inside vs outside the AOI
uv run python -m pipeline.s5_imagery.embeddings \
  --extent config/study_extent.geojson --aoi config/stands.geojson --year 2024 --k 6

# Publish to the map viewer and open it
uv run python -m pipeline.s5_imagery.viewer_catalog \
  --naip-manifest data/interim/naip/stands/naip_manifest.json \
  --clusters data/interim/embeddings/stands/clusters.json
uv run python viewer/serve_viewer.py
```

This stage takes two vector layers by design: `--extent` is the footprint imagery must cover,
`--aoi` is the ground features under study, and the area between them is the control the
clustering is compared against. Requires Earth Engine authentication. See
[`pipeline/s5_imagery/README.md`](pipeline/s5_imagery/README.md) for coverage modes and outputs,
[`viewer/README.md`](viewer/README.md) for the viewer connection, and
[`notes/naip-imagery-embeddings-viewer.md`](notes/naip-imagery-embeddings-viewer.md) for current
status and open questions.

### Explore notebooks

See [`notebooks/README.md`](notebooks/README.md) for purpose, prerequisites, and the maintained
entry point for each notebook group.

## Repository map

```text
config/                    Spatial, BMP, projection, ownership/regime/treelist policy,
                           and local data-path configuration
data/                      Gitignored raw/interim/processed data products
gee/                       Google Earth Engine export scripts
notebooks/                 Exploratory analyses and reusable notebook helpers
pipeline/
  s3_management/           Draft management-unit generation
  s4_fvs/                  FVS trajectory-to-raster painting
  s5_imagery/              NAIP acquisition, embedding clustering, viewer catalog
research/mgmt_units/       Segmentation research, state, and next steps
scripts/                    Repository utility scripts
tests/                      Pytest suite
viewer/                     Map-viewer side panel and its build/serve script
notes/                      Durable findings, decisions, run status, and open questions
PLAN.md                    Target v1 architecture and build sequence
pyproject.toml             Python metadata and dependencies
uv.lock                    Locked Python environment
```

### Inspect the ownership and regime policy

```bash
# Owner classes and the TPO budget each charges against
uv run python -m pipeline.s3_management.owner_classes

# Fixed fallback tree lists and whether their donor plots are pinned yet
uv run python -m pipeline.s4_fvs.fallback_treelists
```

## Known constraints and open decisions

- Many workflows need both the project data and interactive Earth Engine credentials, so
  notebook availability is environment-dependent. The data can come from the local `/mnt/d`
  mount or, on a machine without it, from the `artemis-r2` bucket; Earth Engine has no such
  substitute.
- FIA inventory years differ among stands. The common trajectory anchors are the initial cycle
  and shared final year; arbitrary calendar years do not form complete synchronized snapshots.
- TreeMap raster, crosswalk, and FIA/FVS outputs must use the same TreeMap vintage.
- The draft management-unit workflow still needs visual QA and decisions on road buffers,
  large-unit splitting, terrain, and sub-2 ha sliver handling.
- The committed repository paints existing FVS output but does not yet provide a complete,
  automated FVS trajectory-generation pipeline.
- The DOR use-code table in `config/ownership_policy.yaml` is transcribed, not yet verified
  against the parcel layer (`--audit-parcels`), and the fallback tree lists have no pinned
  donor plots until `fallback_treelists --resolve` runs against the FIA database. Both need
  the local data mount.
- Natural disturbances, climate-modified growth, stochastic replicates, and formal uncertainty
  quantification remain outside v1 scope.

## Documentation maintenance

`notes/` records discoveries faster than stable documentation changes. Periodically—and before
merging a README update—review its status and index:

```bash
git status --short -- notes/
find notes -maxdepth 1 -type f -name '*.md' -printf '%f\n' | sort
```

Promote stable findings into the nearest README, leave experiment-specific details in notes, and
add a nested README only when a directory needs its own entry points, prerequisites, or operating
instructions. Keep [`notes/README.md`](notes/README.md) as the index rather than duplicating every
research detail in the root README.

## Primary datasets

- Houtman et al. (2025), TreeMap 2022 CONUS, DOI: `10.2737/RDS-2025-0032`
- Harris, Caputo & Butler (2025), forest ownership circa 2022, DOI: `10.2737/RDS-2025-0045`
- USFS Forest Inventory and Analysis (FIA) DataMart
- USFS Forest Vegetation Simulator, Southern variant
- LCMS v2024.10
- PRISM 1991–2020 normals
- POLARIS soils via the GEE community catalog
- USGS 3DEP terrain

Dataset version pinning and a publication-ready data dictionary remain planned deliverables.
