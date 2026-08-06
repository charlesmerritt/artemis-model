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
- **Management-unit delineation:** three steps, in order —
  `sketch_management_units.py` (parcels ∩ forest, minus water and road artefacts) →
  `sliver_merge.py` (resolve sub-5-acre stands) → `riparian_overlay.py` (cut the settled
  stands along the BMP buffers and classify the buffered pieces no-entry). Buffers are
  built in step 1 but applied only in step 3, so hydrography annotates the stand map rather
  than shaping it. Stands are contiguous and the overlay conserves area exactly, both
  enforced. Segmentation, road-buffer policy, and terrain integration remain under review.
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
```

Then resolve slivers and overlay the riparian buffers, in that order:

```bash
uv run python -m pipeline.s3_management.sliver_merge \
  --input  data/interim/management_units/12125/candidate_management_units.gpkg \
  --output data/interim/management_units/12125/management_units_state0.gpkg

uv run python -m pipeline.s3_management.riparian_overlay \
  --stands  data/interim/management_units/12125/management_units_state0.gpkg \
  --buffers data/interim/management_units/12125/riparian_buffers.gpkg \
  --output  data/interim/management_units/12125/management_units_final.gpkg
```

The overlay must run last. A BMP buffer is 35–75 ft wide, so buffer polygons are almost all
below the 5-acre minimum stand size — overlaying before `sliver_merge` would delete the
riparian layer outright.

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
research/mgmt_units/       Segmentation research, state, and next steps
scripts/                    Repository utility scripts
tests/                      Pytest suite
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

- The local `/mnt/d` data mount and interactive Earth Engine credentials are required for many
  workflows; notebook availability can therefore be environment-dependent.
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
