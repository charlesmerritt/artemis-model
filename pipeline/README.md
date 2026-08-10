# Pipeline

The committed pipeline currently contains three implemented slices of the larger workflow in
[`../PLAN.md`](../PLAN.md). Numbering follows the target architecture, so missing stage numbers
are planned work rather than missing directories.

## Implemented modules

| Module | Purpose | Maturity |
|---|---|---|
| `s3_management/sketch_management_units.py` | Build draft Florida management units by intersecting forested parcels with road, water, and BMP exclusions, processing one county at a time | Pilot; requires visual QA and policy decisions |
| `s4_fvs/paint_fvs_to_raster.py` | Join FVS stand trajectories through a TreeMap crosswalk and paint stand metrics onto TreeMap pixels | Five-county prototype; external inputs required |
| `s5_imagery/` | Pull NAIP over an extent vector with a real coverage check, cluster Earth Engine embeddings inside versus outside an area of interest, and publish both to the map viewer | Working; Earth Engine paths not covered by tests |

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

## Imagery and embeddings

```bash
# NAIP for an extent vector, one mosaic per year, coverage verified
uv run python -m pipeline.s5_imagery.naip_acquire \
  --extent config/study_extent.geojson --aoi config/stands.geojson \
  --years 2019,2021,2023

# Embeddings clustered inside vs outside the area of interest
uv run python -m pipeline.s5_imagery.embeddings \
  --extent config/study_extent.geojson --aoi config/stands.geojson --year 2024 --k 6

# Publish both to the map viewer, then open it
uv run python -m pipeline.s5_imagery.viewer_catalog \
  --naip-manifest data/interim/naip/stands/naip_manifest.json \
  --clusters data/interim/embeddings/stands/clusters.json
uv run python viewer/serve_viewer.py
```

This stage takes two vector layers on purpose: `--extent` is what imagery must cover, `--aoi` is
what the embeddings are about, and the ground between them is the control the clustering is
compared against. Requires Earth Engine authentication. See
[`s5_imagery/README.md`](s5_imagery/README.md) for coverage modes, outputs, and what the
separability statistic does and does not establish.

## Verification

```bash
uv run pytest tests/test_s3_sketch_management_units.py \
  tests/test_s4_paint_fvs_to_raster.py \
  tests/test_s5_vectors.py tests/test_s5_naip_acquire.py \
  tests/test_s5_embeddings.py tests/test_s5_viewer_catalog.py
```

The broader stages—initial-state assembly, site attributes, automated FVS execution, validation,
and product packaging—remain described in [`../PLAN.md`](../PLAN.md) until implemented.
