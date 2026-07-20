# LETO Initial-State Python Port Design

**Date:** 2026-07-19

## Goal

Create a reproducible, non-ArcPy Python implementation of the LETO steps that
convert management units and TreeMap/FIA inputs into FVS-ready initial-state
tables. Preserve the legacy workflow's observable behavior while making each
transformation independently testable and inspectable in a notebook.

This slice establishes the management-unit state that ARTEMIS can project
forward. It ends with LETO-compatible CSV tables; creating the FVS SQLite
database, running FVS, and painting projected outputs back to the map remain
later pipeline stages.

## Scope

### Included

- Port the `assign_plt_cn` portion of `scripts/LETO.V1.1.txt`:
  rasterize management-unit IDs on the TreeMap grid, count TreeMap cells by
  management unit and plot, and calculate `MU_PLT_CN_Weights.csv`.
- Port the data transformations in `scripts/LETO_CSV_PIPELINE.txt`:
  management-unit crosswalk construction, plot-weight filtering and
  normalization, multistate FIA tree joins, FVS species translation, live-tree
  filtering, weighted trees-per-acre calculation, nearest-runnable-unit
  imputation, and FVS stand/tree CSV output.
- Add a notebook that exposes the same stages and diagnostics without
  duplicating production logic.
- Add a behavioral-parity test that places the legacy LETO operation and new
  Python operation side by side on common synthetic inputs.

### Excluded

- Management-unit delineation, parcel clipping, ownership assignment, and SMZ
  construction from the rest of `LETO.V1.1.txt`; these belong to
  `pipeline/s3_management`.
- `Create_FVS_Database.txt`, FVS execution, and `Join_FVS_output_to_arc.txt`;
  these belong to later FVS/output stages.
- Changes to unrelated existing pipeline modules or uncommitted work on
  `main`.

## Inputs and outputs

### Required inputs

1. A GeoPandas-readable management-unit vector layer with:
   `MU_ID`, `Acres`, `OWN_CODE`, `OWN_TYPE`, `SMZ_Pct`, and geometry.
2. The TreeMap 2022 plot-ID raster.
3. A TreeMap lookup table mapping raster `VALUE` or `TM_ID` to `PLT_CN`.
4. One or more FIA `TREE.csv` files.
5. The FVS species-crosswalk workbook and sheet.

FIA control numbers are read and retained as strings throughout. The code will
accept paths and dataframes/geodataframes at focused boundaries so tests and
the notebook can exercise transformations without large production datasets.

### Outputs

- `MU_PLT_CN_Weights.csv`
- `MU_FVS_Crosswalk.csv`
- `FVS_StandInit.csv`
- `FVS_TreeInit.csv`
- `MU_FVS_Stands_No_Live_Trees.csv`

Column names needed by the legacy FVS database loader remain compatible with
LETO. Tree rows also retain `TREE_SOURCE`, `DONOR_STAND_ID`, and `NEAR_DIST` so
direct and imputed state can be distinguished.

## Architecture

### `pipeline/s1_initial_state/weights.py`

This module owns the raster-to-table bridge. It will:

1. Validate unique, non-null management-unit IDs and a projected CRS.
2. Read only the TreeMap window covering the management units.
3. Rasterize `MU_ID` into that exact TreeMap window using the TreeMap transform
   and shape.
4. Count valid `(MU_ID, TM_ID)` cells.
5. Join `TM_ID` to `PLT_CN`, calculate each plot's fraction of its management
   unit's valid TreeMap cells, and identify the majority plot for diagnostics.

Rasterio's default pixel-center rule is the portable non-ArcPy equivalent for
assigning non-overlapping polygons to the TreeMap grid. The parity fixture will
avoid ambiguous half-cell boundaries; the notebook will report units with no
valid TreeMap cells.

### `pipeline/s1_initial_state/leto_initial_state.py`

This module owns tabular initial-state construction. Focused functions will:

- build the management-unit crosswalk, deriving its majority `PLT_CN` from the
  generated plot-weight table;
- filter plots below `MIN_PLT_WEIGHT` and renormalize retained weights per
  management unit;
- join weighted plots to FIA trees and retain `STATUSCD == "1"`;
- translate FIA species codes to FVS Southern species codes;
- coerce FVS numeric fields, use `ACTUALHT` when `HT` is absent, and multiply
  `TPA_UNADJ` by the normalized plot weight;
- remove rows lacking stand, species, diameter, or positive tree count;
- construct direct stand and tree tables;
- find the nearest runnable management unit in projected coordinates and copy
  its tree list to an otherwise unrunnable unit, recording donor provenance;
- assemble and write the five output CSVs.

The top-level coordinator will return result dataframes and diagnostics before
writing them. This makes the notebook and tests use the same production code.

### Notebook

`notebooks/LETO_Initial_State_Walkthrough.ipynb` will contain:

1. Input-path configuration and preflight checks.
2. Management-unit and TreeMap alignment inspection.
3. Plot-weight construction and per-unit sum diagnostics.
4. FIA join coverage and unmatched-plot inspection.
5. Live-tree filtering and species-translation diagnostics.
6. Weighted-tree and donor-plot summaries.
7. Direct versus nearest-imputed management-unit mapping.
8. Optional output writing through the production coordinator.

The notebook will import package functions rather than carry a second
implementation. Its committed cells will have no large outputs or widget state.

## Legacy parity test

`tests/test_s1_leto_parity.py` will make the requested side-by-side comparison
explicit. For each operation, the test will identify the corresponding legacy
ArcPy/pandas expression and compare it with the new function on the same small
fixture:

| Legacy LETO operation | New Python operation | Compared result |
| --- | --- | --- |
| `PolygonToRaster` plus aligned TreeMap arrays | Rasterio rasterization plus windowed TreeMap read | Cell counts and plot weights |
| Weight threshold plus group normalization | `filter_and_normalize_weights` | Retained plots and unit weight sums |
| `weights.merge(TREE)` plus live-tree filter | FIA join function | Joined live tree identities |
| Species lookup and weighted `TPA_UNADJ` | Tree preparation function | FVS species and tree counts |
| `GenerateNearTable(CLOSEST)` plus donor copy | GeoPandas/Shapely nearest donor selection | Donor ID, distance, and copied tree list |

ArcPy cannot run in the project environment, so the legacy side is an
independent reference implementation of the documented equations and expected
tables, not an import of ArcPy. Fixtures will use unambiguous geometries and
deterministic tie-free nearest neighbors. This makes parity enforceable in CI
without weakening the comparison to static source-text checks.

## Validation and error behavior

The implementation will fail clearly for missing required columns, duplicate
management-unit IDs, geographic CRS input for distance imputation, a TreeMap
lookup that maps one raster ID to multiple plot IDs, a retained weight group
with a non-positive total, or no runnable donor unit. If filtering removes all
weighted plots for a unit, that unit follows LETO's normal missing-tree path and
is eligible for nearest-runnable-unit imputation.

Diagnostics will include row and unique-ID counts, unmatched TreeMap/FIA plots,
missing FVS species, per-unit weight sums, donor plots per unit, direct versus
imputed stand counts, and any units still missing trees.

## Verification

- Write tests first and observe failures before production implementation.
- Run targeted `s1_initial_state` tests during development.
- Run the full existing test suite from the worktree with an explicit pytest
  root directory.
- Parse every notebook code cell as Python and validate notebook structure with
  `nbformat`.
- Run Ruff on new Python modules and tests if Ruff is available in the locked
  environment; otherwise report that it was not run.
- Do not claim a production-data run because the large LETO inputs are not
  committed to this worktree.

## Durable documentation

Add a short `pipeline/s1_initial_state/README.md` documenting inputs, outputs,
the Python API, the notebook, the pixel-center rasterization rule, and the
parity test. Update `notes/notebooks.md` and `notes/README.md` only where needed
to index the new walkthrough and record the production-data verification gap.
