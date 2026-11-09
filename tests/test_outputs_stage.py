"""The ``outputs`` stage: a scheduled run folder to its trajectory table, COGs and QA report."""

import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from pipeline import outputs_stage as os_
from pipeline import run
from pipeline.s4_fvs import trajectory_library as tl
from pipeline.s4_fvs.regime_templates import ThinDBH
from pipeline.spatial_ref import project_crs, resolution_m, snap_origin

LONG_CN = "1234567890123456789"          # 19 digits: a float64 would round it
CELL = 30.0
X0, Y0 = 1_000_020.0, 1_200_000.0        # the grid's origin, a 30 m multiple
N_CYCLES = 10
THIN_CUT = "thin_then_cut@+5"            # a 30% thin in 2032, a clearcut in 2042
CUT = "cut@+0"                           # a clearcut in 2027


def _keyfile(plt: str, entries: list[tl.Entry]) -> str:
    return tl.render(plt, "plantation_rotation", entries)


RUNS = {   # run_hash -> (PLT_CN, prescription, keyfile entries, {cycle: removed per acre})
    "h-thin": (LONG_CN, THIN_CUT, [tl.Entry(ThinDBH(2032, 0.3)), tl.Entry(ThinDBH(2042, 1.0))],
               {2: 50.0, 4: 400.0}),
    "h-none": (LONG_CN, "no_management", [], {}),
    "h-cut": ("2", CUT, [tl.Entry(ThinDBH(2027, 1.0))], {1: 300.0}),
    "h-none2": ("2", "no_management", [], {}),
}
# unit -> (PLT_CN, owner class, owner group, unit class, acres, selected run, column of cells)
UNITS = {
    "12003-1": (LONG_CN, "corporate", "Private", "managed", 2.0, "h-thin", 0),
    "12003-2": (LONG_CN, "corporate", "Private", "managed", 1.0, "h-thin", 1),   # shares a run
    "12003-3": ("2", "state", "Other public", "managed", 3.0, "h-cut", 2),
    "12003-4": ("2", "state", "Other public", "riparian", 0.5, "h-none2", 3),
}


def _cycles() -> pd.DataFrame:
    rows = []
    for h, (plt, _presc, _entries, removed) in RUNS.items():
        for c in range(tl.NUM_CYCLE + 1):
            rows.append({"run_hash": h, "PLT_CN": plt, "cycle": c, "calendar_year": 2022 + 5 * c,
                         "Age": 20 + 5 * c, "BA": 80.0 + c, "MCuFt": 1000.0 + 10 * c,
                         "removed_merch_cuft_per_ac": removed.get(c, 0.0)})
    return pd.DataFrame(rows)


def _plan() -> pd.DataFrame:
    rows = []
    for unit, (plt, owner, group, cls, acres, h, _col) in UNITS.items():
        presc = RUNS[h][1]
        base, offset = tl.split_label(presc)
        removed = RUNS[h][3]
        rows.append({"stand_id": unit, "trajectory_id": f"t{unit}", "fvs_run_id": f"{plt}::{presc}",
                     "PLT_CN": plt, "prescription": presc, "base_prescription": base,
                     "timing_offset_years": offset, "county": "Baker", "owner_class": owner,
                     "owner_group": group, "unit_class": cls, "acres": acres,
                     **{f"cuft_cycle_{c}": removed.get(c, 0.0) * acres
                        for c in range(1, N_CYCLES + 1)}})
    return pd.DataFrame(rows)


def _library() -> pd.DataFrame:
    rows = []
    for unit, (plt, *_rest) in UNITS.items():
        for h, (run_plt, presc, _e, _r) in RUNS.items():
            if run_plt == plt:
                rows.append({"unit_id": unit, "PLT_CN": plt, "prescription": presc,
                             "base_prescription": tl.split_label(presc)[0], "run_hash": h})
    return pd.DataFrame(rows)


def _units_layer() -> gpd.GeoDataFrame:
    """One 2x2-cell square per unit along a row, and a masked unit with no plan."""
    rows = []
    for unit, (_plt, owner, _g, cls, acres, _h, col) in UNITS.items():
        x = X0 + col * 3 * CELL
        rows.append({"unit_id": unit, "owner_class": owner, "unit_class": cls, "Acres": acres,
                     "geometry": box(x, Y0 - 2 * CELL, x + 2 * CELL, Y0)})
    rows.append({"unit_id": "12003-5", "owner_class": "masked", "unit_class": "managed",
                 "Acres": 0.2, "geometry": box(X0, Y0 - 5 * CELL, X0 + 2 * CELL, Y0 - 3 * CELL)})
    return gpd.GeoDataFrame(rows, crs=project_crs())


def _write_grid(path, x0=X0 - 300, y0=Y0 + 300) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=40, width=60, count=1, dtype="int32",
                       crs=project_crs(), transform=from_origin(x0, y0, CELL, CELL), nodata=0) as dst:
        dst.write(np.ones((1, 40, 60), dtype=np.int32))


@pytest.fixture
def run_dir(tmp_path):
    """A run folder after ``schedule``: stands, library, cycles, keyfiles, plan and units."""
    d = tmp_path / "run"
    d.mkdir()
    stands = pd.DataFrame([{"unit_id": u, "PLT_CN": p, "county": "Baker", "owner_class": o,
                            "OWN_CODE": 4, "FORTYPCD": 161, "forest_branch": "pine",
                            "stand_age": 20, "acres": a, "unit_class": c}
                           for u, (p, o, _g, c, a, _h, _col) in UNITS.items()])
    stands.to_csv(d / run.STANDS, index=False)
    _library().to_csv(d / run.LIBRARY, index=False)
    _cycles().to_csv(d / run.CYCLES, index=False)
    _plan().to_csv(d / run.PLAN, index=False)
    (d / "keyfiles").mkdir()
    for h, (plt, _presc, entries, _r) in RUNS.items():
        (d / "keyfiles" / f"{h}.key").write_text(_keyfile(plt, entries))
    _units_layer().to_file(d / os_.UNITS_GPKG, layer=os_.UNITS_LAYER, driver="GPKG")
    _write_grid(d / "leto" / "12003" / "parent_stands.tif")
    return d


def _table(d) -> pd.DataFrame:
    return pd.read_csv(d / os_.TABLE, dtype={"unit_id": str, "PLT_CN": str, "run_hash": str})


def _cell(path, unit_col: int, band: int = 1):
    """The value at the centre of a unit's first cell."""
    with rasterio.open(path) as src:
        row, col = src.index(X0 + unit_col * 3 * CELL + CELL / 2, Y0 - CELL / 2)
        return src.read(band)[row, col]


# ── activities from a keyfile ──────────────────────────────────────────────────────────


def test_a_keyfiles_thins_and_clearcuts_land_in_their_cycles():
    text = _keyfile(LONG_CN, RUNS["h-thin"][2])

    assert os_.keyfile_activities(text) == {2: os_.Activity.THINNING, 4: os_.Activity.CLEARCUT}


def test_a_keyfile_without_a_cut_has_no_activity():
    assert os_.keyfile_activities(_keyfile("2", [])) == {}


# ── the trajectory table ───────────────────────────────────────────────────────────────


def test_the_table_has_a_row_per_unit_and_cycle_even_when_units_share_a_run(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.NONE)

    table = _table(run_dir)
    assert len(table) == len(UNITS) * (N_CYCLES + 1)
    assert table.groupby("unit_id")["cycle"].apply(list).map(sorted).eq(
        [list(range(N_CYCLES + 1))] * len(UNITS)).all()
    assert set(table.loc[table["run_hash"] == "h-thin", "unit_id"]) == {"12003-1", "12003-2"}


def test_the_table_keeps_control_numbers_exact(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.NONE)

    assert LONG_CN in set(_table(run_dir)["PLT_CN"])


def test_each_row_carries_the_selected_regime_its_activity_and_vegetation(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.NONE)

    unit = _table(run_dir).query("unit_id == '12003-1'").set_index("cycle")
    assert unit.loc[0, "prescription"] == THIN_CUT and unit.loc[0, "timing_offset_years"] == 5
    assert unit.loc[2, "activity"] == "thinning" and unit.loc[4, "activity"] == "clearcut"
    assert unit.loc[3, "activity"] == "none"
    assert unit.loc[4, "BA"] == 84.0 and unit.loc[4, "Age"] == 40
    assert unit.loc[4, "removed_cuft"] == 400.0 * 2.0


def test_riparian_units_show_no_activity_in_any_cycle(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.NONE)

    riparian = _table(run_dir).query("unit_class == 'riparian'")
    assert len(riparian) == N_CYCLES + 1 and set(riparian["activity"]) == {"none"}


def test_a_riparian_unit_scheduled_to_cut_stops_the_stage(run_dir):
    plan = pd.read_csv(run_dir / run.PLAN, dtype=str)
    plan.loc[plan["stand_id"] == "12003-4", "prescription"] = CUT
    plan.to_csv(run_dir / run.PLAN, index=False)

    with pytest.raises(os_.RiparianActivity, match="12003-4"):
        os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.NONE)


def test_a_selected_run_without_its_keyfile_stops_the_stage(run_dir):
    (run_dir / "keyfiles" / "h-cut.key").unlink()

    with pytest.raises(os_.MissingKeyfile, match="h-cut"):
        os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.NONE)


def test_a_planned_unit_missing_from_the_units_layer_stops_the_stage(run_dir):
    _units_layer().query("unit_id != '12003-3'").to_file(
        run_dir / os_.UNITS_GPKG, layer=os_.UNITS_LAYER, driver="GPKG", mode="w")

    with pytest.raises(os_.UnitsMissingFromLayer, match="12003-3"):
        os_.run_outputs(run_dir, rasters=os_.RasterSet.ACTIVITY, qa=os_.QaLevel.NONE)


# ── the rasters ────────────────────────────────────────────────────────────────────────


def test_an_activity_cog_per_timestep_on_the_runs_grid(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.ACTIVITY, qa=os_.QaLevel.NONE)

    paths = sorted((run_dir / os_.ACTIVITY_DIR).glob("*.tif"))
    assert len(paths) == N_CYCLES + 1
    year_2032 = run_dir / os_.ACTIVITY_DIR / "activity_2032.tif"
    with rasterio.open(year_2032) as src:
        assert src.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
        assert src.res == (CELL, CELL) and src.crs == project_crs()
        assert (src.transform.c - (X0 - 300)) % CELL == 0     # aligned to the leto grid
    assert _cell(year_2032, 0) == os_.Activity.THINNING
    assert _cell(run_dir / os_.ACTIVITY_DIR / "activity_2042.tif", 1) == os_.Activity.CLEARCUT
    assert _cell(run_dir / os_.ACTIVITY_DIR / "activity_2027.tif", 2) == os_.Activity.CLEARCUT
    assert _cell(year_2032, 2) == os_.Activity.NONE


def test_riparian_cells_are_never_managed_in_any_timestep(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.ACTIVITY, qa=os_.QaLevel.NONE)

    for path in (run_dir / os_.ACTIVITY_DIR).glob("*.tif"):
        assert _cell(path, 3) == os_.Activity.NONE


def test_a_masked_unit_without_a_plan_is_nodata(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.ACTIVITY, qa=os_.QaLevel.NONE)

    with rasterio.open(run_dir / os_.ACTIVITY_DIR / "activity_2022.tif") as src:
        row, col = src.index(X0 + CELL / 2, Y0 - 4 * CELL + CELL / 2)
        assert src.read(1)[row, col] == src.nodata


def test_a_vegetation_cog_per_timestep_carries_the_fvs_measures(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.VEGETATION, qa=os_.QaLevel.NONE)

    path = run_dir / os_.VEGETATION_DIR / "vegetation_2042.tif"
    assert len(list((run_dir / os_.VEGETATION_DIR).glob("*.tif"))) == N_CYCLES + 1
    assert not (run_dir / os_.ACTIVITY_DIR).exists()
    with rasterio.open(path) as src:
        assert src.descriptions == os_.VEGETATION_MEASURES
        assert src.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
    assert _cell(path, 0, band=1) == 40          # Age in cycle 4
    assert _cell(path, 0, band=2) == 84.0        # BA
    assert _cell(path, 0, band=3) == 1040.0      # MCuFt


def test_the_unit_index_raster_joins_a_cell_to_its_unit(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.ACTIVITY, qa=os_.QaLevel.NONE)

    index = pd.read_csv(run_dir / os_.UNIT_INDEX_CSV, dtype={"unit_id": str})
    value = _cell(run_dir / os_.UNIT_INDEX_TIF, 2)
    assert index.set_index("index").loc[value, "unit_id"] == "12003-3"


def _origin(path) -> tuple[float, float]:
    with rasterio.open(path) as src:
        return src.transform.c, src.transform.f


def test_without_a_leto_grid_the_stage_falls_back_to_the_treemap_snap_grid(run_dir):
    (run_dir / "leto" / "12003" / "parent_stands.tif").unlink()

    os_.run_outputs(run_dir, rasters=os_.RasterSet.ACTIVITY, qa=os_.QaLevel.NONE)

    x, y = _origin(run_dir / os_.ACTIVITY_DIR / "activity_2022.tif")
    sx, sy = snap_origin()
    assert (x - sx) % resolution_m() == 0 and (y - sy) % resolution_m() == 0


def test_an_explicit_grid_raster_sets_the_alignment(run_dir, tmp_path):
    grid = tmp_path / "grid.tif"
    _write_grid(grid, x0=X0 - 300 + 10, y0=Y0 + 300 + 10)     # shifted 10 m off the leto grid

    os_.run_outputs(run_dir, rasters=os_.RasterSet.ACTIVITY, qa=os_.QaLevel.NONE, grid=grid)

    x, y = _origin(run_dir / os_.ACTIVITY_DIR / "activity_2022.tif")
    assert (x - (X0 + 10)) % CELL == 0 and (y - (Y0 + 10)) % CELL == 0


# ── the QA report ──────────────────────────────────────────────────────────────────────


def test_the_qa_harvest_totals_reconcile_with_the_plan(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.TABLES)

    harvest = pd.read_csv(run_dir / os_.QA_DIR / os_.HARVEST_QA)
    plan = _plan()
    expected = plan.groupby("owner_group")[[f"cuft_cycle_{c}" for c in range(1, N_CYCLES + 1)]].sum()
    for row in harvest.itertuples():
        assert row.removed_cuft == pytest.approx(expected.loc[row.owner_group, f"cuft_cycle_{row.cycle}"])
        assert row.plan_cuft == pytest.approx(row.removed_cuft)
    assert set(harvest["owner_group"]) == {"Private", "Other public"}
    assert set(harvest["cycle"]) == set(range(1, N_CYCLES + 1))


def test_a_plan_that_disagrees_with_its_trajectories_stops_the_stage(run_dir):
    plan = _plan()
    plan.loc[0, "cuft_cycle_2"] += 1.0
    plan.to_csv(run_dir / run.PLAN, index=False)

    with pytest.raises(os_.PlanMismatch, match="Private"):
        os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.TABLES)


def test_the_full_qa_report_lays_out_tables_and_figures_offline(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.ALL, qa=os_.QaLevel.FULL)

    qa = run_dir / os_.QA_DIR
    for name in (os_.HARVEST_QA, os_.ACTIVITY_QA, os_.VEGETATION_QA, os_.COVERAGE_QA):
        assert (qa / name).exists(), name
    pngs = {p.name for p in (qa / "figures").glob("*.png")}
    assert {"harvest_by_owner_group.png", "activity_acres_by_cycle.png",
            "vegetation_by_cycle.png", "map_activity.png", "map_MCuFt.png"} <= pngs
    page = (qa / "index.html").read_text()
    assert "harvest_by_owner_group.csv" in page and "figures/map_activity.png" in page
    assert "http" not in page.replace("http-equiv", "")          # nothing fetched


def test_the_coverage_table_counts_units_too_small_for_a_cell(run_dir):
    layer = _units_layer()
    layer.loc[layer["unit_id"] == "12003-2", "geometry"] = box(
        X0 + 3 * CELL + 1, Y0 - 10, X0 + 3 * CELL + 11, Y0 - 1)       # off every cell centre
    layer.to_file(run_dir / os_.UNITS_GPKG, layer=os_.UNITS_LAYER, driver="GPKG", mode="w")

    os_.run_outputs(run_dir, rasters=os_.RasterSet.ACTIVITY, qa=os_.QaLevel.TABLES)

    coverage = pd.read_csv(run_dir / os_.QA_DIR / os_.COVERAGE_QA).set_index("measure")["value"]
    assert coverage["planned_units"] == len(UNITS)
    assert coverage["units_without_a_cell"] == 1
    assert coverage["acres_without_a_cell"] == 1.0
    assert coverage["unplanned_units"] == 1


def test_no_qa_writes_no_report(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.NONE)

    assert not (run_dir / os_.QA_DIR).exists()


def test_main_outputs_runs_the_stage(run_dir):
    run.main(["outputs", str(run_dir), "--rasters", "none", "--qa", "tables"])

    assert (run_dir / os_.TABLE).exists()
    assert (run_dir / os_.QA_DIR / os_.HARVEST_QA).exists()
    record = json.loads((run_dir / os_.RECORD).read_text())
    assert record["cycles"] == list(range(N_CYCLES + 1)) and record["rasters"] == "none"


def test_a_full_removal_inside_a_diameter_window_is_a_thinning():
    text = _keyfile("2", [tl.Entry(ThinDBH(2032, 1.0, min_dbh=0.0, max_dbh=8.0)),
                          tl.Entry(ThinDBH(2042, 1.0, species=131))])

    assert os_.keyfile_activities(text) == {2: os_.Activity.THINNING, 4: os_.Activity.THINNING}


def test_a_failed_rerun_leaves_no_stale_outputs(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.TABLES)
    (run_dir / "keyfiles" / "h-cut.key").unlink()

    with pytest.raises(os_.MissingKeyfile):
        os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.TABLES)

    assert not (run_dir / os_.TABLE).exists() and not (run_dir / os_.QA_DIR).exists()


def test_a_plot_and_prescription_with_two_runs_stops_the_stage(run_dir):
    library = _library()
    library.loc[library["unit_id"] == "12003-2", "run_hash"] = library.loc[
        library["unit_id"] == "12003-2", "run_hash"] + "-other"
    library.to_csv(run_dir / run.LIBRARY, index=False)

    with pytest.raises(os_.AmbiguousRun, match=LONG_CN):
        os_.run_outputs(run_dir, rasters=os_.RasterSet.NONE, qa=os_.QaLevel.NONE)


def test_units_without_a_plan_are_listed_in_the_qa_report(run_dir):
    os_.run_outputs(run_dir, rasters=os_.RasterSet.ACTIVITY, qa=os_.QaLevel.TABLES)

    unplanned = pd.read_csv(run_dir / os_.QA_DIR / os_.UNPLANNED_QA, dtype={"unit_id": str})
    assert unplanned["unit_id"].tolist() == ["12003-5"]
    assert unplanned["owner_class"].tolist() == ["masked"]
