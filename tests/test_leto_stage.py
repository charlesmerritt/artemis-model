"""The ``leto`` stage: repaired rasters to management units and ``stands.csv``, county by county.

One county (#163), its plot layout's ``FVS_Data.db`` (#164), many counties: resume, workers
and the recorded layout (#165), and the LETO layout's per-unit inventory (#170).

The fixture is a tiny raster-repair run folder over two counties side by side, Alpha (12001)
and Beta (12003). A band of plot 11 runs across the county line, so a whole-window
segmentation would make a parent stand that crosses it. Alpha holds an add-back patch: the
run's own raster stamps it with neighbour plot 33, and ``young_plots/`` gives it young-plot
ID 1,000,000. A perennial stream and a pond run through Alpha; Beta has only an ephemeral
stream, which is never buffered.
"""

import dataclasses
import hashlib
import json
import shutil
import sqlite3
import stat

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
import yaml
from rasterio import features
from rasterio.transform import Affine
from shapely.geometry import LineString, box, shape

from pipeline import leto_riparian
from pipeline.leto_ca import SmallStandAction
from pipeline.leto_proximity import ProximityMatch, ProximitySettings, Unmatched
from pipeline.ids import read_id_csv
from pipeline.leto_database import DatabaseMode
from pipeline.leto_layout import unit_stdage_means
from pipeline.leto_speckle import SpeckleAction, SpeckleSettings
from pipeline.leto_stage import (
    CONFIG_PATH,
    AddBackWithoutYoungPlots,
    AmbiguousStandInit,
    ConflictingPlotRows,
    CountyOutcome,
    MissingRepresentativePlots,
    NoCountiesInManifest,
    NeighbourIdOnAddBack,
    UnknownCounty,
    PlotInventory,
    UnknownYoungPlotIds,
    CountyCells,
    assemble_inventory,
    clip_county,
    read_county,
    load_settings,
    parent_traits,
    run_leto,
)
from pipeline.run import InventoryLayout, LayoutMismatch, main, read_stands
from pipeline.s1_initial_state.add_back_ids import VAT_COLUMNS
from pipeline.spatial_ref import project_crs

NODATA = 4294967295
HARRIS_NODATA = 15
CELL = 30.0
X0, Y0 = 1_300_000.0, 900_000.0
ROWS, COLS = 30, 80                       # Alpha is columns 0-39 (0-44 below row 15), Beta the rest
ALPHA, BETA = "12001", "12003"
YOUNG_ID = 1_000_000

PLOT_11 = "100000000000011"
PLOT_22 = "1234567890123456789"           # 19 digits: a float64 would round it
PLOT_33 = "100000000000033"               # the neighbour plot: only ever on add-back pixels
PLOT_44 = "100000000000044"               # the young plot behind ID 1,000,000
TREE_11 = "9000000000000000111"           # 19-digit tree control numbers
TREE_22 = "9000000000000000222"
# FIA code -> SN alpha, as the crosswalk's EasternSpeciesTranslator maps them
SPECIES = {"131": "LP", "611": "SU", "111": "SA", "121": "LL"}
LIVE_TPA = {PLOT_11: 230.0, PLOT_22: 150.0, f"Y{YOUNG_ID}": 550.0}   # a donor's live trees per acre

TRANSFORM = Affine(CELL, 0, X0, 0, -CELL, Y0)
PUBLISHED, ADDED_BACK_YOUNG = 1, 4
YOUNG_ROWS, YOUNG_COLS = slice(18, 30), slice(0, 15)
UNKNOWN_ROWS, UNKNOWN_COLS = slice(0, 15), slice(25, 40)
WATER_ROWS, WATER_COLS = slice(24, 30), slice(30, 40)

VAT = pd.DataFrame({
    "Value": ["11", "22", "33"], "TM_ID": ["11", "22", "33"],
    "PLT_CN": [PLOT_11, PLOT_22, PLOT_33],
    "FORTYPCD": [161, 503, 161], "BALIVE": [80.0, 120.0, 90.0],
    "QMD": [8.0, 12.0, 9.0], "TPA_LIVE": [230.0, 150.0, 200.0],
})


def _tif(path, band, dtype, nodata):
    with rasterio.open(path, "w", driver="GTiff", height=ROWS, width=COLS, count=1, dtype=dtype,
                       crs=project_crs(), transform=TRANSFORM, nodata=nodata) as dst:
        dst.write(np.asarray(band, dtype=dtype), 1)


def _treemap():
    tm = np.full((ROWS, COLS), 22, dtype=np.uint32)
    tm[:15, :] = 11                       # the band across the county line
    tm[29, 35:40] = NODATA                # a few non-forest cells
    return tm


def _harris():
    harris = np.full((ROWS, COLS), 3, dtype=np.uint8)      # family
    harris[UNKNOWN_ROWS, UNKNOWN_COLS] = 0                  # unknown: scheduled, flagged
    harris[WATER_ROWS, WATER_COLS] = 2                      # water: masked
    harris[:, 60:] = 4                                      # corporate, in Beta
    return harris


def _fia_extract(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE PLOT (CN TEXT);
        CREATE TABLE COND (PLT_CN TEXT, CONDID INTEGER, COND_STATUS_CD INTEGER,
                           CONDPROP_UNADJ REAL, STDAGE REAL, FORTYPCD REAL, BALIVE REAL);
        CREATE TABLE TREE (CN TEXT, PLT_CN TEXT, CONDID INTEGER, STATUSCD INTEGER, SPCD INTEGER,
                           DIA REAL, HT REAL, ACTUALHT REAL, CR REAL, TPA_UNADJ REAL);
        CREATE TABLE FVS_STANDINIT_PLOT (STAND_CN TEXT, STAND_ID TEXT, VARIANT TEXT,
                                         INV_YEAR REAL, AGE REAL, LATITUDE REAL, SITE_INDEX REAL,
                                         BASAL_AREA_FACTOR REAL, INV_PLOT_SIZE REAL, BRK_DBH REAL,
                                         NUM_PLOTS REAL, NONSTK_PLOTS REAL);
        CREATE TABLE FVS_TREEINIT_PLOT (STAND_CN TEXT, STAND_ID TEXT, PLOT_ID REAL, TREE_ID REAL,
                                        TREE_COUNT REAL, HISTORY REAL, SPECIES TEXT,
                                        DIAMETER REAL, HT REAL, CRRATIO REAL);
    """)
    for cn, age, fortyp, ba, dia, tpa, start_age, lat, (tree_cn, spcd, ht, actual) in (
            (PLOT_11, 30, 161, 80.0, 8.0, 230.0, 28, 30.1, (TREE_11, 131, None, 55.0)),
            (PLOT_22, 60, 503, 120.0, 12.0, 150.0, 58, 30.2, (TREE_22, 611, 70.0, 68.0)),
            (PLOT_33, 45, 161, 90.0, 9.0, 200.0, 44, 30.3, ("100000000000331", 131, 60.0, 60.0))):
        con.execute("INSERT INTO PLOT VALUES (?)", (cn,))
        con.execute("INSERT INTO COND VALUES (?, 1, 1, 1.0, ?, ?, ?)", (cn, age, fortyp, ba))
        con.execute("INSERT INTO TREE VALUES (?, ?, 1, 1, ?, ?, ?, ?, 40, ?)",
                    (tree_cn, cn, spcd, dia, ht, actual, tpa))
        # FIA's design (subplot and microplot) and its own inventory year, 2009
        con.execute("INSERT INTO FVS_STANDINIT_PLOT VALUES (?, ?, 'SN', 2009, ?, ?, 70, "
                    "24, -24, 5, 4, 0)", (cn, f"FIA{cn}", start_age, lat))
        for tree in (1, 2):
            con.execute("INSERT INTO FVS_TREEINIT_PLOT VALUES (?, ?, 1, ?, 6.0, 1, '131', ?, 50, 40)",
                        (cn, f"FIA{cn}", tree, dia))
    con.commit()
    con.close()


def _hydrography(path):
    stream = LineString([(X0 + 600, Y0), (X0 + 600, Y0 - ROWS * CELL)])      # through Alpha
    pond = box(X0 + 240, Y0 - 150, X0 + 330, Y0 - 60)
    ephemeral = LineString([(X0 + 1800, Y0), (X0 + 1800, Y0 - ROWS * CELL)])  # through Beta
    gpd.GeoDataFrame({"fcode": [46006, 46007]}, geometry=[stream, ephemeral],
                     crs=project_crs()).to_file(path, layer="flowline", driver="GPKG")
    gpd.GeoDataFrame({"fcode": [39004]}, geometry=[pond],
                     crs=project_crs()).to_file(path, layer="waterbody", driver="GPKG")


def _counties(path):
    """The line steps at row 15: Alpha's lower half reaches column 44, so its box takes in Beta's cells."""
    step = box(X0 + 40 * CELL, Y0 - ROWS * CELL, X0 + 45 * CELL, Y0 - 15 * CELL)
    alpha = box(X0, Y0 - ROWS * CELL, X0 + 40 * CELL, Y0).union(step)
    beta = box(X0 + 40 * CELL, Y0 - ROWS * CELL, X0 + COLS * CELL, Y0).difference(step)
    frame = gpd.GeoDataFrame({"ADMIN_FIPS": [ALPHA, BETA], "NAME": ["Alpha", "Beta"],
                              "STATE": ["FL", "FL"], "STATE_FIPS": ["12", "12"]},
                             geometry=[alpha, beta], crs=project_crs())
    frame.to_crs(4269).to_file(path, driver="GPKG")       # the national layer is in degrees
    return frame


def _young_plots(repair, young_tm=None):
    young = repair / "young_plots"
    young.mkdir()
    tm = _treemap() if young_tm is None else young_tm
    if young_tm is None:
        tm[YOUNG_ROWS, YOUNG_COLS] = YOUNG_ID
    provenance = np.full((ROWS, COLS), PUBLISHED, dtype=np.uint8)
    provenance[YOUNG_ROWS, YOUNG_COLS] = ADDED_BACK_YOUNG
    _tif(young / "treemap2022_improved.tif", tm, "uint32", NODATA)
    _tif(young / "treemap2022_provenance.tif", provenance, "uint8", 255)
    pd.DataFrame({"Value": [YOUNG_ID], "TM_ID": [YOUNG_ID], "PLT_CN": [PLOT_44], "FORTYPCD": [161],
                  "BALIVE": [5.0], "QMD": [1.5], "TPA_LIVE": [600.0], "STAND_AGE_2022": [7],
                  "CUT_YEAR": [2014], "AGE_SOURCE": ["hansen"], "YOUNG_TM_ID": ["44"],
                  "YOUNG_PLT_CN": [PLOT_44], "NEIGHBOUR_TM_ID": ["33"],
                  "NEIGHBOUR_PLT_CN": [PLOT_33]}).to_csv(
        young / "treemap2022_improved_vat_extension.csv", index=False)
    pd.DataFrame({"TM_ID": [YOUNG_ID] * 4, "PLT_CN": [PLOT_44] * 4, "STATUSCD": [1, 1, 2, 1],
                  "TPA_UNADJ": [300.0, 250.0, 75.0, 0.0], "SPCD": [111, 121, 111, 111],
                  "DIA": [1.2, 1.8, 2.0, 0.5], "HT": [9.0, 12.0, 14.0, 4.0],
                  "CR": [60, 55, 0, 50]}).to_csv(
        young / "treemap2022_improved_tree_extension.csv", index=False)


@pytest.fixture
def world(tmp_path):
    """The raster-repair run, the region inputs and the county layer; ``run_dir`` is empty."""
    repair = tmp_path / "repair" / "aoi"
    repair.mkdir(parents=True)
    tm = _treemap()
    tm[YOUNG_ROWS, YOUNG_COLS] = 33                       # the neighbour plot's TM_ID
    provenance = np.full((ROWS, COLS), PUBLISHED, dtype=np.uint8)
    provenance[YOUNG_ROWS, YOUNG_COLS] = ADDED_BACK_YOUNG
    _tif(repair / "treemap2022_improved.tif", tm, "uint32", NODATA)
    _tif(repair / "treemap2022_provenance.tif", provenance, "uint8", 255)
    _tif(repair / "nwos2022_improved.tif", _harris(), "uint8", HARRIS_NODATA)
    (repair / "manifest.json").write_text(json.dumps({"counties": ["12001 Alpha", "12003 Beta"]}))
    _young_plots(repair)

    region = tmp_path / "region"
    region.mkdir()
    _fia_extract(region / "fia_extract.db")
    _hydrography(region / "hydrography.gpkg")
    counties = _counties(tmp_path / "counties.gpkg")
    return {"repair": repair, "region": region, "counties_path": tmp_path / "counties.gpkg",
            "counties": counties, "run_dir": tmp_path / "run"}


def _run(world, county=ALPHA, **kwargs):
    fips = [county] if isinstance(county, str) else county
    kwargs.setdefault("run_dir", world["run_dir"])
    return run_leto(repair=world["repair"], fips=fips, vat=VAT, counties=world["counties_path"],
                    region_dir=world["region"], **kwargs)


def _units(world):
    return gpd.read_file(world["run_dir"] / "management_units.gpkg", layer="units")


def _stands(world):
    return pd.read_csv(world["run_dir"] / "stands.csv", dtype={"county_fips": str, "PLT_CN": str,
                                                               "unit_id": str,
                                                               "representative_plot": str})


# ── settings ───────────────────────────────────────────────────────────────────────────


def test_florida_runs_the_maine_verified_ca_with_force_merge_and_its_first_pass_buffers():
    settings = load_settings(ALPHA)

    assert settings.region == "florida"
    for key, value in {"shared_edge_bonus": 0.2, "similar_merge_min_shared_edges": 2,
                       "maximum_iterations": 40, "convergence_threshold": 0.01,
                       "remaining_small_stand_action": "force_merge"}.items():
        assert settings.segmentation[key] == value, key
    first_pass = leto_riparian.FLORIDA_FIRST_PASS_RULES
    assert settings.buffer_rules.flowline == {
        **first_pass.flowline, 55800: ("Artificial Path (NHDArea stand-in)", 75.0)}
    assert settings.buffer_rules.waterbody == first_pass.waterbody
    assert settings.buffer_rules.area == first_pass.area
    assert settings.minimum_plot_weight == 0.05
    assert settings.speckle == SpeckleSettings(SpeckleAction.MERGE_DELETE, minimum_unit_acres=1.0)


def test_a_county_in_no_configured_region_fails_closed():
    with pytest.raises(ValueError, match="no region in .*leto.yaml for state FIPS '99'"):
        load_settings("99001")


def test_every_region_states_its_small_stand_action_under_upstreams_key():
    # Upstream falls back to `retain` when the key is unset; no region may rely on that.
    config = yaml.safe_load(CONFIG_PATH.read_text())
    for name, spec in config["regions"].items():
        assert spec["small_stands"]["remaining_small_stand_action"], name
    assert config["regions"]["florida"]["small_stands"]["remaining_small_stand_action"] == \
        "force_merge"


def _config_with_action(tmp_path, action):
    config = yaml.safe_load(CONFIG_PATH.read_text())
    config["regions"]["florida"]["small_stands"]["remaining_small_stand_action"] = action
    path = tmp_path / "leto.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def test_the_regional_action_is_normalised_as_upstream_does(tmp_path):
    settings = load_settings(ALPHA, _config_with_action(tmp_path, " Delete "))
    assert settings.segmentation["remaining_small_stand_action"] is SmallStandAction.DELETE


def test_an_unknown_regional_action_fails_closed_and_names_the_allowed_ones(tmp_path):
    with pytest.raises(ValueError, match="must be one of .*'force_merge'.*'retain'"):
        load_settings(ALPHA, _config_with_action(tmp_path, "drop"))


def test_a_regional_proximity_merge_takes_upstreams_limits_from_the_regional_config(tmp_path):
    settings = load_settings(ALPHA, _config_with_action(tmp_path, "proximity_merge"))

    assert settings.segmentation["remaining_small_stand_action"] is SmallStandAction.PROXIMITY_MERGE
    assert settings.proximity == ProximitySettings(
        maximum_distance_meters=60.0, maximum_combined_acres=300.0,
        match=(ProximityMatch.OWNER, ProximityMatch.FOREST_TYPE),
        unmatched=Unmatched.RETAIN, warning_chain_ratio=2.0)
    assert settings.record()["proximity"]["match"] == ["owner", "forest_type"]


def test_proximity_limits_are_read_from_the_regional_config(tmp_path):
    config = yaml.safe_load(CONFIG_PATH.read_text())
    config["regions"]["florida"]["small_stands"].update(
        remaining_small_stand_action="proximity_merge", maximum_distance_meters=90.0,
        maximum_combined_acres=50.0, require_same_forest_type=False,
        retain_when_no_candidate=False)
    path = tmp_path / "leto.yaml"
    path.write_text(yaml.safe_dump(config))

    proximity = load_settings(ALPHA, path).proximity
    assert (proximity.maximum_distance_meters, proximity.maximum_combined_acres) == (90.0, 50.0)
    assert proximity.match == (ProximityMatch.OWNER,)
    assert proximity.unmatched is Unmatched.ERROR


def test_a_proximity_flag_that_is_not_a_yaml_boolean_fails_closed(tmp_path):
    config = yaml.safe_load(CONFIG_PATH.read_text())
    config["regions"]["florida"]["small_stands"]["retain_when_no_candidate"] = "false"
    path = tmp_path / "leto.yaml"
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(ValueError, match="retain_when_no_candidate must be true or false"):
        load_settings(ALPHA, path)


def test_a_combined_cap_under_the_minimum_stand_size_fails_closed(tmp_path):
    config = yaml.safe_load(CONFIG_PATH.read_text())
    config["regions"]["florida"]["small_stands"].update(
        remaining_small_stand_action="proximity_merge", maximum_combined_acres=4.0)
    path = tmp_path / "leto.yaml"
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(ValueError, match="maximum_combined_acres"):
        load_settings(ALPHA, path)


# ── outputs ────────────────────────────────────────────────────────────────────────────


def test_stands_csv_passes_the_runners_reader_and_adds_county_origin_and_the_unknown_flag(world):
    _run(world)

    stands = read_stands(world["run_dir"])
    assert (stands["county_fips"] == ALPHA).all()           # the runner reads it as a string
    assert (stands["county"] == "Alpha").all()
    assert set(stands["origin"]) == {"treemap", "young_plot"}
    assert set(stands["unit_class"]) == {"managed", "riparian"}
    unknown = stands["owner_class"] == "unknown"
    assert unknown.any() and stands.loc[unknown, "unknown_ownership"].all()
    assert not stands.loc[~unknown, "unknown_ownership"].any()
    assert set(stands.loc[unknown, "OWN_CODE"]) == {0}
    assert not stands["OWN_CODE"].isin([1, 2]).any()       # Harris 1-2 are masked


def test_control_numbers_are_exact_and_the_neighbour_plot_is_never_joined_to_trees(world):
    _run(world)

    stands = _stands(world)
    assert PLOT_22 in set(stands["PLT_CN"])                 # 19 digits, intact
    assert PLOT_33 not in set(stands["PLT_CN"])
    assert set(stands["PLT_CN"]) <= {PLOT_11, PLOT_22, f"Y{YOUNG_ID}"}
    young = stands[stands["origin"] == "young_plot"]
    assert (young["PLT_CN"] == f"Y{YOUNG_ID}").all()
    assert (young["stand_age"] == 7).all()                 # its dated 2022 age


def test_a_treemap_units_stand_age_is_its_representative_plots_fvs_start_age(world):
    _run(world)

    stands = _stands(world)
    ages = stands[stands["origin"] == "treemap"].groupby("PLT_CN")["stand_age"].unique()
    assert {cn: list(a) for cn, a in ages.items()} == {PLOT_11: [28], PLOT_22: [58]}


@pytest.mark.parametrize("age", [None, -1])
def test_a_unit_without_a_usable_age_is_left_blank_and_its_acres_reported(world, age):
    with sqlite3.connect(world["region"] / "fia_extract.db") as con:
        con.execute("UPDATE FVS_STANDINIT_PLOT SET AGE = ? WHERE STAND_CN = ?", (age, PLOT_22))
    _run(world)

    stands = _stands(world)
    blank = stands["PLT_CN"] == PLOT_22
    assert stands.loc[blank, "stand_age"].isna().all()
    assert stands.loc[~blank, "stand_age"].notna().all()
    table = pd.read_csv(world["run_dir"] / "qa" / "leto" / "area_accounting.csv")
    assert table["no_age_acres"].item() == pytest.approx(stands.loc[blank, "acres"].sum(), abs=0.01)
    assert table["no_age_acres"].item() > 0


def test_the_units_layer_and_the_run_record_are_written(world):
    _run(world)

    units = _units(world)
    assert units.crs == project_crs()
    assert units["unit_id"].is_unique
    stands = _stands(world)
    managed_or_riparian = units[units["owner_class"] != "masked"]
    assert set(managed_or_riparian["unit_id"]) == set(stands["unit_id"])
    assert (units["owner_class"] == "masked").any()          # in the layer, not in stands.csv

    record = json.loads((world["run_dir"] / "leto.json").read_text())
    manifest = (world["repair"] / "manifest.json").read_bytes()
    assert record["repair"]["manifest_sha256"] == hashlib.sha256(manifest).hexdigest()
    florida = record["regions"]["florida"]
    assert florida["fia_extract"]["path"] == str((world["region"] / "fia_extract.db").resolve())
    assert len(florida["fia_extract"]["sha256"]) == 64
    assert florida["settings"]["segmentation"]["shared_edge_bonus"] == 0.2
    assert record["counties"][ALPHA]["region"] == "florida"
    assert florida["settings"]["speckle"] == {"action": "merge_delete", "minimum_unit_acres": 1.0}
    assert record["counties"][ALPHA]["status"] == "done"
    assert len(record["counties"][ALPHA]["input_sha256"]) == 64
    assert record["layout"] == "plot"


def test_the_plot_layout_writes_no_leto_layout_tables(world):
    _run(world)

    assert "DB_GROUP" not in _stands(world).columns
    assert not (world["run_dir"] / "fvs_groups").exists()
    assert not list((world["run_dir"] / "qa" / "leto").glob("imputation*"))
    with sqlite3.connect(world["run_dir"] / "FVS_Data.db") as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"FVS_StandInit_Plot", "FVS_TreeInit_Plot"}


def test_the_area_accounting_splits_unit_acres_into_managed_riparian_unknown_and_masked(world):
    _run(world)

    table = pd.read_csv(world["run_dir"] / "qa" / "leto" / "area_accounting.csv",
                        dtype={"county_fips": str})
    row = table.set_index("county_fips").loc[ALPHA]
    parts = ["managed_acres", "riparian_acres", "unknown_flagged_acres", "masked_acres"]
    assert all(row[p] > 0 for p in parts)
    assert row[parts].sum() == pytest.approx(row["unit_acres"], abs=0.01)
    assert row["forest_cells"] == 40 * ROWS + 5 * 15 - 5      # the step, less five nodata cells
    assert row["harris_nodata_cells"] == 0
    assert abs(row["drift_acres"]) <= 0.1
    stands = _stands(world)
    assert row["managed_acres"] + row["unknown_flagged_acres"] + row["riparian_acres"] == \
        pytest.approx(stands["acres"].sum(), abs=0.01)


def _with_owner_patch(world):
    """A 3 x 3 corporate patch inside Alpha's family land: a hard edge, so it stays under 5 ac."""
    harris = _harris()
    harris[5:8, 5:8] = 4
    _tif(world["repair"] / "nwos2022_improved.tif", harris, "uint8", HARRIS_NODATA)


def _with_action(action):
    settings = load_settings(ALPHA)
    return dataclasses.replace(settings, segmentation={**settings.segmentation,
                                                       "remaining_small_stand_action": action})


def _accounting(world):
    table = pd.read_csv(world["run_dir"] / "qa" / "leto" / "area_accounting.csv",
                        dtype={"county_fips": str})
    return table.set_index("county_fips").loc[ALPHA]


def test_delete_reports_its_deleted_acres_in_the_area_accounting(world):
    _with_owner_patch(world)
    _run(world, settings=_with_action(SmallStandAction.DELETE))

    row = _accounting(world)
    cell_acres = CELL * CELL / leto_riparian.SQUARE_METERS_PER_ACRE
    assert (row["deleted_small_stands"], row["deleted_small_stand_cells"]) == (1, 9)  # the patch
    assert row["deleted_small_stand_acres"] == \
        pytest.approx(row["deleted_small_stand_cells"] * cell_acres, abs=1e-3)
    assert abs(row["drift_acres"]) <= 0.1                 # units still equal parent stands
    assert row["parent_stand_acres"] + row["deleted_small_stand_acres"] == \
        pytest.approx(row["forest_cell_acres"], rel=0.01)
    with rasterio.open(world["run_dir"] / "leto" / ALPHA / "parent_stands.tif") as src:
        assert (src.read(1)[5:8, 5:8] == 0).all()        # the patch is no parent stand
    assert not _units(world).intersects(
        box(X0 + 5 * CELL + 1, Y0 - 8 * CELL + 1, X0 + 8 * CELL - 1, Y0 - 5 * CELL - 1)).any()


def test_force_merge_keeps_an_isolated_patch_and_deletes_nothing(world):
    _with_owner_patch(world)
    _run(world)

    row = _accounting(world)
    assert (row["deleted_small_stands"], row["deleted_small_stand_cells"],
            row["deleted_small_stand_acres"]) == (0, 0, 0)
    with rasterio.open(world["run_dir"] / "leto" / ALPHA / "parent_stands.tif") as src:
        assert (src.read(1)[5:8, 5:8] > 0).all()


def test_the_stage_logs_what_delete_removed(world, caplog):
    _with_owner_patch(world)
    with caplog.at_level("INFO", logger="artemis.leto"):
        _run(world, settings=_with_action(SmallStandAction.DELETE))
    acres = 9 * CELL * CELL / leto_riparian.SQUARE_METERS_PER_ACRE
    assert f"leto: county {ALPHA}: deleted 1 undersized stands, 9 forest cells ({acres:.2f} ac)" in \
        caplog.messages


def _fvs(world, table):
    with sqlite3.connect(world["run_dir"] / "FVS_Data.db") as con:
        frame = pd.read_sql(f"SELECT * FROM {table}", con)
    frame["STAND_CN"] = frame["STAND_CN"].astype(str)
    return frame


def test_fvs_data_holds_one_plot_layout_stand_per_distinct_representative_plot(world):
    _run(world)

    stand, trees = _fvs(world, "FVS_StandInit_Plot"), _fvs(world, "FVS_TreeInit_Plot")
    assert sorted(stand["STAND_CN"]) == sorted(set(_stands(world)["PLT_CN"]))
    assert sorted(stand["STAND_CN"]) == sorted([PLOT_11, PLOT_22, f"Y{YOUNG_ID}"])
    assert set(trees["STAND_CN"]) == set(stand["STAND_CN"])


def test_an_fia_plot_stand_is_its_extract_rows_anchored_to_2022(world):
    _run(world)

    stand = _fvs(world, "FVS_StandInit_Plot").set_index("STAND_CN")
    plot = stand.loc[PLOT_22]                                  # 19 digits, intact
    assert plot["INV_YEAR"] == 2022
    assert plot["STAND_ID"] == f"S{PLOT_22}"
    assert plot["AGE"] == 58 and plot["LATITUDE"] == 30.2
    assert (plot["BASAL_AREA_FACTOR"], plot["INV_PLOT_SIZE"], plot["NUM_PLOTS"]) == (24, -24, 4)
    trees = _fvs(world, "FVS_TreeInit_Plot")
    assert trees.loc[trees["STAND_CN"] == PLOT_22, "DIAMETER"].tolist() == [12.0, 12.0]


def test_a_young_plot_stand_has_its_neighbours_site_its_dated_age_and_letos_design(world):
    _run(world)

    young = _fvs(world, "FVS_StandInit_Plot").set_index("STAND_CN").loc[f"Y{YOUNG_ID}"]
    assert young["LATITUDE"] == 30.3                           # neighbour plot 33's site
    assert young["SITE_INDEX"] == 70
    assert young["AGE"] == 7 and young["INV_YEAR"] == 2022
    assert young["STAND_ID"] == f"SY{YOUNG_ID}"
    design = ["BASAL_AREA_FACTOR", "INV_PLOT_SIZE", "BRK_DBH", "NUM_PLOTS", "NONSTK_PLOTS"]
    assert young[design].tolist() == [-1, 1, 0, 1, 0]
    trees = _fvs(world, "FVS_TreeInit_Plot")
    trees = trees[trees["STAND_CN"] == f"Y{YOUNG_ID}"].sort_values("TREE_ID")
    assert trees["TREE_COUNT"].tolist() == [300.0, 250.0]      # TPA; dead and zero-TPA trees left out
    assert trees["SPECIES"].astype(str).tolist() == ["111", "121"]
    assert trees["DIAMETER"].tolist() == [1.2, 1.8]
    assert trees["HT"].tolist() == [9.0, 12.0]
    assert trees["CRRATIO"].tolist() == [60, 55]
    assert (trees["PLOT_ID"] == 1).all() and (trees["HISTORY"] == 1).all()


# ── proximity merge (#169) ─────────────────────────────────────────────────────────────


def _with_two_corporate_patches(world):
    """Two 3 x 3 corporate patches 30 m apart in Alpha's family land: small, same owner and type."""
    harris = _harris()
    harris[10:13, 3:6] = 4
    harris[10:13, 7:10] = 4
    _tif(world["repair"] / "nwos2022_improved.tif", harris, "uint8", HARRIS_NODATA)


def _proximity_settings():
    return _with_action(SmallStandAction.PROXIMITY_MERGE)


def _segments(world):
    return gpd.read_file(world["run_dir"] / "management_units.gpkg", layer="segments")


def _corporate_unit(world):
    units = _units(world)
    corporate = units[(units["county_fips"] == ALPHA) & (units["owner_class"] == "corporate")]
    assert len(corporate) == 1
    return corporate.iloc[0]


def test_proximity_merge_makes_one_multipart_unit_of_two_nearby_small_stands(world):
    _with_two_corporate_patches(world)
    _run(world, settings=_proximity_settings())

    unit = _corporate_unit(world)
    assert unit["PART_COUNT"] == 2 and unit.geometry.geom_type == "MultiPolygon"
    segments = _segments(world)
    parts = segments[segments["unit_id"] == unit["unit_id"]]
    assert sorted(parts["MU_PART"]) == [1, 2]
    assert parts["BIO_MU_ID"].nunique() == 1 and parts["SEG_ID"].nunique() == 2
    assert (parts.geometry.geom_type == "Polygon").all()
    assert unit["Acres"] == pytest.approx(parts["Acres"].sum())
    stands = _stands(world)
    assert (stands["unit_id"] == unit["unit_id"]).sum() == 1
    assert not stands["unit_id"].duplicated().any()


def test_the_units_layer_has_one_feature_per_unit_id_and_the_segments_cover_it(world):
    _with_two_corporate_patches(world)
    _run(world, settings=_proximity_settings())

    units, segments = _units(world), _segments(world)
    assert not units["unit_id"].duplicated().any()
    assert set(segments["unit_id"]) == set(units["unit_id"])
    by_unit = segments.groupby("unit_id")["Acres"].sum()
    assert np.allclose(units.set_index("unit_id")["Acres"], by_unit.reindex(units["unit_id"]))


def test_without_proximity_every_segment_is_its_own_unit(world):
    _with_two_corporate_patches(world)
    _run(world)

    units, segments = _units(world), _segments(world)
    assert len(units) == len(segments)
    assert (units["PART_COUNT"] == 1).all() and (segments["MU_PART"] == 1).all()
    assert (segments["unit_id"] == segments["leto_unit_id"]).sum() + \
        int(_speckle_qa(world)[["merged_same_owner", "merged_other_owner", "merged_riparian"]].sum()) \
        == len(segments)


def _proximity_qa(world):
    return pd.read_csv(world["run_dir"] / "qa" / "leto" / "proximity.csv",
                       dtype={"county_fips": str}).set_index("county_fips").loc[ALPHA]


def test_the_proximity_qa_reports_merges_distances_multipart_units_and_small_stands_left(world):
    _with_two_corporate_patches(world)
    _run(world, settings=_proximity_settings())

    qa = _proximity_qa(world)
    assert qa["small_stand_action"] == "proximity_merge"
    assert qa["proximity_merges"] >= 1
    assert 0.0 < qa["maximum_merge_distance_meters"] <= 60.0
    assert qa["multipart_management_units"] >= 1
    assert qa["small_stands_remaining"] >= 0
    for column in ("average_merge_distance_meters", "average_segments_per_multipart_unit",
                   "chain_flagged_units", "maximum_observed_span_meters"):
        assert column in qa.index
    merges = pd.read_csv(world["run_dir"] / "leto" / ALPHA / "qa" / "proximity_merges.csv")
    assert len(merges) == qa["proximity_merges"]
    assert {"SOURCE_SEG_ID", "TARGET_SEG_ID", "DISTANCE_METERS", "AGE_DIFFERENCE"} <= set(merges)


def test_without_proximity_the_qa_reports_no_merge_and_the_small_stands_left(world):
    _with_two_corporate_patches(world)
    _run(world, settings=_with_action(SmallStandAction.RETAIN))

    qa = _proximity_qa(world)
    assert qa["small_stand_action"] == "retain"
    assert (qa["proximity_merges"], qa["multipart_management_units"]) == (0, 0)
    assert qa["small_stands_remaining"] >= 2                      # the two patches at least


def test_a_proximity_run_keeps_its_acres(world):
    _with_two_corporate_patches(world)
    _run(world, settings=_proximity_settings())

    line = _accounting(world)
    assert abs(line["drift_acres"]) <= 0.1
    assert line["segments"] >= line["units"]


# ── invariants ─────────────────────────────────────────────────────────────────────────


def _with_edge_stream(world):
    """A perennial stream 30 m inside Alpha's west edge: its buffer leaves upland speckle strips."""
    path = world["region"] / "hydrography.gpkg"
    flowlines = gpd.read_file(path, layer="flowline")
    edge = LineString([(X0 + CELL, Y0), (X0 + CELL, Y0 - ROWS * CELL)])
    flowlines = pd.concat([flowlines, gpd.GeoDataFrame({"fcode": [46006]}, geometry=[edge],
                                                       crs=flowlines.crs)], ignore_index=True)
    flowlines.to_file(path, layer="flowline", driver="GPKG", mode="w")


def _speckle_qa(world):
    return pd.read_csv(world["run_dir"] / "qa" / "leto" / "speckle.csv",
                       dtype={"county_fips": str}).set_index("county_fips").loc[ALPHA]


def _with_isolated_cell(world):
    """One forest cell ringed by non-forest in Alpha: speckle with no neighbour to merge into."""
    for path in (world["repair"] / "treemap2022_improved.tif",
                 world["repair"] / "young_plots" / "treemap2022_improved.tif"):
        with rasterio.open(path) as src:
            tm = src.read(1)
        tm[4:9, 10:15] = NODATA
        tm[6, 12] = 11
        _tif(path, tm, "uint32", NODATA)


def test_by_default_speckle_merges_then_what_is_left_is_deleted(world):
    _with_edge_stream(world)
    _with_isolated_cell(world)
    _run(world)

    units, qa = _units(world), _speckle_qa(world)
    open_units = units[units["owner_class"] != "masked"]
    assert (open_units["Acres"] >= 1.0).all()                # no speckle left in the layer
    assert qa["units_after"] == len(units) < qa["units_before"]
    merged = sum(qa[f"merged_{step}"] for step in ("same_owner", "other_owner", "riparian"))
    assert merged + qa["speckle_deleted"] == qa["units_before"] - qa["units_after"]
    assert qa["speckle_kept"] == 0 and qa["speckle_deleted"] > 0


def test_deleted_speckle_is_its_own_line_in_the_area_accounting(world):
    _with_edge_stream(world)
    _with_isolated_cell(world)
    _run(world)

    line = pd.read_csv(world["run_dir"] / "qa" / "leto" / "area_accounting.csv",
                       dtype={"county_fips": str}).set_index("county_fips").loc[ALPHA]
    qa = _speckle_qa(world)
    assert line["deleted_speckle"] == qa["speckle_deleted"]
    assert line["deleted_speckle_acres"] == pytest.approx(qa["speckle_deleted_acres"], abs=1e-3)
    assert line["unit_acres"] + line["deleted_speckle_acres"] == pytest.approx(
        line["parent_stand_acres"], abs=0.1)
    assert abs(line["drift_acres"]) <= 0.1
    assert line["deleted_speckle"] >= 1
    assert not _units(world).intersects(box(X0 + 12 * CELL + 1, Y0 - 7 * CELL + 1,
                                            X0 + 13 * CELL - 1, Y0 - 6 * CELL - 1)).any()


def test_a_many_county_run_assembles_every_countys_speckle_qa(world):
    _run(world, county=[ALPHA, BETA])

    qa = pd.read_csv(world["run_dir"] / "qa" / "leto" / "speckle.csv", dtype={"county_fips": str})
    assert list(qa["county_fips"]) == [ALPHA, BETA]
    assert qa["units_after"].sum() == len(_units(world))


def test_keep_replays_letos_own_units(world):
    _with_edge_stream(world)
    keep = dataclasses.replace(load_settings(ALPHA),
                               speckle=SpeckleSettings(SpeckleAction.KEEP, minimum_unit_acres=1.0))
    _run(world, settings=keep)

    qa = _speckle_qa(world)
    assert qa["units_before"] == qa["units_after"] == len(_units(world))
    assert qa["speckle_kept"] == qa["speckle_before"] > 0


def test_a_young_parent_stand_has_a_lower_standardized_age_than_its_mature_neighbour():
    """The speckle cost sees add-back land as young: what keeps a young speckle on young land."""
    shape = (4, 8)
    seg = np.ones(shape, dtype=np.int32)
    seg[:, 4:] = 2                                          # parent 1 young, parent 2 mature
    age = np.where(seg == 1, 4.0, 35.0)
    grids = {"STDAGE": age, "BALIVE": np.where(seg == 1, 10.0, 110.0),
             "QMD": np.where(seg == 1, 2.0, 9.0), "TPA": np.where(seg == 1, 600.0, 200.0),
             "FORTYPCD": np.where(seg == 1, 161, 503).astype(float)}
    seg[3, 7] = 0                                           # a non-forest cell
    valid = seg > 0
    cells = CountyCells(treemap=np.zeros(shape, np.int64), harris=np.full(shape, 4),
                        harris_nodata=np.zeros(shape, bool), valid=valid,
                        added_back=seg == 1, transform=TRANSFORM, crs=project_crs())

    traits = parent_traits(seg, cells, grids, load_settings(ALPHA))

    assert list(traits.index) == [1, 2]
    assert traits.loc[1, "STDAGE"] < traits.loc[2, "STDAGE"]
    assert traits.loc[1, "BALIVE"] < traits.loc[2, "BALIVE"]
    assert list(traits["FORTYPCD"]) == [161, 503]


def _parent_acres(world, county):
    with rasterio.open(world["run_dir"] / "leto" / county / "parent_stands.tif") as src:
        seg, transform, crs = src.read(1), src.transform, src.crs
    parents = gpd.GeoDataFrame(
        [{"SEG_ID": int(v), "geometry": shape(g)}
         for g, v in features.shapes(seg, mask=seg > 0, transform=transform)], crs=crs)
    return float(leto_riparian.geodesic_acres(parents.dissolve(by="SEG_ID")).sum())


@pytest.mark.parametrize("county", [ALPHA, BETA])
def test_unit_acres_equal_parent_stand_acres_and_no_parent_crosses_the_county_line(world, county):
    _run(world, county)

    units = _units(world)
    assert units["Acres"].sum() == pytest.approx(_parent_acres(world, county), abs=0.1)
    line = world["counties"].set_index("ADMIN_FIPS").geometry[county]
    assert units.geometry.within(line.buffer(0.01)).all()


def test_riparian_area_never_sits_in_a_managed_unit(world):
    _run(world)

    units = _units(world)
    region = world["region"] / "hydrography.gpkg"
    footprint = leto_riparian.riparian_footprint(
        load_settings(ALPHA).buffer_rules, flowlines=gpd.read_file(region, layer="flowline"),
        waterbodies=gpd.read_file(region, layer="waterbody"))
    managed = units[units["unit_class"] == "managed"]
    assert managed.geometry.intersection(footprint).area.sum() < 1.0      # m², float noise only
    assert (units["unit_class"] == "riparian").any()


def test_a_county_without_a_buffered_stream_has_no_riparian_units(world):
    _run(world, BETA)

    assert set(_stands(world)["unit_class"]) == {"managed"}


# ── fail closed ────────────────────────────────────────────────────────────────────────


def test_add_back_pixels_without_young_plots_fail_closed(world):
    shutil.rmtree(world["repair"] / "young_plots")

    with pytest.raises(AddBackWithoutYoungPlots, match="young_plots"):
        _run(world)
    assert not (world["run_dir"] / "stands.csv").exists()


def test_a_county_without_add_back_needs_no_young_plots(world):
    shutil.rmtree(world["repair"] / "young_plots")

    _run(world, BETA)

    assert (world["run_dir"] / "stands.csv").exists()


def test_a_neighbour_id_left_on_an_add_back_pixel_fails_closed(world):
    shutil.rmtree(world["repair"] / "young_plots")
    tm = _treemap()
    tm[YOUNG_ROWS, YOUNG_COLS] = 33                     # young_plots/ that kept the neighbour ID
    _young_plots(world["repair"], young_tm=tm)

    with pytest.raises(NeighbourIdOnAddBack, match="33"):
        _run(world)


def test_a_young_plot_id_missing_from_the_young_plot_tables_fails_closed(world):
    ext = world["repair"] / "young_plots" / "treemap2022_improved_vat_extension.csv"
    pd.read_csv(ext).iloc[0:0].to_csv(ext, index=False)

    with pytest.raises(UnknownYoungPlotIds, match=str(YOUNG_ID)):
        _run(world)


def _delete_from_extract(world, table, cn):
    with sqlite3.connect(world["region"] / "fia_extract.db") as con:
        con.execute(f"DELETE FROM {table} WHERE STAND_CN = ?", (cn,))


@pytest.mark.parametrize("table", ["FVS_STANDINIT_PLOT", "FVS_TREEINIT_PLOT"])
def test_a_representative_plot_missing_from_the_extract_fails_closed(world, table):
    _delete_from_extract(world, table, PLOT_22)

    with pytest.raises(MissingRepresentativePlots, match=PLOT_22):
        _run(world)
    assert not (world["run_dir"] / "stands.csv").exists()
    assert not (world["run_dir"] / "FVS_Data.db").exists()


def test_a_representative_plot_with_two_standinit_rows_fails_closed(world):
    """stand_age and FVS's start row must be the same row; two rows leave that open."""
    with sqlite3.connect(world["region"] / "fia_extract.db") as con:
        con.execute("INSERT INTO FVS_STANDINIT_PLOT SELECT * FROM FVS_STANDINIT_PLOT "
                    "WHERE STAND_CN = ?", (PLOT_22,))

    with pytest.raises(AmbiguousStandInit, match=f"{PLOT_22}.*more than one"):
        _run(world)


def test_a_young_plots_neighbour_missing_from_the_extract_fails_closed(world):
    _delete_from_extract(world, "FVS_STANDINIT_PLOT", PLOT_33)

    with pytest.raises(MissingRepresentativePlots, match=f"neighbour plots .*{PLOT_33}"):
        _run(world)


def test_a_young_plot_id_without_live_trees_fails_closed(world):
    trees = world["repair"] / "young_plots" / "treemap2022_improved_tree_extension.csv"
    frame = pd.read_csv(trees)
    frame.assign(STATUSCD=2).to_csv(trees, index=False)

    with pytest.raises(UnknownYoungPlotIds, match=f"{YOUNG_ID}.*no live tree"):
        _run(world)


def test_a_folder_without_a_run_manifest_fails_closed(world):
    (world["repair"] / "manifest.json").unlink()

    with pytest.raises(FileNotFoundError, match="manifest.json"):
        _run(world)


def test_hydrography_without_a_flowline_layer_fails_closed(world):
    gpkg = world["region"] / "hydrography.gpkg"
    waterbody = gpd.read_file(gpkg, layer="waterbody")
    gpkg.unlink()
    waterbody.to_file(gpkg, layer="waterbody", driver="GPKG")

    with pytest.raises(ValueError, match="no flowline layer"):
        _run(world)


def test_a_fips_not_in_the_county_layer_fails_closed(world):
    with pytest.raises(UnknownCounty, match="12999"):
        _run(world, "12999")


# ── many counties: resume, workers, the layout ─────────────────────────────────────────


def _record(world, run_dir=None):
    return json.loads(((run_dir or world["run_dir"]) / "leto.json").read_text())


def _county_stands(world, county):
    return world["run_dir"] / "leto" / county / "stands.csv"


def test_without_a_county_list_the_stage_runs_every_county_the_raster_run_covers(world):
    result = _run(world, None)

    assert result.outcomes == {ALPHA: CountyOutcome.RAN, BETA: CountyOutcome.RAN}
    assert set(_stands(world)["county_fips"]) == {ALPHA, BETA}
    assert {c: e["status"] for c, e in _record(world)["counties"].items()} == \
        {ALPHA: "done", BETA: "done"}


def test_a_raster_run_whose_manifest_names_no_counties_needs_a_county_list(world):
    (world["repair"] / "manifest.json").write_text(json.dumps({"rule": "v4"}))

    with pytest.raises(NoCountiesInManifest, match="--county"):
        _run(world, None)


def test_the_run_level_outputs_are_assembled_from_every_finished_county(world):
    result = _run(world, [ALPHA, BETA])

    stands, units = read_stands(world["run_dir"]), _units(world)
    assert set(stands["county_fips"]) == {ALPHA, BETA}
    assert set(units["county_fips"]) == {ALPHA, BETA}
    assert stands["unit_id"].is_unique and units["unit_id"].is_unique
    for county in (ALPHA, BETA):
        own = pd.read_csv(_county_stands(world, county), dtype={"unit_id": str})
        assert set(own["unit_id"]) == set(stands.loc[stands["county_fips"] == county, "unit_id"])
    stand = _fvs(world, "FVS_StandInit_Plot")
    assert stand["STAND_CN"].is_unique                      # plot 11 is in both counties, once
    assert set(stand["STAND_CN"]) == set(stands["PLT_CN"])
    trees = _fvs(world, "FVS_TreeInit_Plot")
    assert (trees.groupby("STAND_CN").size() == 2).loc[[PLOT_11, PLOT_22]].all()
    table = pd.read_csv(world["run_dir"] / "qa" / "leto" / "area_accounting.csv",
                        dtype={"county_fips": str})
    assert table["county_fips"].tolist() == [ALPHA, BETA]
    assert table.set_index("county_fips")["units"].to_dict() == \
        units.groupby("county_fips").size().to_dict()
    assert result.accounting["county_fips"].tolist() == [ALPHA, BETA]


def test_a_rerun_skips_a_county_whose_inputs_match(world):
    _run(world)
    own = _county_stands(world, ALPHA)
    before, written = own.stat().st_mtime_ns, (world["run_dir"] / "stands.csv").read_text()

    result = _run(world)

    assert result.outcomes == {ALPHA: CountyOutcome.SKIPPED}
    assert own.stat().st_mtime_ns == before
    assert (world["run_dir"] / "stands.csv").read_text() == written


def test_widening_the_county_list_runs_only_the_new_county(world):
    _run(world)

    result = _run(world, [ALPHA, BETA])

    assert result.outcomes == {ALPHA: CountyOutcome.SKIPPED, BETA: CountyOutcome.RAN}
    assert set(_stands(world)["county_fips"]) == {ALPHA, BETA}


def test_a_county_finished_earlier_stays_in_the_run_level_outputs(world):
    _run(world, ALPHA)

    _run(world, BETA)

    assert set(_stands(world)["county_fips"]) == {ALPHA, BETA}


def test_a_changed_setting_redoes_the_county(world):
    _run(world)
    first = _record(world)["counties"][ALPHA]["input_sha256"]
    changed = dataclasses.replace(load_settings(ALPHA), minimum_plot_weight=0.2)

    result = _run(world, settings=changed)

    assert result.outcomes == {ALPHA: CountyOutcome.RAN}
    record = _record(world)
    assert record["counties"][ALPHA]["input_sha256"] != first
    assert record["regions"]["florida"]["settings"]["minimum_plot_weight"] == 0.2


def test_a_changed_input_redoes_the_county(world):
    _run(world)
    with sqlite3.connect(world["region"] / "fia_extract.db") as con:
        con.execute("UPDATE FVS_STANDINIT_PLOT SET AGE = 61 WHERE STAND_CN = ?", (PLOT_22,))

    result = _run(world)

    assert result.outcomes == {ALPHA: CountyOutcome.RAN}
    stands = _stands(world)
    assert (stands.loc[stands["PLT_CN"] == PLOT_22, "stand_age"] == 61).all()


def test_a_raster_regenerated_in_place_redoes_the_county(world):
    """The manifest records paths, not contents: a repaired raster rewritten under it still counts."""
    _run(world)
    harris = _harris()
    harris[:, :20] = 5                                     # another owner, same manifest
    _tif(world["repair"] / "nwos2022_improved.tif", harris, "uint8", HARRIS_NODATA)

    result = _run(world)

    assert result.outcomes == {ALPHA: CountyOutcome.RAN}


def test_a_county_finished_earlier_is_reported_as_kept(world):
    _run(world, ALPHA)

    result = _run(world, BETA)

    assert result.outcomes == {ALPHA: CountyOutcome.KEPT, BETA: CountyOutcome.RAN}


def test_a_county_finished_under_other_inputs_is_marked_stale_and_left_out(world):
    _run(world, [ALPHA, BETA])
    changed = dataclasses.replace(load_settings(ALPHA), minimum_plot_weight=0.2)

    _run(world, BETA, settings=changed)

    assert set(_stands(world)["county_fips"]) == {BETA}     # never mix settings
    assert set(_units(world)["county_fips"]) == {BETA}
    assert _record(world)["counties"][ALPHA]["status"] == "stale"


def test_a_failed_county_is_recorded_beside_the_finished_ones_and_nothing_is_assembled(world):
    shutil.rmtree(world["repair"] / "young_plots")           # Alpha has add-back pixels; Beta not

    with pytest.raises(AddBackWithoutYoungPlots):
        _run(world, [ALPHA, BETA])

    counties = _record(world)["counties"]
    assert counties[ALPHA]["status"] == "failed"
    assert "young_plots" in counties[ALPHA]["error"]
    assert counties[BETA]["status"] == "done"
    assert not (world["run_dir"] / "stands.csv").exists()
    assert not (world["run_dir"] / "FVS_Data.db").exists()


def _gpkg(path):
    frame = gpd.read_file(path, layer="units")
    return pd.DataFrame(frame.drop(columns="geometry")).assign(wkt=frame.geometry.to_wkt())


def test_workers_run_counties_in_parallel_and_the_outputs_equal_a_serial_run(world, tmp_path):
    serial, parallel = tmp_path / "serial", tmp_path / "parallel"

    _run(world, None, run_dir=serial, workers=1)
    _run(world, None, run_dir=parallel, workers=2)

    for name in ("stands.csv", "qa/leto/area_accounting.csv", "leto/12001/donor_weights.csv",
                 "leto/12003/donor_weights.csv"):
        assert (parallel / name).read_text() == (serial / name).read_text(), name
    pd.testing.assert_frame_equal(_gpkg(parallel / "management_units.gpkg"),
                                  _gpkg(serial / "management_units.gpkg"))
    for table in ("FVS_StandInit_Plot", "FVS_TreeInit_Plot"):
        frames = []
        for run in (serial, parallel):
            with sqlite3.connect(run / "FVS_Data.db") as con:
                frames.append(pd.read_sql(f"SELECT * FROM {table}", con))
        pd.testing.assert_frame_equal(frames[1], frames[0])
    hashes = [{c: e["input_sha256"] for c, e in _record(world, run)["counties"].items()}
              for run in (serial, parallel)]
    assert hashes[0] == hashes[1]


def _county_inventory(path, age):
    stand = pd.DataFrame({"STAND_CN": [PLOT_22], "AGE": [age]})
    tree = pd.DataFrame({"STAND_CN": [PLOT_22, PLOT_22], "TREE_ID": [1, 2]})
    PlotInventory(stand, tree).write(path)
    return path


def test_a_plot_in_two_counties_is_one_stand_with_one_counties_trees(tmp_path):
    paths = [_county_inventory(tmp_path / f"{c}.db", 58) for c in (ALPHA, BETA)]

    inventory = assemble_inventory(paths)

    assert inventory.stand["STAND_CN"].tolist() == [PLOT_22]       # 19 digits, intact
    assert inventory.tree["TREE_ID"].tolist() == [1, 2]


def test_a_plot_with_different_standinit_rows_in_two_counties_fails_closed(tmp_path):
    paths = [_county_inventory(tmp_path / "a.db", 58), _county_inventory(tmp_path / "b.db", 59)]

    with pytest.raises(ConflictingPlotRows, match=PLOT_22):
        assemble_inventory(paths)


def test_library_with_another_layout_than_the_run_was_built_with_fails_closed(world, tmp_path):
    _run(world)

    with pytest.raises(LayoutMismatch, match="built with the plot layout.*--layout leto"):
        main(["library", str(world["run_dir"]), "--inventory", "leto", "--workers", "1",
              "--fvs-bin", str(tmp_path / "never-run")])
    assert not (world["run_dir"] / "library.csv").exists()


# ── the command ────────────────────────────────────────────────────────────────────────


def test_main_leto_runs_one_county(world, tmp_path):
    vat = tmp_path / "vat.csv"                         # read_vat wants all 27 VAT columns
    VAT.reindex(columns=VAT_COLUMNS, fill_value=0).to_csv(vat, index=False)

    main(["leto", str(world["run_dir"]), "--repair", str(world["repair"]), "--county", ALPHA,
          "--region-dir", str(world["region"]), "--vat", str(vat),
          "--counties", str(world["counties_path"])])

    assert len(read_stands(world["run_dir"])) > 0


def test_main_leto_takes_a_county_list_and_workers(world, tmp_path):
    vat = tmp_path / "vat.csv"
    VAT.reindex(columns=VAT_COLUMNS, fill_value=0).to_csv(vat, index=False)

    main(["leto", str(world["run_dir"]), "--repair", str(world["repair"]), "--county", ALPHA, BETA,
          "--workers", "2", "--region-dir", str(world["region"]), "--vat", str(vat),
          "--counties", str(world["counties_path"])])

    assert set(read_stands(world["run_dir"])["county_fips"]) == {ALPHA, BETA}


def test_main_leto_defaults_to_the_declared_sources_under_a_data_root(world, tmp_path):
    # --data-root stands in for the drive (/mnt/d), as region_prep's does, e.g. data/r2_cache:
    # the VAT and the county layer sit where config/data_paths.yaml declares them under it.
    import pyogrio

    root = tmp_path / "mirror"
    vat = root / "TreeMap-2022" / "Data" / "TreeMap2022_CONUS.tif.vat.dbf"
    vat.parent.mkdir(parents=True)
    pyogrio.write_dataframe(VAT.reindex(columns=VAT_COLUMNS, fill_value=0), vat,
                            driver="ESRI Shapefile")
    counties = root / "county_p010g.shp_nt00934" / "countyp010g.shp"
    counties.parent.mkdir(parents=True)
    world["counties"].to_file(counties)

    main(["leto", str(world["run_dir"]), "--repair", str(world["repair"]), "--county", ALPHA,
          "--region-dir", str(world["region"]), "--data-root", str(root)])

    assert len(read_stands(world["run_dir"])) > 0


def test_a_leto_run_folder_feeds_the_library_stage(world, tmp_path):
    """The chain: ``leto`` then ``library`` with a fake FVS binary, every keyfile on a stand it wrote."""
    _run(world)
    fake = tmp_path / "FVSsn"
    fake.write_text("#!/bin/sh\nexit 2\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    main(["library", str(world["run_dir"]), "--workers", "1", "--fvs-bin", str(fake)])

    library = pd.read_csv(world["run_dir"] / "library.csv", dtype={"PLT_CN": str, "unit_id": str})
    assert set(library["unit_id"]) == set(_stands(world)["unit_id"])
    written = set(_fvs(world, "FVS_StandInit_Plot")["STAND_CN"])
    keyfiles = list((world["run_dir"] / "keyfiles").glob("*.key"))
    assert len(keyfiles) == library["run_hash"].nunique()
    for keyfile in keyfiles:
        lines = keyfile.read_text().splitlines()
        assert lines[lines.index("StandCN") + 1] in written
        assert "FROM FVS_StandInit_Plot WHERE" in keyfile.read_text()


# ── the LETO layout (#170) ─────────────────────────────────────────────────────────────


def _run_leto(world, county=ALPHA, **kwargs):
    kwargs.setdefault("species_lookup", SPECIES)
    return _run(world, county, layout=InventoryLayout.LETO, **kwargs)


def _qa(world, name):
    return pd.read_csv(world["run_dir"] / "qa" / "leto" / name,
                       dtype={"county_fips": str, "STAND_ID": str, "PLT_CN": str})


def test_a_units_stand_age_mean_is_its_parent_stands_ages_weighted_by_acres():
    # upstream read_stand_polygons; leto_inventory.prepare_stands rounds it half to even
    units = pd.DataFrame({"unit_id": ["a", "b", "c", "c", "d"], "SEG_ID": [1, 2, 1, 2, 3],
                          "Acres": [1.0, 1.0, 3.0, 1.0, 2.0]})
    parent_age = pd.Series({1: 6.5, 2: 7.5})                 # parent 3 has no age

    means = unit_stdage_means(units, parent_age)

    assert means.loc[["a", "b", "c"]].to_dict() == {"a": 6.5, "b": 7.5, "c": 6.75}
    assert np.isnan(means.loc["d"])


def test_the_leto_layout_writes_one_fvs_stand_per_scheduled_unit_under_letos_table_names(world):
    _run_leto(world)

    stands = read_stands(world["run_dir"])
    assert (stands["PLT_CN"] == stands["unit_id"]).all()      # each unit is its own FVS stand
    assert set(_stands(world)["representative_plot"]) <= {PLOT_11, PLOT_22, f"Y{YOUNG_ID}"}
    stand = _fvs(world, "FVS_StandInit")
    assert sorted(stand["STAND_CN"]) == sorted(stands["unit_id"])
    assert (stand["STAND_ID"] == stand["STAND_CN"]).all()
    assert set(stand["INV_YEAR"]) == {2022} and set(stand["VARIANT"]) == {"SN"}
    assert set(stand["STATE"]) == {12} and set(stand["GROUPS"]) == {"All_Stands"}
    design = ["BASAL_AREA_FACTOR", "INV_PLOT_SIZE", "BRK_DBH", "NUM_PLOTS", "NONSTK_PLOTS"]
    assert stand[design].drop_duplicates().values.tolist() == [[-1, 1, 0, 1, 0]]
    assert set(_fvs(world, "FVS_TreeInit")["STAND_CN"]) == set(stand["STAND_CN"])
    with sqlite3.connect(world["run_dir"] / "FVS_Data.db") as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"FVS_StandInit", "FVS_TreeInit"}
    assert json.loads((world["run_dir"] / "leto.json").read_text())["layout"] == "leto"


def test_a_units_trees_are_its_donors_live_trees_at_tpa_times_weight(world):
    _run_leto(world)

    stands = _stands(world)
    weights = read_id_csv(world["run_dir"] / "leto" / ALPHA / "donor_weights.csv")
    weights["TPA"] = weights["PLT_CN"].map(LIVE_TPA)
    expected = (weights["WEIGHT"] * weights["TPA"]).groupby(weights["SEG_ID"].astype(int)).sum()
    trees = _fvs(world, "FVS_TreeInit").groupby("STAND_CN")["TREE_COUNT"].sum()
    for unit in stands.itertuples():
        assert trees[unit.unit_id] == pytest.approx(expected[unit.parent_stand]), unit.unit_id


def test_species_go_through_the_crosswalk_and_height_falls_back_to_actualht(world):
    _run_leto(world)

    trees = _fvs(world, "FVS_TreeInit")
    by_diameter = trees.groupby("DIAMETER")[["SPECIES", "HT", "CRRATIO"]].first()
    assert by_diameter.loc[8.0].tolist() == ["LP", 55.0, 40]      # plot 11: no HT, ACTUALHT 55
    assert by_diameter.loc[12.0].tolist() == ["SU", 70.0, 40]     # plot 22: HT kept
    assert by_diameter.loc[1.2].tolist() == ["SA", 9.0, 60]       # the young plot's own trees
    assert by_diameter.loc[1.8].tolist() == ["LL", 12.0, 55]
    assert 9.0 not in set(trees["DIAMETER"])                      # neighbour plot 33: never joined


def test_a_leto_units_stand_age_is_its_parent_stands_age_mean_rounded_half_to_even(world):
    _run_leto(world)

    cells = clip_county(world["repair"], read_county(world["counties_path"], ALPHA))
    ages = np.vectorize({11: 30.0, 22: 60.0, YOUNG_ID: 7.0}.get)(cells.treemap[cells.valid])
    with rasterio.open(world["run_dir"] / "leto" / ALPHA / "parent_stands.tif") as src:
        seg = src.read(1)[cells.valid]
    expected = pd.Series(ages).groupby(seg).mean().round()
    stands = _stands(world)
    assert stands["stand_age"].tolist() == expected.loc[stands["parent_stand"]].tolist()
    start = _fvs(world, "FVS_StandInit").set_index("STAND_CN")["AGE"]
    assert stands["stand_age"].tolist() == start.loc[stands["unit_id"]].tolist()


def test_a_unit_whose_donors_have_no_mapped_live_tree_takes_its_nearest_units_trees(world):
    _run_leto(world, species_lookup={k: v for k, v in SPECIES.items() if k != "611"})

    diagnostics = _qa(world, "imputation_diagnostics.csv")
    assert len(diagnostics) > 0 and (diagnostics["county_fips"] == ALPHA).all()
    assert set(diagnostics["FAILURE_REASON"]) == {"SPECIES_MAPPING_FAILURE"}
    trees = _fvs(world, "FVS_TreeInit")
    imputed = trees[trees["STAND_CN"].isin(diagnostics["STAND_ID"])]
    assert set(imputed["SPECIES"]) <= {"LP", "SA", "LL"}          # a direct unit's trees
    assert set(trees["STAND_CN"]) == set(_stands(world)["unit_id"])
    summary = _qa(world, "imputation_summary.csv")
    overall = summary[summary["SECTION"] == "OVERALL"].iloc[0]
    assert overall["IMPUTED_MUS"] == len(diagnostics)
    missing = _qa(world, "missing_species.csv")
    assert missing["Species_FIA"].tolist() == [611]
    by_stand = _qa(world, "missing_species_by_stand.csv")
    assert set(by_stand["STAND_ID"]) <= set(_stands(world)["unit_id"])


def test_every_species_mapping_leaves_the_qa_tables_empty_but_written(world):
    _run_leto(world)

    assert _qa(world, "imputation_diagnostics.csv").empty
    assert _qa(world, "missing_species.csv").empty
    assert len(_qa(world, "imputation_summary.csv")) > 0


def test_a_county_with_no_usable_tree_at_all_fails_closed(world):
    with pytest.raises(ValueError, match="No runnable stands"):
        _run_leto(world, species_lookup={})


def test_stands_keep_their_db_group_and_per_group_databases_are_written_only_when_asked(world):
    _run_leto(world)

    stands = _stands(world)
    riparian = stands["unit_class"] == "riparian"
    assert (stands.loc[riparian, "DB_GROUP"] == "Riparian").all()
    assert (stands.loc[~riparian, "DB_GROUP"] == stands.loc[~riparian, "owner_class"]).all()
    assert not (world["run_dir"] / "fvs_groups").exists()

    settings = load_settings(ALPHA)
    grouped = dataclasses.replace(settings, database_mode=DatabaseMode.GROUPED)
    _run_leto(world, settings=grouped)

    groups = world["run_dir"] / "fvs_groups"
    names = sorted(p.name for p in groups.glob("*.db"))
    assert names == sorted(f"FVS_Inventory_{g}.db" for g in set(_stands(world)["DB_GROUP"]))
    with sqlite3.connect(groups / "FVS_Inventory_Riparian.db") as con:
        ids = set(pd.read_sql("SELECT STAND_CN FROM FVS_StandInit", con)["STAND_CN"])
    assert ids == set(stands.loc[riparian, "unit_id"])
    assert (world["run_dir"] / "FVS_Data.db").exists()         # the combined one, always


def test_florida_writes_only_the_combined_database_by_default():
    assert load_settings(ALPHA).database_mode is DatabaseMode.COMBINED


def test_the_leto_layout_assembles_every_countys_units_and_qa(world):
    _run_leto(world, [ALPHA, BETA])

    stands = _stands(world)
    assert set(stands["county_fips"]) == {ALPHA, BETA}
    assert sorted(_fvs(world, "FVS_StandInit")["STAND_CN"]) == sorted(stands["unit_id"])
    summary = _qa(world, "imputation_summary.csv")
    assert set(summary["county_fips"]) == {ALPHA, BETA}


def test_changing_the_layout_redoes_the_county(world):
    _run(world)
    result = _run_leto(world)

    assert result.outcomes == {ALPHA: CountyOutcome.RAN}
    assert len(_fvs(world, "FVS_StandInit")) == len(_stands(world))


def test_a_changed_crosswalk_redoes_the_county(world):
    _run_leto(world)
    assert _run_leto(world).outcomes == {ALPHA: CountyOutcome.SKIPPED}

    result = _run_leto(world, species_lookup={**SPECIES, "131": "SA"})

    assert result.outcomes == {ALPHA: CountyOutcome.RAN}


def test_the_leto_layout_needs_a_species_crosswalk(world):
    with pytest.raises(ValueError, match="species crosswalk"):
        _run_leto(world, species_lookup=None)


def test_library_on_a_leto_layout_run_with_the_plot_layout_fails_closed(world, tmp_path):
    _run_leto(world)

    with pytest.raises(LayoutMismatch, match="built with the leto layout"):
        main(["library", str(world["run_dir"]), "--workers", "1",
              "--fvs-bin", str(tmp_path / "never-run")])


def _crosswalk(path):
    pd.DataFrame({"FIA CODE": list(SPECIES), "SN_Mapped_To": list(SPECIES.values())}).to_excel(
        path, sheet_name="EasternSpeciesTranslator", index=False)
    return path


def test_main_leto_layout_reads_the_species_crosswalk(world, tmp_path):
    vat = tmp_path / "vat.csv"
    VAT.reindex(columns=VAT_COLUMNS, fill_value=0).to_csv(vat, index=False)

    main(["leto", str(world["run_dir"]), "--repair", str(world["repair"]), "--county", ALPHA,
          "--layout", "leto", "--species-crosswalk", str(_crosswalk(tmp_path / "xw.xlsx")),
          "--region-dir", str(world["region"]), "--vat", str(vat),
          "--counties", str(world["counties_path"])])

    assert set(_fvs(world, "FVS_TreeInit")["SPECIES"]) == {"LP", "SU", "SA", "LL"}


def test_library_layout_leto_runs_on_a_leto_layout_run_folder(world, tmp_path):
    """The chain: ``leto --layout leto`` then ``library --layout leto`` with a fake FVS binary."""
    _run_leto(world)
    fake = tmp_path / "FVSsn"
    fake.write_text("#!/bin/sh\nexit 2\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    main(["library", str(world["run_dir"]), "--layout", "leto", "--workers", "1",
          "--fvs-bin", str(fake)])

    library = pd.read_csv(world["run_dir"] / "library.csv", dtype={"PLT_CN": str, "unit_id": str})
    assert set(library["unit_id"]) == set(_stands(world)["unit_id"])
    written = set(_fvs(world, "FVS_StandInit")["STAND_CN"])
    keyfiles = list((world["run_dir"] / "keyfiles").glob("*.key"))
    assert len(keyfiles) == library["run_hash"].nunique() > 0
    for keyfile in keyfiles:
        text = keyfile.read_text()
        lines = text.splitlines()
        assert lines[lines.index("StandCN") + 1] in written
        assert "FROM FVS_StandInit WHERE" in text and "FROM FVS_TreeInit WHERE" in text
