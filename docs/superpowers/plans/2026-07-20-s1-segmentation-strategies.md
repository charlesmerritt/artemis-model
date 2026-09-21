# S1 Segmentation Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make management-unit creation the first ARTEMIS stage with a faithful pure-Python LETO method, preserve the boundary-overlay method as an experimental alternative, and feed both through one TreeMap/FIA initial-state contract.

**Architecture:** Add focused `segmentation` modules under `pipeline/s1_initial_state`, move the existing S3 implementation behind an import-compatible wrapper, and keep TreeMap/FIA attribution shared. Production readers use the mounted data drive directly, while portable tests use small rasters, GeoDataFrames, SQLite fixtures, and an optional Windows ArcPy reference runner.

**Tech Stack:** Python 3.14, GeoPandas, Shapely 2, Rasterio, Pyogrio, Pandas, NumPy, SQLite, pytest, Ruff, nbformat.

## Global Constraints

- Management-unit creation is S1, not S3.
- Preserve LETO's stage order and defaults: 200-acre maximum, 100 acres per point, 1,000-foot point separation, 5-acre minimum, and 35-foot SMZ buffer.
- Preserve modal `PLT_CN` for unit identity while using all retained donor plots for weighted tree construction.
- Both segmentation methods must emit `MU_ID`, `Acres`, `SEGMENTATION_METHOD`, and geometry before shared attribution.
- Missing production data must stop execution and name the mount/R2 recovery action.
- The production FIADB source is read-only SQLite; state CSV exports remain supported but are not required.
- ArcPy/Python random polygons are compared by behavior and topology, not coordinate identity, because the original ArcPy script has no fixed seed.
- Do not change FVS database creation or later projection stages.

## File map

- Create `pipeline/s1_initial_state/data_sources.py`: verified production paths, hard preflight, VAT and FIADB readers.
- Create `pipeline/s1_initial_state/segmentation/__init__.py`: public segmentation functions.
- Create `pipeline/s1_initial_state/segmentation/leto.py`: LETO geometry creation, ownership, and SMZ attribution.
- Create `pipeline/s1_initial_state/segmentation/boundary_overlay.py`: canonical home of the current S3 method.
- Create `pipeline/s1_initial_state/segmentation/comparison.py`: method-neutral spatial and attribution metrics.
- Modify `pipeline/s3_management/sketch_management_units.py`: compatibility re-export and CLI delegation only.
- Modify `pipeline/s1_initial_state/leto_initial_state.py`: SQLite-backed coordinator and shared modal-unit attribution.
- Modify `notebooks/LETO_Initial_State_Walkthrough.ipynb`: segmentation becomes the first stage.
- Create `docs/research/leto-vs-boundary-overlay.md`: evidence-backed baseline review.
- Create `docs/superpowers/specs/2026-07-20-s1-segmentation-synthesis-design.md`: hybrid experiment specification.
- Add focused tests under `tests/` and an optional ArcPy reference runner under `tests/arcpy_reference/`.

---

### Task 1: Production data contract and readers

**Files:**
- Create: `pipeline/s1_initial_state/data_sources.py`
- Test: `tests/test_s1_data_sources.py`
- Modify: `pipeline/s1_initial_state/README.md`

**Interfaces:**
- Produces: `ProductionDataPaths.from_root(root: Path) -> ProductionDataPaths`
- Produces: `preflight_production_data(paths: ProductionDataPaths) -> None`
- Produces: `load_treemap_lookup(path: Path) -> pd.DataFrame`
- Produces: `load_fia_trees_sqlite(path: Path, plot_ids: Collection[str], state_codes: Collection[int] = (1, 12, 13, 45)) -> pd.DataFrame`
- Consumes: the existing `load_species_lookup` function for workbook validation.

- [ ] **Step 1: Write failing path, VAT, and SQLite tests**

```python
def test_preflight_names_mount_and_r2_when_source_is_missing(tmp_path):
    paths = ProductionDataPaths.from_root(tmp_path)
    with pytest.raises(FileNotFoundError, match="mount.*R2"):
        preflight_production_data(paths)


def test_load_treemap_lookup_preserves_large_plot_ids(tmp_path, monkeypatch):
    dbf_path = tmp_path / "lookup.dbf"
    dbf_path.touch()
    monkeypatch.setattr(
        data_sources,
        "read_dataframe",
        lambda path, read_geometry: pd.DataFrame({"Value": [7], "PLT_CN": [223267700000001]}),
    )
    result = load_treemap_lookup(dbf_path)
    assert result.to_dict("records") == [{"VALUE": 7, "PLT_CN": "223267700000001"}]


def test_load_fia_trees_sqlite_filters_plots_and_states(tmp_path):
    db_path = tmp_path / "fiadb.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table TREE (CN text, PLT_CN text, STATUSCD text, INVYR text, "
            "STATECD integer, SPCD text, DIA text, HT text, ACTUALHT text, CR text, TPA_UNADJ text)"
        )
        connection.executemany(
            "insert into TREE values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [("t1", "101", "1", "2022", 12, "131", "10", "50", "50", "40", "5"),
             ("t2", "202", "1", "2022", 13, "131", "9", "45", "45", "30", "4")],
        )
    result = load_fia_trees_sqlite(db_path, {"101"}, state_codes={12})
    assert result["PLT_CN"].tolist() == ["101"]
    assert result["STATECD"].tolist() == ["12"]
```

- [ ] **Step 2: Run tests and verify missing imports/functions fail**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_data_sources.py -v`

Expected: collection failure because `data_sources` does not exist.

- [ ] **Step 3: Implement immutable paths, fail-fast preflight, DBF normalization, and parameterized read-only SQLite query**

```python
@dataclass(frozen=True)
class ProductionDataPaths:
    root: Path
    treemap: Path
    treemap_vat: Path
    fiadb: Path
    species_crosswalk: Path
    ownership: Path
    parcels: Path
    streams: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProductionDataPaths":
        return cls(
            root=root,
            treemap=root / "TreeMap-2022/Data/TreeMap2022_CONUS.tif",
            treemap_vat=root / "TreeMap-2022/Data/TreeMap2022_CONUS.tif.vat.dbf",
            fiadb=root / "SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db",
            species_crosswalk=root / "FVS_SpeciesCrosswalk.xls",
            ownership=root / "RDS-2025-0045/Data/US_forest_ownership.tif",
            parcels=root / "FL_5_Co_Parcels.gdb",
            streams=root / "FL_5_Co_Streams.zip",
        )
```

Use `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` and chunk `PLT_CN IN (...)`
queries below SQLite's parameter limit. Select only columns required by
`prepare_direct_tree_rows`; convert identifiers to pandas string dtype.

- [ ] **Step 4: Run the focused tests and a live metadata preflight**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_data_sources.py -v`

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run python -c "from pathlib import Path; from pipeline.s1_initial_state.data_sources import ProductionDataPaths, preflight_production_data; preflight_production_data(ProductionDataPaths.from_root(Path('/mnt/d')))"`

Expected: tests pass and live preflight exits zero without scanning full tables.

- [ ] **Step 5: Commit**

```bash
git add pipeline/s1_initial_state/data_sources.py pipeline/s1_initial_state/README.md tests/test_s1_data_sources.py
git commit -m "feat(s1): add production data readers"
```

---

### Task 2: Faithful LETO geometry primitives

**Files:**
- Create: `pipeline/s1_initial_state/segmentation/__init__.py`
- Create: `pipeline/s1_initial_state/segmentation/leto.py`
- Test: `tests/test_s1_leto_segmentation.py`

**Interfaces:**
- Produces: `LetoSegmentationConfig(max_acres=200.0, acres_per_point=100.0, min_distance_feet=1000.0, min_acres=5.0, smz_buffer_feet=35.0, seed=0)`
- Produces: `calculate_acres(units: gpd.GeoDataFrame) -> gpd.GeoDataFrame`
- Produces: `sample_constrained_points(geometry: BaseGeometry, count: int, min_distance: float, rng: np.random.Generator) -> list[Point]`
- Produces: `split_unit_thiessen(geometry: BaseGeometry, point_count: int, min_distance: float, rng: np.random.Generator) -> list[Polygon]`
- Produces: `subdivide_large_units(units: gpd.GeoDataFrame, config: LetoSegmentationConfig) -> gpd.GeoDataFrame`

- [ ] **Step 1: Write failing tests for LETO thresholds, repeatability, coverage, and impossible constraints**

```python
def test_subdivide_large_units_is_repeatable_and_preserves_coverage():
    units = gpd.GeoDataFrame(geometry=[box(0, 0, 1_200, 1_200)], crs="EPSG:5070")
    config = LetoSegmentationConfig(max_acres=200, acres_per_point=100, min_distance_feet=100, seed=42)
    first = subdivide_large_units(units, config)
    second = subdivide_large_units(units, config)
    assert first.geometry.to_wkb().tolist() == second.geometry.to_wkb().tolist()
    assert first.geometry.union_all().symmetric_difference(units.geometry.iloc[0]).area == pytest.approx(0)
    assert first["Acres"].max() <= 200


def test_constrained_points_fail_instead_of_looping_forever():
    with pytest.raises(SegmentationError, match="minimum separation"):
        sample_constrained_points(box(0, 0, 1, 1), 3, 10, np.random.default_rng(1))
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_leto_segmentation.py -v`

Expected: collection failure for the missing module.

- [ ] **Step 3: Implement the smallest pure geometry pipeline**

Use rejection sampling with a bounded attempt count and Shapely
`voronoi_polygons(MultiPoint(points), extend_to=geometry.envelope)`. Intersect
each cell with the parent, explode polygonal parts, and validate that the union
matches the parent within `max(1e-6, parent.area * 1e-9)`. Derive child RNGs
from the run seed and stable parent order so reruns are deterministic.

- [ ] **Step 4: Run focused tests and Ruff**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_leto_segmentation.py -v`

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run ruff check pipeline/s1_initial_state/segmentation/leto.py tests/test_s1_leto_segmentation.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/s1_initial_state/segmentation tests/test_s1_leto_segmentation.py
git commit -m "feat(s1): port LETO unit subdivision"
```

---

### Task 3: LETO domain, cleanup, ownership, and SMZ stages

**Files:**
- Modify: `pipeline/s1_initial_state/segmentation/leto.py`
- Modify: `tests/test_s1_leto_segmentation.py`
- Test: `tests/test_s1_leto_arcpy_parity.py`
- Create: `tests/arcpy_reference/leto_segmentation_fixture.py`

**Interfaces:**
- Produces: `build_treemap_domain(treemap_path: Path, clip_features: gpd.GeoDataFrame) -> gpd.GeoDataFrame`
- Produces: `cleanup_and_clip_units(units: gpd.GeoDataFrame, parcels: gpd.GeoDataFrame, min_acres: float) -> gpd.GeoDataFrame`
- Produces: `assign_majority_ownership(units: gpd.GeoDataFrame, ownership_path: Path) -> gpd.GeoDataFrame`
- Produces: `assign_smz_percent(units: gpd.GeoDataFrame, streams: gpd.GeoDataFrame, buffer_feet: float) -> gpd.GeoDataFrame`
- Produces: `build_leto_management_units(treemap_path: Path, treemap_lookup: pd.DataFrame, parcels: gpd.GeoDataFrame, ownership_path: Path, streams: gpd.GeoDataFrame, config: LetoSegmentationConfig) -> tuple[gpd.GeoDataFrame, pd.DataFrame]`

- [ ] **Step 1: Add failing raster-domain, cleanup, majority, and SMZ tests**

```python
def test_assign_smz_percent_matches_legacy_intersection_formula():
    units = gpd.GeoDataFrame({"MU_ID": ["1"]}, geometry=[box(0, 0, 100, 100)], crs="EPSG:5070")
    streams = gpd.GeoDataFrame(geometry=[LineString([(0, 50), (100, 50)])], crs=units.crs)
    result = assign_smz_percent(units, streams, buffer_feet=10 / 0.3048)
    assert result.loc[0, "SMZ_Pct"] == pytest.approx(20.0)


def test_cleanup_matches_leto_singlepart_minimum_and_parcel_clip():
    large_piece = box(0, 0, 200, 200)
    small_piece = box(300, 0, 310, 310)
    parcels = gpd.GeoDataFrame(geometry=[box(0, 0, 500, 500)], crs="EPSG:5070")
    units = gpd.GeoDataFrame(geometry=[MultiPolygon([large_piece, small_piece])], crs="EPSG:5070")
    result = cleanup_and_clip_units(units, parcels, min_acres=5)
    assert len(result) == 1
    assert result.iloc[0].geometry.within(parcels.geometry.union_all())
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_leto_segmentation.py tests/test_s1_leto_arcpy_parity.py -v`

- [ ] **Step 3: Implement windowed raster domain and per-unit ownership sampling**

Read only raster windows intersecting the AOI. Polygonize valid TreeMap cells
with `rasterio.features.shapes`, dissolve them, and intersect the parcel AOI.
For ownership, transform each unit to the ownership CRS, read its raster window,
mask by geometry using pixel centers, count non-nodata codes, and select the
largest count with ascending-code tie-break. Calculate SMZ area in the units'
projected CRS after converting feet to meters.

- [ ] **Step 4: Add the optional ArcPy reference runner**

The runner creates the same tiny vector/raster fixture under a caller-selected
temporary directory and executes the corresponding ArcPy operations. Mark the
test `@pytest.mark.arcpy` and skip unless `ARCGIS_PYTHON` is set. Compare stage
fields and invariants; do not assert identical random polygons.

- [ ] **Step 5: Run portable parity tests and, if importable, the ArcPy fixture**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_leto_segmentation.py tests/test_s1_leto_arcpy_parity.py -v`

Expected: portable tests pass; ArcPy test is either pass or an explicit skip.

- [ ] **Step 6: Commit**

```bash
git add pipeline/s1_initial_state/segmentation/leto.py tests/test_s1_leto_segmentation.py tests/test_s1_leto_arcpy_parity.py tests/arcpy_reference/leto_segmentation_fixture.py
git commit -m "feat(s1): complete faithful LETO segmentation"
```

---

### Task 4: Move the boundary-overlay baseline into S1

**Files:**
- Create: `pipeline/s1_initial_state/segmentation/boundary_overlay.py`
- Modify: `pipeline/s3_management/sketch_management_units.py`
- Create: `tests/test_s1_boundary_overlay.py`
- Modify: `tests/test_s3_sketch_management_units.py`

**Interfaces:**
- Preserves: `feet_to_meters`, `classify_stream_fcode`, `classify_unit_size`, `clean_geometries`, `split_large_geometry`, `process_county`, and `main`.
- Adds: `SEGMENTATION_METHOD="boundary_overlay"` and normalized `MU_ID`/`Acres` fields to emitted units.

- [ ] **Step 1: Add failing canonical-import and compatibility tests**

```python
def test_s3_imports_are_compatibility_aliases():
    from pipeline.s1_initial_state.segmentation import boundary_overlay as canonical
    from pipeline.s3_management import sketch_management_units as legacy
    assert legacy.process_county is canonical.process_county


def test_boundary_output_meets_s1_contract(candidate_units):
    result = normalize_output_contract(candidate_units)
    assert {"MU_ID", "Acres", "SEGMENTATION_METHOD", "geometry"} <= set(result.columns)
    assert set(result["SEGMENTATION_METHOD"]) == {"boundary_overlay"}
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_boundary_overlay.py tests/test_s3_sketch_management_units.py -v`

- [ ] **Step 3: Move implementation without unrelated refactoring**

Copy the current module to the S1 path, add only shared-contract normalization,
then replace the S3 module body with explicit re-exports and `main()` delegation.
Keep old command-line flags working.

- [ ] **Step 4: Run compatibility and full existing S3 tests**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_boundary_overlay.py tests/test_s3_sketch_management_units.py -v`

Expected: all pass with the canonical implementation located only under S1.

- [ ] **Step 5: Commit**

```bash
git add pipeline/s1_initial_state/segmentation/boundary_overlay.py pipeline/s3_management/sketch_management_units.py tests/test_s1_boundary_overlay.py tests/test_s3_sketch_management_units.py
git commit -m "refactor(s1): move boundary segmentation into initial state"
```

---

### Task 5: Share modal attribution and SQLite-backed initial-state execution

**Files:**
- Modify: `pipeline/s1_initial_state/leto_initial_state.py`
- Modify: `pipeline/s1_initial_state/weights.py`
- Test: `tests/test_s1_leto_initial_state.py`
- Modify: `tests/test_s1_leto_parity.py`

**Interfaces:**
- Produces: `attach_modal_plot(units: gpd.GeoDataFrame, weights: pd.DataFrame) -> gpd.GeoDataFrame`
- Produces: `run_initial_state_from_sqlite(management_units: gpd.GeoDataFrame, weights: pd.DataFrame, fiadb_path: Path, species_crosswalk_path: Path, output_dir: Path | None = None) -> InitialStateTables`
- Preserves: existing CSV-backed `run_leto_initial_state` API.

- [ ] **Step 1: Add failing mixed-plot and SQLite coordinator tests**

```python
def test_modal_plot_is_identity_but_all_retained_plots_build_trees():
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1"], "Acres": [10.0], "OWN_CODE": [4],
         "OWN_TYPE": ["Corporate/Other Private Forest"], "SMZ_Pct": [0.0]},
        geometry=[box(0, 0, 100, 100)], crs="EPSG:5070",
    )
    weights = pd.DataFrame({
        "MU_ID": ["1", "1"], "TM_VALUE": [1, 2], "CELL_COUNT": [6, 4],
        "TOTAL_CELLS": [10, 10], "WEIGHT": [0.6, 0.4], "PLT_CN": ["101", "202"],
    })
    fia = pd.DataFrame({
        "CN": ["t1", "t2"], "PLT_CN": ["101", "202"], "STATUSCD": ["1", "1"],
        "INVYR": ["2022", "2022"], "SPCD": ["131", "131"], "DIA": ["10", "10"],
        "HT": ["50", "50"], "ACTUALHT": ["50", "50"], "CR": ["40", "40"],
        "TPA_UNADJ": ["10", "10"],
    })
    attributed = attach_modal_plot(units, weights)
    tables = build_initial_state(attributed, weights, fia, {"131": "LP"})
    assert attributed.loc[0, "PLT_CN"] == "101"
    assert set(tables.trees["PLT_CN"]) == {"101", "202"}
    assert tables.trees.groupby("PLT_CN")["TREE_COUNT"].sum().to_dict() == {"101": 6.0, "202": 4.0}
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_leto_initial_state.py tests/test_s1_leto_parity.py -v`

- [ ] **Step 3: Implement modal attachment and query only weighted plots from SQLite**

Use the existing stable LETO tie-break (`MU_ID`, descending `CELL_COUNT`, then
ascending `TM_VALUE`). Call `load_fia_trees_sqlite` with the normalized weighted
plot IDs, then pass its result through the existing `build_initial_state` code.

- [ ] **Step 4: Run focused tests**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_leto_initial_state.py tests/test_s1_leto_parity.py tests/test_s1_data_sources.py -v`

- [ ] **Step 5: Commit**

```bash
git add pipeline/s1_initial_state/leto_initial_state.py pipeline/s1_initial_state/weights.py tests/test_s1_leto_initial_state.py tests/test_s1_leto_parity.py
git commit -m "feat(s1): connect segmentation to FIADB initial state"
```

---

### Task 6: Machine-readable and human-readable comparisons

**Files:**
- Create: `pipeline/s1_initial_state/segmentation/comparison.py`
- Create: `tests/test_s1_segmentation_comparison.py`
- Create: `docs/research/leto-vs-boundary-overlay.md`
- Modify: `notes/README.md`

**Interfaces:**
- Produces: `compare_segmentations(reference: gpd.GeoDataFrame, candidate: gpd.GeoDataFrame, *, reference_name: str, candidate_name: str) -> pd.Series`
- Produces: `compare_attribution(reference_weights: pd.DataFrame, candidate_weights: pd.DataFrame) -> pd.Series`
- Produces: `write_comparison(metrics: pd.Series, path: Path) -> None`

- [ ] **Step 1: Add failing metric tests**

```python
def test_compare_segmentations_reports_coverage_overlap_and_sizes():
    reference = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"], "Acres": [0.000247, 0.000247]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs="EPSG:5070",
    )
    candidate = gpd.GeoDataFrame(
        {"MU_ID": ["a", "b", "c"], "Acres": [0.000247, 0.0001235, 0.0001235]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 1.5, 1), box(1.5, 0, 2, 1)],
        crs="EPSG:5070",
    )
    metrics = compare_segmentations(reference, candidate, reference_name="arcpy_leto", candidate_name="python_leto")
    assert metrics["reference_unit_count"] == 2
    assert metrics["candidate_unit_count"] == 3
    assert metrics["coverage_jaccard"] == pytest.approx(1.0)
    assert metrics["candidate_overlap_acres"] == pytest.approx(0)
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_segmentation_comparison.py -v`

- [ ] **Step 3: Implement shared metrics without matching unit IDs across methods**

Compute union coverage, intersection, symmetric difference, within-method
overlap, count, total/median/p05/p95 acreage, sliver/oversized counts, and
per-unit boundary-length summaries. Attribution comparison reports donor-count
distribution, mixed-plot rate, modal plot agreement only where an explicit
crosswalk is supplied, and raw/normalized weight-sum diagnostics.

- [ ] **Step 4: Write the source-stage and scientific side-by-side review**

Document the observed code paths, current approximations, shared metrics, and
research implications. Label claims as established behavior, interpretation,
or hypothesis. Do not report production numerical results until the smoke run.

- [ ] **Step 5: Run tests and commit**

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_segmentation_comparison.py -v
git add pipeline/s1_initial_state/segmentation/comparison.py tests/test_s1_segmentation_comparison.py docs/research/leto-vs-boundary-overlay.md notes/README.md
git commit -m "feat(s1): compare segmentation baselines"
```

---

### Task 7: Notebook and hybrid research specification

**Files:**
- Modify: `notebooks/LETO_Initial_State_Walkthrough.ipynb`
- Modify: `tests/test_s1_leto_notebook.py`
- Create: `docs/superpowers/specs/2026-07-20-s1-segmentation-synthesis-design.md`
- Modify: `pipeline/s1_initial_state/README.md`
- Modify: `notes/notebooks.md`

**Interfaces:**
- Notebook imports both segmentation strategies and the comparison functions.
- Synthesis spec defines hypotheses, factors, metrics, and keep/reject gates; it does not implement a hybrid.

- [ ] **Step 1: Extend notebook structural tests first**

```python
def test_walkthrough_begins_with_segmentation_and_offers_both_methods():
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    source = "\n".join(cell.source for cell in notebook.cells)
    assert source.index("Management-unit segmentation") < source.index("MU x PLT_CN weights")
    assert "build_leto_management_units" in source
    assert "boundary_overlay" in source
    assert "WRITE_OUTPUTS = False" in source
```

- [ ] **Step 2: Run the notebook test and confirm RED**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_leto_notebook.py -v`

- [ ] **Step 3: Update notebook cells to call production functions**

Add data preflight, method selection, segmentation parameters, method-specific
diagnostics, shared attribution, and comparison stages. Keep cells unexecuted
and output writing disabled.

- [ ] **Step 4: Write the synthesis research spec**

Define experiments for domain source, exclusion boundaries, large-unit
splitter, sliver policy, SMZ treatment, and seed sensitivity. Specify controlled
AOIs, repeated seeds, coverage/fragmentation/FIA/FVS metrics, and paired
keep/reject criteria. Preserve both baselines regardless of hybrid outcome.

- [ ] **Step 5: Validate notebook and docs, then commit**

```bash
UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_leto_notebook.py -v
git diff --check
git add notebooks/LETO_Initial_State_Walkthrough.ipynb tests/test_s1_leto_notebook.py docs/superpowers/specs/2026-07-20-s1-segmentation-synthesis-design.md pipeline/s1_initial_state/README.md notes/notebooks.md
git commit -m "docs(s1): add segmentation research workflow"
```

---

### Task 8: Production smoke, full verification, and independent review

**Files:**
- Create: `tests/test_s1_production_smoke.py`
- Modify only if evidence requires it: files introduced in Tasks 1 through 7.

**Interfaces:**
- Mark production test `@pytest.mark.production_data` and skip only when explicitly deselected; once selected, missing data is a failure.
- The smoke fixture uses a small real parcel/AOI window and never writes to `/mnt/d`.

- [ ] **Step 1: Add a production smoke that exercises real readers and one small LETO AOI**

```python
@pytest.mark.production_data
def test_real_data_small_aoi_builds_weighted_management_units():
    paths = ProductionDataPaths.from_root(Path("/mnt/d"))
    preflight_production_data(paths)
    parcels = gpd.read_file(paths.parcels, layer="FL_5_Co_Parcels", rows=1)
    streams = gpd.read_file(f"zip://{paths.streams}", mask=parcels.to_crs("EPSG:26917"))
    lookup = load_treemap_lookup(paths.treemap_vat)
    units, weights = build_leto_management_units(
        paths.treemap,
        lookup,
        parcels,
        paths.ownership,
        streams,
        LetoSegmentationConfig(seed=7),
    )
    assert not units.empty
    assert not weights.empty
    assert weights.groupby("MU_ID")["WEIGHT"].sum().between(0.999999, 1.000001).all()
```

- [ ] **Step 2: Run production smoke explicitly**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. -m production_data tests/test_s1_production_smoke.py -v`

Expected: PASS against `/mnt/d`; if a source disappears, stop and ask for mount/R2 recovery.

- [ ] **Step 3: Run focused and compatibility suites**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. tests/test_s1_*.py tests/test_s3_sketch_management_units.py -v`

- [ ] **Step 4: Run full tests and static checks**

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run pytest --rootdir=. -q`

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run ruff check pipeline/s1_initial_state pipeline/s3_management/sketch_management_units.py tests/test_s1_*.py tests/test_s3_sketch_management_units.py`

Run: `UV_CACHE_DIR=/tmp/artemis-leto-uv-cache uv run ruff format --check pipeline/s1_initial_state pipeline/s3_management/sketch_management_units.py tests/test_s1_*.py tests/test_s3_sketch_management_units.py`

Run: `git diff --check main...HEAD`

- [ ] **Step 5: Request independent review and fix every Critical/Important finding with a regression test**

Review scope: faithful LETO semantics, non-overlapping/terminating geometry,
modal versus weighted plot behavior, data-drive fail-fast behavior, S3 import
compatibility, comparison validity, and production-smoke evidence.

- [ ] **Step 6: Commit final review fixes and rerun every gate**

```bash
git add pipeline/s1_initial_state pipeline/s3_management/sketch_management_units.py tests/test_s1_data_sources.py tests/test_s1_leto_segmentation.py tests/test_s1_leto_arcpy_parity.py tests/test_s1_boundary_overlay.py tests/test_s1_leto_initial_state.py tests/test_s1_leto_parity.py tests/test_s1_segmentation_comparison.py tests/test_s1_leto_notebook.py tests/test_s1_production_smoke.py tests/test_s3_sketch_management_units.py tests/arcpy_reference/leto_segmentation_fixture.py docs/research/leto-vs-boundary-overlay.md docs/superpowers/specs/2026-07-20-s1-segmentation-synthesis-design.md notebooks/LETO_Initial_State_Walkthrough.ipynb notes/README.md notes/notebooks.md
git commit -m "fix(s1): address segmentation review"
```
