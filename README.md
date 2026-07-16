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
| Spatial reference | EPSG:5070 (CONUS Albers Equal Area) |
| Working grid | 30 m, aligned to TreeMap 2022 |
| Growth model | FVS Southern (`SN`) variant |
| Projection horizon | Approximately 50 years, using FVS cycles |
| Initial forest state | TreeMap 2022 linked to FIA/FVS-ready tree lists |
| Management evidence | LCMS tree removal, ownership, parcels, roads, water, and Florida BMP constraints |
| Compute model | GEE for remote raster preparation; local Python/FVS for joins, simulation, painting, and validation |
| Reproducibility | `uv`, pytest, fixed inputs/configuration, and documented iteration |

See [`PLAN.md`](PLAN.md) for the target architecture. It is a build plan, not a claim that
every stage is implemented.

## Current implementation

- **Management-unit sketching:** `pipeline/s3_management/sketch_management_units.py`
  processes Florida county-by-county and can create draft units from parcels, forest cover,
  roads, water, and BMP exclusions. A Union County smoke run has completed; segmentation,
  sliver merging, road-buffer policy, and terrain integration remain under review.
- **FVS raster painting:** `pipeline/s4_fvs/paint_fvs_to_raster.py` maps stand-level FVS
  trajectories back to TreeMap pixels for initial and final snapshots. It requires external
  five-county trajectory, crosswalk, and raster files.
- **GEE acquisition:** `gee/scripts/` exports LCMS, POLARIS, PRISM, and terrain inputs.
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
# Create the environment and install locked dependencies
uv sync

# Run the tracked test suite. The explicit path avoids scanning external data links.
uv run pytest tests/

# Enable the tracked hook that rejects accidentally staged files larger than 99 MiB
git config core.hooksPath .githooks

# Start Jupyter for exploratory workflows
uv run jupyter lab
```

For Earth Engine workflows, authenticate separately:

```bash
uv run earthengine authenticate
```

Most production data is intentionally not stored in Git. Local paths are declared in
[`config/data_paths.yaml`](config/data_paths.yaml) and currently assume an external `/mnt/d`
mount. Update that configuration for another workstation or HPC environment.

## Runnable workflows

### Draft management units

```bash
# Inspect the five-county pilot without writing outputs
uv run python -m pipeline.s3_management.sketch_management_units \
  --pilot-five-county --dry-run

# Build Union County and save QA layers
uv run python -m pipeline.s3_management.sketch_management_units \
  --county-fips 125 --save-qa --overwrite

# Process all Florida counties after pilot review
uv run python -m pipeline.s3_management.sketch_management_units --all-florida
```

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

### Explore notebooks

See [`notebooks/README.md`](notebooks/README.md) for purpose, prerequisites, and the maintained
entry point for each notebook group.

## Repository map

```text
config/                    Spatial, BMP, projection, and local data-path configuration
data/                      Gitignored raw/interim/processed data products
gee/                       Google Earth Engine export scripts
notebooks/                 Exploratory analyses and reusable notebook helpers
pipeline/
  s3_management/           Draft management-unit generation
  s4_fvs/                  FVS trajectory-to-raster painting
research/mgmt_units/       Segmentation research, state, and next steps
scripts/                    Repository utility scripts
tests/                      Pytest suite
notes/                      Durable findings, decisions, run status, and open questions
PLAN.md                    Target v1 architecture and build sequence
pyproject.toml             Python metadata and dependencies
uv.lock                    Locked Python environment
```

## Known constraints and open decisions

- The local `/mnt/d` data mount and interactive Earth Engine credentials are required for many
  workflows; notebook availability can therefore be environment-dependent.
- FIA inventory years differ among stands. The common trajectory anchors are the initial cycle
  and shared final year; arbitrary calendar years do not form complete synchronized snapshots.
- TreeMap raster, crosswalk, and FIA/FVS outputs must use the same TreeMap vintage.
- The draft management-unit workflow still needs visual QA and decisions on road buffers,
  large-unit splitting, terrain, and sub-2 ha sliver handling.
- The committed repository paints existing FVS output but does not yet provide a complete,
  automated FVS trajectory-generation pipeline.
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
