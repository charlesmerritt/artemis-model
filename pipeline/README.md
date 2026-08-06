# Pipeline

The committed pipeline contains implemented slices of the larger workflow in
[`../PLAN.md`](../PLAN.md). Numbering follows the target architecture, so missing stage numbers
are planned work rather than missing directories.

## Where these modules sit in the architecture

ARTEMIS builds a **library of candidate trajectories per stand**, its contents determined by
the stand's **ownership class**, then selects one trajectory per stand with **simulated
annealing** ([`../notes/trajectory-library-and-annealing.md`](../notes/trajectory-library-and-annealing.md)).
The modules below cover the front half — delineating stands, attributing them, and rendering
the keyfiles a library is generated from. The library generator and the annealing scheduler
are **not implemented yet**.

## Implemented modules

| Module | Purpose | Maturity |
|---|---|---|
| `s3_management/sketch_management_units.py` | Build draft Florida management units by intersecting forested parcels with road, water, and BMP exclusions, processing one county at a time | Pilot; requires visual QA and policy decisions |
| `s3_management/sliver_merge.py` | Dissolve sub-minimum-size polygons into their best neighbour, conserving area | Pilot |
| `s3_management/assign_plt_cn.py` | Assign TreeMap plots to units with area-share weights | Implemented |
| `s3_management/regime_assignment.py` | Deterministic single-regime default per unit | Implemented; to be joined by an ownership-class *eligible set* expander |
| `s3_management/tpo_targets.py` | Parse TPO harvest guidance into annual volume caps | Implemented |
| `s3_management/harvest_scheduler.py` | Greedy oldest-first allocation against TPO caps | Implemented; retained as the annealer's initial solution and reported baseline |
| `s4_fvs/build_fvs_inputs.py` | Build per-unit FVS tree lists as the area-weighted union of constituent plots | Implemented |
| `s4_fvs/regime_templates.py` | Render FVS keyfiles for the five prescription families | Implemented; `ThinDBH`-only by design |
| `s4_fvs/paint_fvs_to_raster.py` | Join FVS stand trajectories through a TreeMap crosswalk and paint stand metrics onto TreeMap pixels | Five-county prototype; external inputs required |

## Not implemented

| Planned module | Purpose | Plan reference |
|---|---|---|
| Ownership-class eligible-set expander | `stand_id → [prescription_id]` from `config/prescriptions.yaml`, with the riparian override and eligibility screens | `PLAN.md` §3c, pipeline plan Step 3.2 |
| Trajectory library generator | Run FVS once per `(stand, prescription)`, barrier-free and parallel; load to DuckDB | `PLAN.md` §4c, Step 4.2 |
| Simulated-annealing scheduler | Select one trajectory per stand under priced and structural constraints; emit the quality report | `PLAN.md` §4d, Steps 4.3–4.4 |

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

# The ownership -> eligible prescription mapping and the annealing settings
uv run pytest tests/test_config.py
```

The broader stages—initial-state assembly, site attributes, library generation, scheduling,
validation, and product packaging—remain described in [`../PLAN.md`](../PLAN.md) until
implemented.
