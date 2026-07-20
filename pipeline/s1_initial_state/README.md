# LETO initial-state pipeline

This package replaces the initial-state portion of the ArcGIS LETO prototype
with reproducible Python. It covers two legacy operations:

1. `LETO.V1.1.txt:assign_plt_cn` — management units × TreeMap cells → FIA plot
   weights.
2. `LETO_CSV_PIPELINE.txt` — plot weights + multistate FIA trees → FVS stand
   and tree initialization tables.

Management-unit delineation remains in `pipeline/s3_management`. Creating the
FVS SQLite database, running FVS, and painting outputs back to the map are not
part of this package.

## Inputs

- A GeoPandas-readable management-unit layer with unique `MU_ID`, `Acres`,
  `OWN_CODE`, `OWN_TYPE`, `SMZ_Pct`, geometry, and a projected CRS.
- TreeMap 2022 plot-ID GeoTIFF.
- TreeMap lookup CSV containing raster `VALUE` (or a caller-selected equivalent)
  and `PLT_CN`.
- One or more state FIA `TREE.csv` files.
- USFS FVS species-crosswalk workbook and the `EasternSpeciesTranslator` sheet.

FIA control numbers are read and retained as strings. TreeMap rasterization
uses the native TreeMap transform and Rasterio's pixel-center rule. This is the
portable equivalent for non-overlapping management units, but edge cells can
differ from ArcPy's `PolygonToRaster(..., cell_assignment="MAXIMUM_AREA")` when
a polygon boundary crosses a cell away from its center.

## Python interface

Run the complete file-based workflow:

```python
from pipeline.s1_initial_state.leto_initial_state import run_leto_initial_state

tables = run_leto_initial_state(
    management_units_path="data/interim/management_units.gpkg",
    management_units_layer="management_units",
    treemap_path="data/interim/treemap_2022_fl.tif",
    treemap_lookup_path="data/interim/treemap_tmids_fl.csv",
    species_crosswalk_path="data/raw/FVS_SpeciesCrosswalk.xls",
    species_crosswalk_sheet="EasternSpeciesTranslator",
    fia_tree_paths=[
        "data/raw/fia/FL_TREE.csv",
        "data/raw/fia/GA_TREE.csv",
        "data/raw/fia/AL_TREE.csv",
        "data/raw/fia/SC_TREE.csv",
    ],
    output_dir="data/interim/fvs/leto_initial_state",
)
```

For inspection or custom orchestration, use
`weights.build_plot_weights(...)` and the focused functions in
`leto_initial_state.py`. The walkthrough notebook calls these functions one
stage at a time. The returned `tables.diagnostics` series reports weight sums,
donor counts, unmatched FIA plots, missing FVS species, and direct/imputed stand
counts before any CSVs are written.

## Outputs

| File | Contents |
| --- | --- |
| `MU_PLT_CN_Weights.csv` | Raw TreeMap cell counts and plot weights per management unit |
| `MU_FVS_Crosswalk.csv` | Management-unit attributes and majority donor plot |
| `FVS_StandInit.csv` | One FVS Southern stand row per runnable management unit |
| `FVS_TreeInit.csv` | Weighted live FIA tree rows, including imputed rows |
| `MU_FVS_Stands_No_Live_Trees.csv` | Units still missing trees after imputation |

The tree table records `TREE_SOURCE`, `DONOR_STAND_ID`, and `NEAR_DIST`.
Nearest imputation uses polygon-to-polygon distance in the management units'
projected CRS, matching LETO's `GenerateNearTable(..., closest="CLOSEST")`
semantics.

## Walkthrough and verification

Open `notebooks/LETO_Initial_State_Walkthrough.ipynb` to inspect alignment,
weights, FIA join coverage, species translation, donor imputation, and the
initial-state map. Output writing is disabled until `WRITE_OUTPUTS = True`.

The parity test names the legacy operation beside the new operation and checks
both against shared, deterministic fixtures:

| Legacy LETO | Python port | Compared behavior |
| --- | --- | --- |
| `PolygonToRaster` + raster arrays | `build_plot_weights` | Cell counts and plot weights |
| weight threshold + group normalization | `filter_and_normalize_weights` | Donor retention and sums |
| FIA merge + species/TPA expressions | `prepare_direct_tree_rows` | Live trees and FVS fields |
| `GenerateNearTable` + donor copy | `impute_missing_tree_rows` | Donor, distance, and copied tree list |

Run focused verification with:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_weights.py tests/test_s1_leto_initial_state.py \
  tests/test_s1_leto_parity.py tests/test_s1_leto_notebook.py
```

The committed tests use synthetic data. A production-scale run still requires
the local LETO management units, TreeMap raster/table, FIA trees, and species
workbook.
