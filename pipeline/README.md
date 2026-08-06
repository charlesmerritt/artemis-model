# Pipeline

The committed pipeline currently contains two implemented slices of the larger workflow in
[`../PLAN.md`](../PLAN.md). Numbering follows the target architecture, so missing stage numbers
are planned work rather than missing directories.

## Implemented modules

| Module | Purpose | Maturity |
|---|---|---|
| `s3_management/sketch_management_units.py` | Build draft Florida forest units by intersecting parcels with the LANDFIRE forest mask, then partitioning into `managed` and grow-only `riparian` units, processing one county at a time | Pilot; requires visual QA and policy decisions |
| `s4_fvs/paint_fvs_to_raster.py` | Join FVS stand trajectories through a TreeMap crosswalk and paint stand metrics onto TreeMap pixels | Five-county prototype; external inputs required |

## Management-unit sketch

```bash
# Validate paths and enumerate work without writing
uv run python -m pipeline.s3_management.sketch_management_units \
  --pilot-five-county --dry-run

# Run one county with QA layers
uv run python -m pipeline.s3_management.sketch_management_units \
  --county-fips 125 --save-qa --overwrite

# Show every option
uv run python -m pipeline.s3_management.sketch_management_units --help
```

Inputs are configured in [`../config/data_paths.yaml`](../config/data_paths.yaml). Outputs default
to `data/interim/management_units/`. Before statewide use, review
[`../notes/management_units.md`](../notes/management_units.md) for the latest pilot results and
open decisions.

Each county directory holds:

| File | Contents |
|---|---|
| `candidate_management_units.gpkg` | Both unit classes in one layer, keyed by `unit_id` (`mu_*` managed, `rb_*` riparian) with `unit_class` and `buffer_class` |
| `summary.csv` | Polygon count and area by `unit_class` × `buffer_class` × `size_class` |
| `area_accounting.csv` | The area balance, including the permanently excluded acres as their own lines |

Riparian units are the forested part of a Florida BMP stream buffer. They grow freely, are never
harvested, and keep their own polygon identity — they are not dissolved into neighbouring managed
units. Only NHD waterbodies and the small road-artifact buffer are erased outright; those acres
appear on the `excluded_*` lines of `area_accounting.csv` so the drop stays visible. The two unit
classes partition what remains:

    Σ managed + Σ riparian == (forest mask ∩ parcels) − (waterbodies ∪ road buffer)

`area_accounting.csv` reports `balance_residual` for that identity. If it drifts beyond a relative
1e-6, forest has been lost somewhere between the exclusion step and the written units: the run logs
an error naming the county and **exits non-zero**, so a batch run cannot report success while
shipping wrong acreage. Treat a failing county's outputs as wrong rather than approximate.

## FVS raster painting

```bash
uv run python -m pipeline.s4_fvs.paint_fvs_to_raster
```

The painter currently uses constants in the module rather than command-line options. It expects:

- `data/interim/no_management_fl5co_fvs_output/fvs_trajectory.csv`
- matching TreeMap crosswalk and raster files under `/mnt/d/TreeMap_Chaz`

It reports candidate-pair coverage, selects the best matching TreeMap vintage, and writes initial
and final basal-area rasters to `data/processed/no_management_fl5co_rasters/`. Read
[`../notes/fvs-to-raster-painting.md`](../notes/fvs-to-raster-painting.md) before changing
snapshots or metrics.

## Verification

```bash
uv run pytest tests/test_s3_sketch_management_units.py \
  tests/test_s4_paint_fvs_to_raster.py
```

The broader stages—initial-state assembly, site attributes, automated FVS execution, validation,
and product packaging—remain described in [`../PLAN.md`](../PLAN.md) until implemented.
