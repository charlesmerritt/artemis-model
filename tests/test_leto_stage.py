"""The ``leto`` stage, one county: repaired rasters to management units and ``stands.csv`` (#163).

The fixture is a tiny raster-repair run folder over two counties side by side, Alpha (12001)
and Beta (12003). A band of plot 11 runs across the county line, so a whole-window
segmentation would make a parent stand that crosses it. Alpha holds an add-back patch: the
run's own raster stamps it with neighbour plot 33, and ``young_plots/`` gives it young-plot
ID 1,000,000. A perennial stream and a pond run through Alpha; Beta has only an ephemeral
stream, which is never buffered.
"""

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
from rasterio import features
from rasterio.transform import Affine
from shapely.geometry import LineString, box, shape

from pipeline import leto_riparian
from pipeline.leto_stage import (
    AddBackWithoutYoungPlots,
    AmbiguousStandInit,
    MissingRepresentativePlots,
    NeighbourIdOnAddBack,
    UnknownCounty,
    UnknownYoungPlotIds,
    load_settings,
    run_leto,
)
from pipeline.run import main, read_stands
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
        CREATE TABLE TREE (PLT_CN TEXT, CONDID INTEGER, STATUSCD INTEGER, DIA REAL, TPA_UNADJ REAL);
        CREATE TABLE FVS_STANDINIT_PLOT (STAND_CN TEXT, STAND_ID TEXT, VARIANT TEXT,
                                         INV_YEAR REAL, AGE REAL, LATITUDE REAL, SITE_INDEX REAL,
                                         BASAL_AREA_FACTOR REAL, INV_PLOT_SIZE REAL, BRK_DBH REAL,
                                         NUM_PLOTS REAL, NONSTK_PLOTS REAL);
        CREATE TABLE FVS_TREEINIT_PLOT (STAND_CN TEXT, STAND_ID TEXT, PLOT_ID REAL, TREE_ID REAL,
                                        TREE_COUNT REAL, HISTORY REAL, SPECIES TEXT,
                                        DIAMETER REAL, HT REAL, CRRATIO REAL);
    """)
    for cn, age, fortyp, ba, dia, tpa, start_age, lat in (
            (PLOT_11, 30, 161, 80.0, 8.0, 230.0, 28, 30.1),
            (PLOT_22, 60, 503, 120.0, 12.0, 150.0, 58, 30.2),
            (PLOT_33, 45, 161, 90.0, 9.0, 200.0, 44, 30.3)):
        con.execute("INSERT INTO PLOT VALUES (?)", (cn,))
        con.execute("INSERT INTO COND VALUES (?, 1, 1, 1.0, ?, ?, ?)", (cn, age, fortyp, ba))
        con.execute("INSERT INTO TREE VALUES (?, 1, 1, ?, ?)", (cn, dia, tpa))
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


def _run(world, county=ALPHA):
    return run_leto(world["run_dir"], repair=world["repair"], county=county, vat=VAT,
                    counties=world["counties_path"], region_dir=world["region"])


def _units(world):
    return gpd.read_file(world["run_dir"] / "management_units.gpkg", layer="units")


def _stands(world):
    return pd.read_csv(world["run_dir"] / "stands.csv", dtype={"county_fips": str, "PLT_CN": str,
                                                               "unit_id": str})


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


def test_a_county_in_no_configured_region_fails_closed():
    with pytest.raises(ValueError, match="no region in .*leto.yaml for state FIPS '99'"):
        load_settings("99001")


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
    assert record["fia_extract"]["path"] == str((world["region"] / "fia_extract.db").resolve())
    assert len(record["fia_extract"]["sha256"]) == 64
    assert record["settings"]["segmentation"]["shared_edge_bonus"] == 0.2
    assert record["settings"]["region"] == "florida"
    assert record["counties"][ALPHA]["status"] == "done"


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


# ── invariants ─────────────────────────────────────────────────────────────────────────


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


# ── the command ────────────────────────────────────────────────────────────────────────


def test_main_leto_runs_one_county(world, tmp_path):
    vat = tmp_path / "vat.csv"                         # read_vat wants all 27 VAT columns
    VAT.reindex(columns=VAT_COLUMNS, fill_value=0).to_csv(vat, index=False)

    main(["leto", str(world["run_dir"]), "--repair", str(world["repair"]), "--county", ALPHA,
          "--region-dir", str(world["region"]), "--vat", str(vat),
          "--counties", str(world["counties_path"])])

    assert len(read_stands(world["run_dir"])) > 0


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
