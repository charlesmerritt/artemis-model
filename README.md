# ARTEMIS: Adaptive Regional Timber Ecosystem Modeling through Iterative Simulation

ARTEMIS is an active research prototype for reproducible, spatially explicit forest
projection. It links **TreeMap**, **FIA tree lists**, the **Forest Vegetation Simulator
(FVS)**, remotely sensed landscape data, and iterative management scenarios to model how
forest structure, timber volume, and carbon change through time.

The intended v1 extent is Florida. Current implementation and validation work concentrates
on a five-county north Florida pilot before statewide and eastern-US expansion.

## How ARTEMIS decides management

ARTEMIS builds a **library of candidate trajectories for every stand**, where the stand's
**ownership class** determines which management prescriptions are eligible for it. FVS runs
once per `(stand, prescription)` pair, offline and without restart barriers. A **harvest
scheduler then uses simulated annealing** to select one trajectory per stand, subject to
volume, flow, adjacency, and reserve constraints.

Simulation enumerates what each stand *could* do; the scheduler decides what each stand
*will* do. Because every candidate is precomputed, evaluating a whole landscape plan costs
a table lookup and a sum rather than an FVS run — which is what makes searching the
decision space affordable at all.

[`notes/trajectory-library-and-annealing.md`](notes/trajectory-library-and-annealing.md) is
the design of record.

## Modeling frame

| Dimension | Current direction |
|---|---|
| Spatial reference | EPSG:5070 (CONUS Albers Equal Area) |
| Working grid | 30 m, aligned to TreeMap 2022 |
| Growth model | FVS Southern (`SN`) variant |
| Projection horizon | Approximately 50 years, using FVS cycles |
| Initial forest state | TreeMap 2022 linked to FIA/FVS-ready tree lists |
| Simulation unit | Management-unit polygon, initialized from the area-weighted union of its FIA plots' tree lists |
| Decision space | Per-stand trajectory library; eligible prescriptions set by ownership class ([`config/prescriptions.yaml`](config/prescriptions.yaml)) |
| Management selection | Simulated annealing over one trajectory per stand |
| Constraints | TPO volume caps, even flow, adjacency/green-up, opening size; riparian no-entry and eligibility screens enforced structurally |
| Management evidence | LCMS tree removal, ownership, parcels, roads, water, and Florida BMP constraints |
| Compute model | GEE for remote raster preparation; local Python/FVS for joins, simulation, painting, and validation; parallel FVS workers for library generation |
| Reproducibility | `uv`, pytest, fixed inputs/configuration, locked scheduler seed and objective weights |

See [`PLAN.md`](PLAN.md) for the target architecture. It is a build plan, not a claim that
every stage is implemented.

## Guiding references

Two documents guide the methodology; see
[`docs/references/README.md`](docs/references/README.md) for citations, status, and what
each contributes.

- **`LAMPS`** — Bettinger & Lennette et al., Landscape Management Policy Simulator:
  eligibility screening, adjacency and green-up, heuristic harvest scheduling.
- **`CLIMATE-FVS`** — Climate-FVS Simulation Report (GMUG, 2015): FVS-driven alternative
  management trajectories per stand.

Neither PDF is committed yet. Both are expected in `docs/references/`.

## Current implementation

- **Management-unit sketching:** `pipeline/s3_management/sketch_management_units.py`
  processes Florida county-by-county and can create draft units from parcels, forest cover,
  roads, water, and BMP exclusions. A Union County smoke run has completed; segmentation,
  sliver merging, road-buffer policy, and terrain integration remain under review.
- **Prescription templates:** `pipeline/s4_fvs/regime_templates.py` renders FVS keyfiles for
  the five prescription families that make up the libraries, all built from the verified
  `ThinDBH` keyword. `pipeline/s3_management/regime_assignment.py` still picks a single
  default regime per unit; expanding it to emit an ownership-class *eligible set* is the
  next change (see the design note).
- **Harvest allocation:** `pipeline/s3_management/harvest_scheduler.py` is a greedy
  oldest-first allocator against TPO caps. It is retained as the annealer's initial solution
  and as a reported baseline; the simulated-annealing scheduler itself is not built yet.
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
config/                    Spatial, BMP, projection, and local data-path configuration
  prescriptions.yaml       Ownership class → eligible prescriptions + parameter grids
  projection.yaml          Projection, ownership, and scheduler/annealing settings
  tpo_targets.yaml         TPO harvest volume caps by county and owner group
data/                      Gitignored raw/interim/processed data products
docs/references/           The two guiding papers (LAMPS, Climate-FVS)
docs/superpowers/          Design specs and implementation plans
gee/                       Google Earth Engine export scripts
notebooks/                 Exploratory analyses and reusable notebook helpers
pipeline/
  s3_management/           Management units, ownership, regimes, harvest allocation
  s4_fvs/                  FVS input building, keyfile rendering, raster painting
research/mgmt_units/       Segmentation research, state, and next steps
scripts/                    Repository utility scripts
tests/                      Pytest suite
notes/                      Durable findings, decisions, run status, and open questions
PLAN.md                    Target v1 architecture and build sequence
artemis.txt                One-page architecture diagram
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
  automated FVS trajectory-generation pipeline. **No trajectory library has been generated
  and no simulated-annealing scheduler exists yet** — the documentation defines the target
  so implementation can be reviewed against it.
- The decision space is frozen when a library is built. A prescription that was not
  enumerated cannot be selected, so state-dependent silviculture must be expressed as FVS
  event-monitor logic inside a trajectory rather than as a scheduler decision.
- Simulated annealing gives no optimality guarantee. A plan is not a result until it is
  reported with its constraint-violation vector, its gap to the per-stand upper bound, the
  greedy and random baselines, and the objective spread across seeds.
- The v1 objective (NPV, volume, carbon, or a weighting) is undecided, and the tribal and
  unknown-ownership eligible sets are conservative placeholders pending a documented source.
- Carbon output stays disabled (`carbon_extension: false`). The measured corruption was a
  stop/restart artifact and library runs have no barriers, so re-enabling is now a scope
  decision rather than a blocked one.
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

Methodological references are tracked separately in
[`docs/references/README.md`](docs/references/README.md).

Dataset version pinning and a publication-ready data dictionary remain planned deliverables.
Version pinning must also cover the trajectory-library version, the scheduler seed, the
cooling schedule, and the objective weights — a plan is not reproducible without them.
