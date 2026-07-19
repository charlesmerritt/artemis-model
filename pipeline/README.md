# Pipeline

The committed pipeline currently contains two implemented slices of the larger workflow in
[`../PLAN.md`](../PLAN.md). Numbering follows the target architecture, so missing stage numbers
are planned work rather than missing directories.

## Implemented modules

| Module | Purpose | Maturity |
|---|---|---|
| `s3_management/sketch_management_units.py` | Build draft Florida management units by intersecting forested parcels with road, water, and BMP exclusions, processing one county at a time | Pilot; requires visual QA and policy decisions |
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
