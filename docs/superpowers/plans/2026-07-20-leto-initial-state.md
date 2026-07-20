# LETO Initial-State Python Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a non-ArcPy, tested Python pipeline that creates management-unit-to-FIA plot weights and LETO-compatible FVS initial-state tables, plus an inspectable walkthrough notebook.

**Architecture:** `weights.py` owns the spatial TreeMap-grid overlay and plot-weight table. `leto_initial_state.py` owns the tabular FIA/FVS transformations, nearest-donor imputation, orchestration, and output writing. Tests independently encode the legacy LETO equations and copy semantics so the new implementation is checked side by side without requiring ArcPy.

**Tech Stack:** Python 3.14, pandas, GeoPandas, Rasterio, Shapely, openpyxl, pytest, nbformat, Jupyter.

## Global Constraints

- Work only in `.claude/worktrees/leto-initial-state` on branch `codex/leto-initial-state`.
- Do not use or import ArcPy.
- Preserve `MU_ID`, `PLT_CN`, FIA `CN`, and related control numbers as strings outside the temporary integer raster codes needed by Rasterio.
- Use TreeMap 2022 and its native grid; read only the management-unit covering window.
- Use the portable pixel-center rasterization rule and document its edge-cell difference from ArcPy `MAXIMUM_AREA`.
- Preserve LETO output column names and provenance fields.
- A unit with no retained/runnable trees must enter nearest-runnable-unit imputation; it is not an early normalization error.
- Do not add management-unit delineation, FVS database creation/execution, or output painting.
- Production code must follow red-green-refactor: no function is added before its failing test is observed.

---

### Task 1: TreeMap-grid plot weights

**Files:**
- Create: `pipeline/s1_initial_state/__init__.py`
- Create: `pipeline/s1_initial_state/weights.py`
- Create: `tests/test_s1_leto_parity.py`
- Create: `tests/test_s1_leto_weights.py`

**Interfaces:**
- Consumes: a `geopandas.GeoDataFrame` with `MU_ID` and geometry; a TreeMap GeoTIFF path; a `pandas.DataFrame` with `VALUE` and `PLT_CN`.
- Produces: `build_plot_weights(management_units, treemap_path, treemap_lookup, *, lookup_value_column="VALUE") -> pandas.DataFrame` with columns `MU_ID`, `TM_VALUE`, `CELL_COUNT`, `TOTAL_CELLS`, `WEIGHT`, `PLT_CN`.

- [ ] **Step 1: Write the failing two-unit spatial parity test**

Create a 4-by-2 integer GeoTIFF in `tmp_path` with transform `from_origin(0, 60, 30, 30)`, values `[[10, 10, 20, 20], [10, 30, 20, 20]]`, and nodata `-9999`. Create two non-overlapping polygons covering the left and right halves and a lookup `VALUE=[10,20,30]`, `PLT_CN=["10000000000001", "10000000000002", "10000000000003"]`.

The parity assertion in `tests/test_s1_leto_parity.py` must encode the legacy `groupby(["MU_ID", "TM_VALUE"]).size()` and `CELL_COUNT / TOTAL_CELLS` results explicitly:

```python
expected = pd.DataFrame(
    {
        "MU_ID": ["1", "1", "2"],
        "TM_VALUE": [10, 30, 20],
        "CELL_COUNT": [3, 1, 4],
        "TOTAL_CELLS": [4, 4, 4],
        "WEIGHT": [0.75, 0.25, 1.0],
        "PLT_CN": ["10000000000001", "10000000000003", "10000000000002"],
    }
)
```

Also add `tests/test_s1_leto_weights.py::test_build_plot_weights_rejects_ambiguous_treemap_lookup`, where one `VALUE` maps to two `PLT_CN`s.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_parity.py::test_plot_weights_match_leto_assign_plt_cn \
  tests/test_s1_leto_weights.py::test_build_plot_weights_rejects_ambiguous_treemap_lookup -v
```

Expected: collection fails with `ModuleNotFoundError: pipeline.s1_initial_state`.

- [ ] **Step 3: Implement the minimal windowed Rasterio overlay**

In `weights.py`, implement these focused helpers and public function:

```python
WEIGHT_COLUMNS = [
    "MU_ID", "TM_VALUE", "CELL_COUNT", "TOTAL_CELLS", "WEIGHT", "PLT_CN"
]


def _normalized_lookup(lookup: pd.DataFrame, value_column: str) -> pd.DataFrame:
    required = {value_column, "PLT_CN"}
    missing = required.difference(lookup.columns)
    if missing:
        raise ValueError(f"TreeMap lookup missing columns: {sorted(missing)}")
    result = lookup[[value_column, "PLT_CN"]].rename(columns={value_column: "TM_VALUE"}).copy()
    result["PLT_CN"] = result["PLT_CN"].astype("string")
    if result.groupby("TM_VALUE")["PLT_CN"].nunique().gt(1).any():
        raise ValueError("TreeMap lookup maps one raster value to multiple PLT_CNs")
    return result.drop_duplicates("TM_VALUE")


def build_plot_weights(
    management_units: gpd.GeoDataFrame,
    treemap_path: Path | str,
    treemap_lookup: pd.DataFrame,
    *,
    lookup_value_column: str = "VALUE",
) -> pd.DataFrame:
    if "MU_ID" not in management_units:
        raise ValueError("Management units missing column: MU_ID")
    if management_units.crs is None:
        raise ValueError("Management units must define a CRS")
    units = management_units.copy()
    units["MU_ID"] = units["MU_ID"].astype("string")
    if units["MU_ID"].isna().any() or units["MU_ID"].duplicated().any():
        raise ValueError("MU_ID values must be non-null and unique")
    lookup = _normalized_lookup(treemap_lookup, lookup_value_column)

    with rasterio.open(treemap_path) as source:
        units = units.to_crs(source.crs)
        window = geometry_window(source, units.geometry)
        transform = source.window_transform(window)
        code_by_mu = {mu_id: code for code, mu_id in enumerate(units["MU_ID"], 1)}
        mu_by_code = {code: mu_id for mu_id, code in code_by_mu.items()}
        mu_grid = rasterize(
            ((geometry, code_by_mu[mu_id]) for mu_id, geometry in zip(units["MU_ID"], units.geometry)),
            out_shape=(int(window.height), int(window.width)),
            transform=transform,
            fill=0,
            all_touched=False,
            dtype="int32",
        )
        treemap = source.read(1, window=window, masked=True)

    valid = (mu_grid > 0) & ~np.ma.getmaskarray(treemap)
    if not valid.any():
        raise ValueError("Management units overlap no valid TreeMap cells")
    cells = pd.DataFrame({"MU_CODE": mu_grid[valid], "TM_VALUE": treemap.data[valid]})
    counts = cells.groupby(["MU_CODE", "TM_VALUE"]).size().reset_index(name="CELL_COUNT")
    counts["MU_ID"] = counts["MU_CODE"].map(mu_by_code).astype("string")
    counts["TOTAL_CELLS"] = counts.groupby("MU_ID")["CELL_COUNT"].transform("sum")
    counts["WEIGHT"] = counts["CELL_COUNT"] / counts["TOTAL_CELLS"]
    result = counts.merge(lookup, on="TM_VALUE", how="inner")
    return result[WEIGHT_COLUMNS].sort_values(
        ["MU_ID", "WEIGHT", "TM_VALUE"], ascending=[True, False, True]
    ).reset_index(drop=True)
```

Keep the implementation within these requirements; do not add chunking, CLI arguments, or alternate rasterization modes.

- [ ] **Step 4: Run targeted tests and verify GREEN**

Run the command from Step 2. Expected: both tests pass.

- [ ] **Step 5: Add focused validation tests**

Add tests for duplicate `MU_ID`, missing CRS, management units outside TreeMap bounds, and lookup rows missing `PLT_CN`. Each must assert the exact `ValueError` message fragment.

- [ ] **Step 6: Run Task 1 tests**

Run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_weights.py tests/test_s1_leto_parity.py -v
```

Expected: all Task 1 tests pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add pipeline/s1_initial_state/__init__.py pipeline/s1_initial_state/weights.py \
  tests/test_s1_leto_weights.py tests/test_s1_leto_parity.py
git commit -m "feat(s1): build LETO plot weights without ArcPy"
```

---

### Task 2: LETO tabular transformations

**Files:**
- Create: `pipeline/s1_initial_state/leto_initial_state.py`
- Create: `tests/test_s1_leto_initial_state.py`
- Modify: `tests/test_s1_leto_parity.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: management-unit attributes, Task 1 weights, multistate FIA tree rows, and an FIA-to-FVS species mapping.
- Produces:
  - `build_management_unit_crosswalk(management_units, weights) -> pd.DataFrame`
  - `filter_and_normalize_weights(weights, crosswalk, min_plot_weight=0.05) -> pd.DataFrame`
  - `load_species_lookup(path, sheet_name) -> dict[str, str]`
  - `load_fia_tree_files(paths) -> pd.DataFrame`
  - `prepare_direct_tree_rows(normalized_weights, fia_trees, species_lookup) -> pd.DataFrame`

- [ ] **Step 1: Write failing crosswalk and normalization tests**

Use two management units with attributes and weights where unit 1 has weights `0.80`, `0.15`, `0.05`, and unit 2 has `0.96`, `0.04`. Assert:

```python
assert crosswalk.loc[crosswalk["MU_ID"] == "1", "PLT_CN"].item() == "101"
assert normalized.query("MU_ID == '1'")["WEIGHT"].tolist() == pytest.approx([0.80, 0.15, 0.05])
assert normalized.query("MU_ID == '2'")["WEIGHT"].tolist() == pytest.approx([1.0])
```

The `0.05` donor is retained because LETO uses `>= MIN_PLT_WEIGHT`; the `0.04` donor is removed. Assert the merged rows carry `Stand_ID`, `Acres`, `OWN_CODE`, `OWN_TYPE`, and `SMZ_Pct`.

- [ ] **Step 2: Write failing FIA/species/tree-preparation parity test**

Extend `tests/test_s1_leto_parity.py` with FIA rows covering a live tree, a dead tree, an unmapped species, missing `HT` with available `ACTUALHT`, and two donor weights. Compare against an explicit legacy result and assert:

```python
assert direct.loc[0, "STAND_ID"] == "MU_1"
assert direct.loc[0, "SPECIES"] == "LP"
assert direct.loc[0, "TREE_COUNT"] == pytest.approx(4.0)  # TPA_UNADJ 5.0 * WEIGHT 0.8
assert direct.loc[0, "HT"] == pytest.approx(55.0)         # fallback from ACTUALHT
assert direct.loc[0, "TREE_SOURCE"] == "FIA_WEIGHTED_DIRECT"
assert direct.loc[0, "DONOR_STAND_ID"] == ""
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_initial_state.py tests/test_s1_leto_parity.py -v
```

Expected: import fails because `leto_initial_state.py` does not exist.

- [ ] **Step 4: Implement crosswalk and normalized-weight functions**

Define required column constants and implement:

```python
def build_management_unit_crosswalk(
    management_units: pd.DataFrame,
    weights: pd.DataFrame,
) -> pd.DataFrame:
    required = {"MU_ID", "Acres", "OWN_CODE", "OWN_TYPE", "SMZ_Pct"}
    missing = required.difference(management_units.columns)
    if missing:
        raise ValueError(f"Management units missing columns: {sorted(missing)}")
    units = management_units[list(required)].copy()
    units["MU_ID"] = units["MU_ID"].astype("string")
    majority = (
        weights.assign(MU_ID=weights["MU_ID"].astype("string"), PLT_CN=weights["PLT_CN"].astype("string"))
        .sort_values(["MU_ID", "CELL_COUNT", "PLT_CN"], ascending=[True, False, True])
        .drop_duplicates("MU_ID")[["MU_ID", "PLT_CN"]]
    )
    result = units.merge(majority, on="MU_ID", how="left")
    result.insert(0, "Stand_ID", result["MU_ID"])
    return result[["Stand_ID", "MU_ID", "Acres", "PLT_CN", "OWN_CODE", "OWN_TYPE", "SMZ_Pct"]]


def filter_and_normalize_weights(
    weights: pd.DataFrame,
    crosswalk: pd.DataFrame,
    min_plot_weight: float = 0.05,
) -> pd.DataFrame:
    retained = weights.assign(
        MU_ID=weights["MU_ID"].astype("string"),
        PLT_CN=weights["PLT_CN"].astype("string"),
        WEIGHT=pd.to_numeric(weights["WEIGHT"], errors="coerce"),
    ).loc[lambda frame: frame["WEIGHT"] >= min_plot_weight].copy()
    totals = retained.groupby("MU_ID")["WEIGHT"].transform("sum")
    if totals.le(0).any():
        raise ValueError("Retained plot weights must have positive totals")
    retained["WEIGHT"] = retained["WEIGHT"] / totals
    attributes = crosswalk[["MU_ID", "Stand_ID", "Acres", "OWN_CODE", "OWN_TYPE", "SMZ_Pct"]]
    return retained.merge(attributes, on="MU_ID", how="left", validate="many_to_one")
```

- [ ] **Step 5: Implement FIA readers and direct-tree preparation**

Implement only the legacy transformations:

```python
TREE_RENAME = {
    "CN": "TREE_ID",
    "INVYR": "INV_YEAR",
    "SPCD": "Species_FIA",
    "DIA": "DIAMETER",
    "CR": "CRRATIO",
    "TPA_UNADJ": "TREE_COUNT",
}

TREE_OUTPUT_COLUMNS = [
    "STAND_ID",
    "TREE_ID",
    "SPECIES",
    "DIAMETER",
    "HT",
    "CRRATIO",
    "TREE_COUNT",
    "MU_ID",
    "PLT_CN",
    "WEIGHT",
    "Species_FIA",
    "TREE_SOURCE",
    "DONOR_STAND_ID",
    "NEAR_DIST",
]


def load_species_lookup(path: Path | str, sheet_name: str) -> dict[str, str]:
    frame = pd.read_excel(path, sheet_name=sheet_name, dtype=str)
    required = {"FIA CODE", "SN_Mapped_To"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"Species crosswalk missing columns: {sorted(missing)}")
    frame["FIA_CODE_CLEAN"] = (
        pd.to_numeric(frame["FIA CODE"], errors="coerce").astype("Int64").astype("string").str.zfill(3)
    )
    if frame.groupby("FIA_CODE_CLEAN")["SN_Mapped_To"].nunique().gt(1).any():
        raise ValueError("Species crosswalk maps one FIA code to multiple FVS species")
    return dict(zip(frame["FIA_CODE_CLEAN"], frame["SN_Mapped_To"]))


def load_fia_tree_files(paths: Sequence[Path | str]) -> pd.DataFrame:
    if not paths:
        raise ValueError("At least one FIA TREE.csv path is required")
    return pd.concat([pd.read_csv(path, dtype=str) for path in paths], ignore_index=True)


def prepare_direct_tree_rows(
    normalized_weights: pd.DataFrame,
    fia_trees: pd.DataFrame,
    species_lookup: Mapping[str, str],
) -> pd.DataFrame:
    trees = fia_trees.copy()
    trees["PLT_CN"] = trees["PLT_CN"].astype("string")
    joined = normalized_weights.merge(trees, on="PLT_CN", how="inner").loc[lambda frame: frame["STATUSCD"] == "1"]
    joined = joined.rename(columns=TREE_RENAME)
    joined["Species_FIA_CLEAN"] = (
        pd.to_numeric(joined["Species_FIA"], errors="coerce").astype("Int64").astype("string").str.zfill(3)
    )
    joined["SPECIES"] = joined["Species_FIA_CLEAN"].map(species_lookup)
    joined["STAND_ID"] = "MU_" + joined["Stand_ID"].astype("string")
    for column in ["DIAMETER", "HT", "ACTUALHT", "CRRATIO", "TREE_COUNT", "WEIGHT"]:
        if column in joined:
            joined[column] = pd.to_numeric(joined[column], errors="coerce")
    if "HT" in joined and "ACTUALHT" in joined:
        joined["HT"] = joined["HT"].fillna(joined["ACTUALHT"])
    joined["TREE_COUNT"] = joined["TREE_COUNT"] * joined["WEIGHT"]
    joined = joined.dropna(subset=["STAND_ID", "SPECIES", "DIAMETER", "TREE_COUNT"])
    joined = joined.loc[joined["TREE_COUNT"] > 0].copy()
    joined["TREE_SOURCE"] = "FIA_WEIGHTED_DIRECT"
    joined["DONOR_STAND_ID"] = ""
    joined["NEAR_DIST"] = ""
    return joined[TREE_OUTPUT_COLUMNS]
```

- [ ] **Step 6: Add the Excel engine from a failing workbook read**

Create a temporary `.xlsx` in the species-loader test. Run it once and confirm pandas reports the missing optional `openpyxl` dependency, then run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv add openpyxl
```

Re-run the species-loader test. Expected: pass and `pyproject.toml`/`uv.lock` include openpyxl.

- [ ] **Step 7: Run Task 2 tests**

Run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_initial_state.py tests/test_s1_leto_parity.py -v
```

Expected: all Task 1 and Task 2 parity assertions pass.

- [ ] **Step 8: Commit Task 2**

```bash
git add pipeline/s1_initial_state/leto_initial_state.py \
  tests/test_s1_leto_initial_state.py tests/test_s1_leto_parity.py \
  pyproject.toml uv.lock
git commit -m "feat(s1): prepare LETO FVS initial-state rows"
```

---

### Task 3: Nearest-donor imputation and output orchestration

**Files:**
- Modify: `pipeline/s1_initial_state/leto_initial_state.py`
- Modify: `tests/test_s1_leto_initial_state.py`
- Modify: `tests/test_s1_leto_parity.py`

**Interfaces:**
- Consumes: Task 2 crosswalk, normalized weights, direct trees, and projected management-unit geometry.
- Produces:
  - `impute_missing_tree_rows(management_units, crosswalk, direct_trees) -> pd.DataFrame`
  - `build_stand_rows(crosswalk, trees, *, inventory_year=2022, variant="SN", state="FL") -> pd.DataFrame`
  - `InitialStateTables` frozen dataclass with `crosswalk`, `weights`, `stands`, `trees`, and `missing_stands` dataframes.
  - `build_initial_state(...) -> InitialStateTables`
  - `write_initial_state(tables, output_dir) -> dict[str, Path]`
  - `run_leto_initial_state(...) -> InitialStateTables`

- [ ] **Step 1: Write the failing nearest-donor parity test**

Create three projected square management units: unit 1 runnable at x=0, unit 2 missing at x=100, and unit 3 runnable at x=500. Give unit 1 two direct trees and unit 3 one. Assert LETO `GenerateNearTable(... closest="CLOSEST")` semantics:

```python
imputed = result.query("STAND_ID == 'MU_2'")
assert imputed["DONOR_STAND_ID"].unique().tolist() == ["MU_1"]
assert imputed["TREE_SOURCE"].unique().tolist() == ["IMPUTED_NEAREST"]
assert imputed["TREE_ID"].tolist() == [1, 2]
assert imputed["NEAR_DIST"].unique().tolist() == pytest.approx([90.0])
```

Use polygon-to-polygon distance, not centroid distance. Add error tests for geographic CRS and no runnable donor.

- [ ] **Step 2: Run imputation tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_parity.py::test_nearest_imputation_matches_leto_generate_near_table \
  tests/test_s1_leto_initial_state.py -v
```

Expected: fail because `impute_missing_tree_rows` is not defined.

- [ ] **Step 3: Implement nearest geometry imputation**

Use Shapely's `STRtree.query_nearest(..., all_matches=False, return_distance=True)` against runnable unit geometries. Build explicit maps between STRtree positions and string `MU_ID`s. For every missing unit:

```python
donor_trees = direct_trees.loc[direct_trees["STAND_ID"] == f"MU_{donor_mu_id}"].copy()
donor_trees["DONOR_STAND_ID"] = f"MU_{donor_mu_id}"
donor_trees["TREE_SOURCE"] = "IMPUTED_NEAREST"
donor_trees["NEAR_DIST"] = distance
donor_trees["STAND_ID"] = f"MU_{missing_mu_id}"
donor_trees["MU_ID"] = missing_mu_id
donor_trees["TREE_ID"] = range(1, len(donor_trees) + 1)
```

Concatenate direct and imputed rows without changing the donor `PLT_CN` or weighted tree attributes.

- [ ] **Step 4: Write failing stand/coordinator/output tests**

Assert one stand row per tree-bearing stand, defaults `INV_YEAR=2022`, `VARIANT="SN"`, `STATE="FL"`, missing units after imputation are reported, and output filenames exactly match the spec. Use `tmp_path` and read every written CSV back with identifier columns as strings.

- [ ] **Step 5: Implement dataclass, stand rows, coordinator, and writer**

Implement:

```python
@dataclass(frozen=True)
class InitialStateTables:
    crosswalk: pd.DataFrame
    weights: pd.DataFrame
    stands: pd.DataFrame
    trees: pd.DataFrame
    missing_stands: pd.DataFrame


OUTPUT_NAMES = {
    "crosswalk": "MU_FVS_Crosswalk.csv",
    "weights": "MU_PLT_CN_Weights.csv",
    "stands": "FVS_StandInit.csv",
    "trees": "FVS_TreeInit.csv",
    "missing_stands": "MU_FVS_Stands_No_Live_Trees.csv",
}
```

`run_leto_initial_state` must read the vector and tabular inputs, call Task 1 weight creation, call `build_initial_state`, write outputs, and return the same tables. It may accept an optional vector `layer`; do not add a CLI.

- [ ] **Step 6: Run all s1 tests**

Run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_weights.py tests/test_s1_leto_initial_state.py \
  tests/test_s1_leto_parity.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add pipeline/s1_initial_state/leto_initial_state.py \
  tests/test_s1_leto_initial_state.py tests/test_s1_leto_parity.py
git commit -m "feat(s1): impute and export LETO initial state"
```

---

### Task 4: Walkthrough notebook and durable documentation

**Files:**
- Create: `notebooks/LETO_Initial_State_Walkthrough.ipynb`
- Create: `tests/test_s1_leto_notebook.py`
- Create: `pipeline/s1_initial_state/README.md`
- Modify: `notes/notebooks.md`
- Modify: `notes/README.md`

**Interfaces:**
- Consumes: public functions from Tasks 1-3 and user-configured real-data paths.
- Produces: a no-output notebook that exposes each LETO stage, diagnostics, and optional writes.

- [ ] **Step 1: Write the failing notebook contract test**

Load the expected notebook with `nbformat`, call `nbformat.validate`, parse every code cell with `ast.parse`, and assert the markdown heading sequence contains:

```python
required_sections = [
    "Inputs and preflight",
    "Management units and TreeMap alignment",
    "MU x PLT_CN weights",
    "FIA join coverage",
    "Species and live-tree preparation",
    "Nearest-runnable-unit imputation",
    "Initial-state map",
    "Write LETO-compatible outputs",
]
```

Also assert the notebook imports `pipeline.s1_initial_state` functions and has no cell outputs or widget metadata.

- [ ] **Step 2: Run the notebook test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_notebook.py -v
```

Expected: fail because the notebook does not exist.

- [ ] **Step 3: Create the minimal walkthrough notebook**

Create the notebook with `nbformat`-compatible JSON using these code-cell responsibilities:

```python
from pathlib import Path
import geopandas as gpd
import pandas as pd
from pipeline.s1_initial_state.weights import build_plot_weights
from pipeline.s1_initial_state.leto_initial_state import (
    build_initial_state,
    build_management_unit_crosswalk,
    filter_and_normalize_weights,
    load_fia_tree_files,
    load_species_lookup,
    prepare_direct_tree_rows,
    write_initial_state,
)
```

Use one configuration cell for paths and `WRITE_OUTPUTS = False`. Each later cell calls production functions and displays compact tables/counts. The map cell plots `management_units` colored by direct/imputed/missing status. The final cell writes only when `WRITE_OUTPUTS` is true. Commit no execution outputs.

- [ ] **Step 4: Write focused package documentation**

Document required columns, example Python calls, all five outputs, TreeMap pixel-center rasterization, string identifiers, nearest-donor provenance, the parity-test command, notebook path, and the production-data gap in `pipeline/s1_initial_state/README.md`.

Add one indexed entry to `notes/notebooks.md` and `notes/README.md`; do not rewrite unrelated notebook status text.

- [ ] **Step 5: Run notebook and documentation checks**

Run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_notebook.py -v
git diff --check
```

Expected: notebook test passes and `git diff --check` produces no output.

- [ ] **Step 6: Commit Task 4**

```bash
git add notebooks/LETO_Initial_State_Walkthrough.ipynb \
  tests/test_s1_leto_notebook.py pipeline/s1_initial_state/README.md \
  notes/notebooks.md notes/README.md
git commit -m "docs(s1): add LETO initial-state walkthrough"
```

---

### Task 5: Full verification and loop closure

**Files:**
- Modify if needed: only files created or changed in Tasks 1-4.

**Interfaces:**
- Consumes: complete implementation.
- Produces: clean verification evidence and no unrelated changes.

- [ ] **Step 1: Run targeted tests**

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. \
  tests/test_s1_leto_weights.py tests/test_s1_leto_initial_state.py \
  tests/test_s1_leto_parity.py tests/test_s1_leto_notebook.py -v
```

Expected: all targeted tests pass.

- [ ] **Step 2: Run the full test suite**

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/
```

Expected baseline: at least the original 57 tests pass and 17 data-dependent tests skip, plus all new tests pass.

- [ ] **Step 3: Run formatting and lint checks**

First check whether Ruff is available:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run ruff --version
```

If available, run:

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run ruff format --check \
  pipeline/s1_initial_state tests/test_s1_leto_*.py
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run ruff check \
  pipeline/s1_initial_state tests/test_s1_leto_*.py
```

If unavailable, record `Not run: Ruff is not configured in pyproject.toml or uv.lock` in the final report rather than downloading another tool.

- [ ] **Step 4: Inspect branch scope**

```bash
git status --short --branch
git diff --check HEAD~4..HEAD
git diff --stat main...HEAD
git log --oneline --decorate main..HEAD
```

Expected: only the spec, plan, s1 package, tests, notebook, dependency lock, and narrow note/doc updates appear.

- [ ] **Step 5: Commit spec correction and implementation plan if still uncommitted**

```bash
git add docs/superpowers/specs/2026-07-19-leto-initial-state-design.md \
  docs/superpowers/plans/2026-07-20-leto-initial-state.md
git commit -m "docs: plan LETO initial-state implementation"
```

- [ ] **Step 6: Prepare the required loop-closure report**

Report exact files/components changed, test/lint commands and outcomes, documentation updated, the worktree path and branch, and the remaining gap that real LETO datasets were not available for a production-scale execution.
