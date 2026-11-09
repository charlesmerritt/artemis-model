"""Region inputs: the FIA extract from the full FIADB, and staged hydrography (#162)."""

import hashlib
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import Affine

from pipeline.ids import read_id_csv
from pipeline.s1_initial_state.region_prep import (
    HYDROGRAPHY_PAD_M,
    LayerSource,
    PlotCoverage,
    UnknownTreeMapValues,
    main,
    stage_hydrography,
    state_bounds,
    write_fia_extract,
)
from pipeline.spatial_ref import project_crs

NODATA = 4294967295
CN_19 = "1234567890123456789"   # a 19-digit control number: a float64 would round it

# The synthetic FIADB's plots. 11 and 22 are mapped by the runs below; 33 is not.
PLOT_11, PLOT_22, PLOT_33 = "100000000000011", CN_19, "100000000000033"
PLOT_55, PLOT_66, PLOT_77 = "100000000000055", "100000000000066", "100000000000077"


def _fiadb(path, plots=(PLOT_11, PLOT_22, PLOT_33), *, dead_only=(), no_fvs=()):
    """A tiny FIADB: the five extracted tables, under the source's TEXT control numbers.

    Each plot has one condition, one live tree, one dead tree, a StandInit row and a
    TreeInit row per tree; a ``dead_only`` plot has no live tree, a ``no_fvs`` plot no FVS rows.
    """
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE "PLOT" ("CN" TEXT, "STATECD" INTEGER, "INVYR" INTEGER);
        CREATE TABLE "COND" ("CN" TEXT, "PLT_CN" TEXT, "CONDID" INTEGER, "COND_STATUS_CD" INTEGER,
                             "CONDPROP_UNADJ" REAL, "STDAGE" REAL, "FORTYPCD" REAL, "BALIVE" REAL);
        CREATE TABLE "TREE" ("CN" TEXT, "PLT_CN" TEXT, "CONDID" INTEGER, "STATUSCD" INTEGER,
                             "SPCD" INTEGER, "DIA" REAL, "TPA_UNADJ" REAL);
        CREATE TABLE "FVS_STANDINIT_PLOT" ("STAND_CN" TEXT, "STAND_ID" TEXT, "AGE" REAL);
        CREATE TABLE "FVS_TREEINIT_PLOT" ("STAND_CN" TEXT, "TREE_CN" TEXT, "SPECIES" TEXT,
                                          "DIAMETER" REAL, "TREE_COUNT" REAL, "HISTORY" INTEGER);
        CREATE TABLE "REF_FIADB_VERSION" ("VERSION" TEXT, "CREATED_DATE" TIMESTAMP);
        INSERT INTO REF_FIADB_VERSION VALUES ('FIADB_1.9.0.02', '2022-06-10 20:20:07'),
                                             ('FIADB_1.9.3.01', '2025-02-11 14:58:55');
    """)
    for n, plt_cn in enumerate(plots):
        live, dead = f"{plt_cn}1", f"{plt_cn}2"
        con.execute("INSERT INTO PLOT VALUES (?, 12, 2019)", (plt_cn,))
        con.execute("INSERT INTO COND VALUES (?, ?, 1, 1, 1.0, ?, 161, 80.0)", (f"{plt_cn}9", plt_cn, 20 + n))
        if plt_cn not in dead_only:
            con.execute("INSERT INTO TREE VALUES (?, ?, 1, 1, 131, 9.5, 6.018)", (live, plt_cn))
        con.execute("INSERT INTO TREE VALUES (?, ?, 1, 2, 131, 7.0, 6.018)", (dead, plt_cn))
        if plt_cn in no_fvs:
            continue
        con.execute("INSERT INTO FVS_STANDINIT_PLOT VALUES (?, ?, ?)", (plt_cn, f"S{n}", 20 + n))
        trees = ((dead, 6),) if plt_cn in dead_only else ((live, 1), (dead, 6))
        for tree_cn, history in trees:
            con.execute("INSERT INTO FVS_TREEINIT_PLOT VALUES (?, ?, '131', 9.5, 6.018, ?)",
                        (plt_cn, tree_cn, history))
    con.commit()
    con.close()
    return path


def _vat(rows):
    """TreeMap's VAT as :func:`add_back_ids.read_vat` returns it: IDs as exact strings."""
    return pd.DataFrame(rows, columns=["TM_ID", "PLT_CN"]).assign(Value=lambda f: f["TM_ID"])


VAT = _vat([("11", PLOT_11), ("22", PLOT_22), ("33", PLOT_33)])


PUBLISHED, ADDED_BACK, ADDED_BACK_YOUNG = 1, 2, 4   # TreeMapProvenance


def _run(path, values, provenance=None, **tiling):
    """A raster-repair run folder: its improved TreeMap raster and provenance raster.

    By default every pixel is published, except a young-plot ID's (added back young).
    """
    path.mkdir(parents=True)
    values = np.asarray(values, dtype=np.uint32)
    if provenance is None:
        provenance = np.where(values >= 1_000_000, ADDED_BACK_YOUNG, PUBLISHED)
    for name, band, dtype, nodata in (("treemap2022_improved.tif", values, "uint32", NODATA),
                                      ("treemap2022_provenance.tif", provenance, "uint8", 255)):
        with rasterio.open(path / name, "w", driver="GTiff", height=values.shape[0],
                           width=values.shape[1], count=1, dtype=dtype, crs="EPSG:5070",
                           transform=Affine(30, 0, 0, 0, -30, 0), nodata=nodata, **tiling) as dst:
            dst.write(np.asarray(band, dtype=dtype), 1)
    return path


def _rows(db, sql):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_the_extract_holds_every_table_row_of_the_plots_the_run_maps_and_no_others(tmp_path):
    fiadb = _fiadb(tmp_path / "fiadb.db")
    run = _run(tmp_path / "v4" / "aoi", [[11, 11, NODATA, 22]])

    write_fia_extract(tmp_path / "region", fiadb=fiadb, runs=[run], vat=VAT)

    db = tmp_path / "region" / "fia_extract.db"
    assert sorted(r[0] for r in _rows(db, "SELECT CN FROM PLOT")) == sorted([PLOT_11, PLOT_22])
    for table, key, rows_per_plot in (("COND", "PLT_CN", 1), ("TREE", "PLT_CN", 2),
                                      ("FVS_STANDINIT_PLOT", "STAND_CN", 1),
                                      ("FVS_TREEINIT_PLOT", "STAND_CN", 2)):
        keys = [r[0] for r in _rows(db, f"SELECT {key} FROM {table}")]
        assert sorted(keys) == sorted([PLOT_11, PLOT_22] * rows_per_plot), table


def test_the_extract_covers_every_run_and_leaves_out_young_plot_ids(tmp_path):
    # A young-plot ID (1,000,000 up, ADR 0003) is no TreeMap value: its trees come from the
    # run's young_plots/, so it names no plot to extract.
    fiadb = _fiadb(tmp_path / "fiadb.db")
    fl5 = _run(tmp_path / "v4" / "aoi_5county" / "young_plots", [[11, 1_000_000, 1_000_001]])
    statewide = _run(tmp_path / "v5" / "fl_statewide", [[22, 22]])

    write_fia_extract(tmp_path / "region", fiadb=fiadb, runs=[fl5, statewide], vat=VAT)

    plots = _rows(tmp_path / "region" / "fia_extract.db", "SELECT CN FROM PLOT")
    assert sorted(r[0] for r in plots) == sorted([PLOT_11, PLOT_22])


def test_a_value_neither_treemap_nor_young_plot_fails_closed_and_writes_nothing(tmp_path):
    fiadb = _fiadb(tmp_path / "fiadb.db")
    run = _run(tmp_path / "v5" / "fl_statewide", [[11, 44, 44, 0]])

    with pytest.raises(UnknownTreeMapValues, match=r"fl_statewide.*: 0 \(1 px\), 44 \(2 px\)"):
        write_fia_extract(tmp_path / "region", fiadb=fiadb, runs=[run], vat=VAT)
    assert not (tmp_path / "region" / "fia_extract.db").exists()


def test_the_missing_table_lists_each_plot_absent_from_the_fiadb_or_without_live_trees(tmp_path):
    # 55 is in TreeMap but not in the FIADB; 66 has only a dead tree; 77 has live trees but
    # no FVS StandInit row, so the plot layout could not run it. 11 is complete: not listed.
    fiadb = _fiadb(tmp_path / "fiadb.db", plots=(PLOT_11, PLOT_66, PLOT_77),
                   dead_only=(PLOT_66,), no_fvs=(PLOT_77,))
    vat = _vat([("11", PLOT_11), ("55", PLOT_55), ("66", PLOT_66), ("77", PLOT_77)])
    fl5 = _run(tmp_path / "improved-rasters-evt2022-gate-v4" / "aoi_5county", [[11, 55, 66]])
    statewide = _run(tmp_path / "v5" / "fl_statewide", [[55, 77]])

    write_fia_extract(tmp_path / "region", fiadb=fiadb, runs=[fl5, statewide], vat=vat)

    missing = read_id_csv(tmp_path / "region" / "fia_extract_missing.csv")
    assert missing.to_dict("records") == [
        {"PLT_CN": PLOT_55, "TM_ID": "55", "status": PlotCoverage.NOT_IN_FIADB, "plot_rows": 0,
         "live_trees": 0, "fvs_stand_rows": 0, "fvs_tree_rows": 0,
         "runs": "improved-rasters-evt2022-gate-v4/aoi_5county; v5/fl_statewide"},
        {"PLT_CN": PLOT_66, "TM_ID": "66", "status": PlotCoverage.NO_LIVE_TREES, "plot_rows": 1,
         "live_trees": 0, "fvs_stand_rows": 1, "fvs_tree_rows": 1,
         "runs": "improved-rasters-evt2022-gate-v4/aoi_5county"},
        {"PLT_CN": PLOT_77, "TM_ID": "77", "status": PlotCoverage.NO_FVS_STAND, "plot_rows": 1,
         "live_trees": 1, "fvs_stand_rows": 0, "fvs_tree_rows": 0, "runs": "v5/fl_statewide"},
    ]


def test_a_19_digit_control_number_round_trips_through_the_extract_exactly(tmp_path):
    # PLOT_22 is 19 digits and its tree CNs 20; a float64 keeps 15-17. CN_19_MISSING is in
    # TreeMap but not the FIADB, so it reaches the missing table instead.
    from pipeline.s1_initial_state.data_sources import load_leto_attribute_sources

    cn_19_missing = "9876543210987654321"
    fiadb = _fiadb(tmp_path / "fiadb.db")
    vat = _vat([("11", PLOT_11), ("22", PLOT_22), ("88", cn_19_missing)])
    run = _run(tmp_path / "v5" / "fl_statewide", [[22, 88]])

    write_fia_extract(tmp_path / "region", fiadb=fiadb, runs=[run], vat=vat)

    db = tmp_path / "region" / "fia_extract.db"
    assert _rows(db, "SELECT CN, typeof(CN) FROM PLOT") == [(CN_19, "text")]
    assert sorted(_rows(db, "SELECT CN, PLT_CN FROM TREE")) == [(CN_19 + "1", CN_19), (CN_19 + "2", CN_19)]
    assert sorted(_rows(db, "SELECT STAND_CN, TREE_CN FROM FVS_TREEINIT_PLOT")) == [
        (CN_19, CN_19 + "1"), (CN_19, CN_19 + "2")]
    cond, trees = load_leto_attribute_sources(db, [CN_19])   # the LETO stage's FIA reader
    assert cond["PLT_CN"].tolist() == [CN_19] and sorted(trees["PLT_CN"]) == [CN_19, CN_19]
    missing = read_id_csv(tmp_path / "region" / "fia_extract_missing.csv")
    assert missing["PLT_CN"].tolist() == [cn_19_missing]
    assert f"{cn_19_missing},88," in (tmp_path / "region" / "fia_extract_missing.csv").read_text()


def test_the_manifest_records_the_fiadb_each_run_and_what_the_extract_holds(tmp_path):
    fiadb = _fiadb(tmp_path / "fiadb.db", plots=(PLOT_11, PLOT_22, PLOT_66), dead_only=(PLOT_66,))
    vat = _vat([("11", PLOT_11), ("22", PLOT_22), ("55", PLOT_55), ("66", PLOT_66)])
    fl5 = _run(tmp_path / "v4" / "aoi_5county" / "young_plots", [[11, 11, 55, 1_000_000, 1_000_000]])
    statewide = _run(tmp_path / "v5" / "fl_statewide", [[22, 66, 66, NODATA]])
    (statewide / "manifest.json").write_text('{"recipe": "v5"}\n')

    manifest = write_fia_extract(tmp_path / "region", fiadb=fiadb, runs=[fl5, statewide], vat=vat)

    assert json.loads((tmp_path / "region" / "fia_extract.json").read_text()) == manifest
    assert manifest["fiadb"]["path"] == str(fiadb.resolve())
    assert manifest["fiadb"]["version"] == "FIADB_1.9.3.01"   # the latest release it carries
    by_label = {run["label"]: run for run in manifest["runs"]}
    assert by_label["aoi_5county/young_plots"] == {
        "label": "aoi_5county/young_plots", "path": str(fl5.resolve()), "manifest_sha256": None,
        "treemap_values": 2, "plots": 2, "neighbour_only_plots": 0, "add_back_pixels": 2,
        "young_plot_ids_excluded": 1, "young_plot_pixels": 2,
        "plots_by_status": {"complete": 1, "not_in_fiadb": 1},
        "pixels_by_status": {"complete": 2, "not_in_fiadb": 1}}
    assert by_label["v5/fl_statewide"]["manifest_sha256"] == (
        "57d6b2e11941ddb4390edae87fade4b05e9c11a38e8d7a55a60ffba3a1fe5b87")
    assert by_label["v5/fl_statewide"]["plots_by_status"] == {"complete": 1, "no_live_trees": 1}
    assert manifest["plots"] == 4
    assert manifest["plots_by_status"] == {"complete": 2, "no_live_trees": 1, "not_in_fiadb": 1}
    assert manifest["tables"] == {"PLOT": 3, "COND": 3, "TREE": 5, "FVS_STANDINIT_PLOT": 3,
                                  "FVS_TREEINIT_PLOT": 5}
    db = (tmp_path / "region" / "fia_extract.db").read_bytes()
    assert manifest["extract_sha256"] == hashlib.sha256(db).hexdigest()


def test_a_rerun_keeps_the_extract_until_its_inputs_change(tmp_path):
    fiadb = _fiadb(tmp_path / "fiadb.db")
    fl5 = _run(tmp_path / "v4" / "aoi_5county", [[11]])
    region = tmp_path / "region"
    first = write_fia_extract(region, fiadb=fiadb, runs=[fl5], vat=VAT)
    built = (region / "fia_extract.db").stat().st_mtime_ns

    assert write_fia_extract(region, fiadb=fiadb, runs=[fl5], vat=VAT) == first
    assert (region / "fia_extract.db").stat().st_mtime_ns == built   # the FIADB was not read again

    statewide = _run(tmp_path / "v5" / "fl_statewide", [[22]])   # a new run, a new plot: rebuilt
    second = write_fia_extract(region, fiadb=fiadb, runs=[fl5, statewide], vat=VAT)
    assert second["plots"] == 2
    assert sorted(r[0] for r in _rows(region / "fia_extract.db", "SELECT CN FROM PLOT")) == sorted(
        [PLOT_11, PLOT_22])
    assert not list(region.glob("*.partial.*"))



# ── hydrography ─────────────────────────────────────────────────────────────────────────

GAINESVILLE, BANGOR = (-82.30, 29.65), (-68.77, 44.80)   # lon/lat: inside and outside Florida


def _to_project(lon, lat):
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:4269", project_crs(), always_xy=True).transform(lon, lat)


def _florida_box():
    """A project-CRS box of 20 km around Gainesville: the region in these tests."""
    x, y = _to_project(*GAINESVILLE)
    return (x - 10_000, y - 10_000, x + 10_000, y + 10_000)


def _hydro_source(path):
    """NHD-like layers in NAD83 degrees, as NHDPlus ships them: one feature in the region,
    one in Maine. The waterbody layer spells its FCode the NHD way."""
    import geopandas as gpd
    from shapely.geometry import LineString, Point

    (lon, lat), (far_lon, far_lat) = GAINESVILLE, BANGOR
    # The third line is an L round the region's north-east corner (about 0.10 deg east and
    # 0.09 deg north of Gainesville): its envelope overlaps the region, the line never enters it.
    corner = [(lon + 0.05, lat + 0.15), (lon + 0.15, lat + 0.15), (lon + 0.15, lat + 0.05)]
    flow = gpd.GeoDataFrame({"fcode": [46006, 46003, 46000], "GlobalID": ["a", "b", "c"]},
                            crs="EPSG:4269",
                            geometry=[LineString([(lon, lat), (lon + 0.01, lat)]),
                                      LineString([(far_lon, far_lat), (far_lon + 0.01, far_lat)]),
                                      LineString(corner)])
    water = gpd.GeoDataFrame({"FCode": [39004, 39004], "COUNT": [1, 1]}, crs="EPSG:4269",
                             geometry=[Point(lon, lat + 0.02).buffer(0.002),
                                       Point(far_lon, far_lat).buffer(0.002)])
    flow.to_file(path, layer="nhdflowline_fl")
    water.to_file(path, layer="NHDWaterbody_DissolveBoundaries1")
    return LayerSource(path, "nhdflowline_fl"), LayerSource(path, "NHDWaterbody_DissolveBoundaries1")


def test_the_regions_flowlines_and_waterbodies_are_staged_in_the_project_crs(tmp_path):
    import geopandas as gpd

    flowlines, waterbodies = _hydro_source(tmp_path / "nhd.gpkg")

    stage_hydrography(tmp_path / "region", flowlines=flowlines, waterbodies=waterbodies,
                      bounds=_florida_box())

    out = tmp_path / "region" / "hydrography.gpkg"
    flow = gpd.read_file(out, layer="flowline")
    water = gpd.read_file(out, layer="waterbody")
    assert flow.crs == project_crs() and water.crs == project_crs()
    assert flow["fcode"].tolist() == [46006] and water["fcode"].tolist() == [39004]   # Maine's dropped
    assert list(flow.columns) == ["fcode", "geometry"] and list(water.columns) == ["fcode", "geometry"]
    start = flow.geometry.iloc[0].coords[0]
    assert start == pytest.approx(_to_project(*GAINESVILLE), abs=0.01)   # metres, not degrees


def test_the_staged_layers_feed_the_riparian_split_as_they_are(tmp_path):
    import geopandas as gpd

    from pipeline import leto_riparian

    flowlines, waterbodies = _hydro_source(tmp_path / "nhd.gpkg")
    stage_hydrography(tmp_path / "region", flowlines=flowlines, waterbodies=waterbodies,
                      bounds=_florida_box())

    out = tmp_path / "region" / "hydrography.gpkg"
    footprint = leto_riparian.riparian_footprint(
        leto_riparian.FLORIDA_FIRST_PASS_RULES, flowlines=gpd.read_file(out, layer="flowline"),
        waterbodies=gpd.read_file(out, layer="waterbody"))
    # Worked by hand: the perennial stream (46006) is 0.01 deg of longitude, ~967 m, buffered
    # 75 ft (22.86 m) each side with round caps: 967 x 45.72 + pi x 22.86^2 ~ 45,850 m^2. The
    # perennial pond (39004), a 0.002 deg disc, is a 222 x 193 m ellipse in metres; buffered
    # it holds itself: pi x 244.9 x 215.9 ~ 166,050 m^2. Together ~211,900 m^2.
    assert footprint.area == pytest.approx(211_900, rel=0.01)


def _counties(path):
    """Three county polygons, as the national county layer has them: two Florida, one Georgia."""
    import geopandas as gpd
    from shapely.geometry import box

    (lon, lat) = GAINESVILLE
    gpd.GeoDataFrame(
        {"ADMIN_FIPS": ["12001", "12003", "13049"], "STATE": ["FL", "FL", "GA"],
         "STATE_FIPS": ["12", "12", "13"], "NAME": ["Alachua", "Baker", "Charlton"]},
        geometry=[box(lon - 0.2, lat - 0.2, lon, lat), box(lon, lat, lon + 0.2, lat + 0.2),
                  box(lon, lat + 0.5, lon + 0.2, lat + 0.7)], crs="EPSG:4269").to_file(path)
    return path


def test_a_states_bounds_are_its_counties_in_the_project_crs_padded(tmp_path):
    counties = _counties(tmp_path / "counties.shp")
    (lon, lat) = GAINESVILLE
    # The two Florida counties' corners: Alachua south-west of Gainesville, Baker north-east.
    corners = [(-0.2, -0.2), (-0.2, 0), (0, -0.2), (0, 0), (0.2, 0), (0, 0.2), (0.2, 0.2)]
    xs, ys = zip(*(_to_project(lon + dx, lat + dy) for dx, dy in corners))

    for state in ("FL", "12"):   # postal code or state FIPS
        minx, miny, maxx, maxy = state_bounds(counties, state)
        # The box holds every Florida corner, a pad beyond it; Georgia's county is out.
        assert minx <= min(xs) - HYDROGRAPHY_PAD_M and maxx >= max(xs) + HYDROGRAPHY_PAD_M
        assert miny <= min(ys) - HYDROGRAPHY_PAD_M and maxy >= max(ys) + HYDROGRAPHY_PAD_M
        assert maxy < _to_project(lon, lat + 0.5)[1]


def test_a_state_with_no_county_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="no county of state 'ME'"):
        state_bounds(_counties(tmp_path / "counties.shp"), "ME")


def test_the_hydrography_manifest_records_each_source_and_a_rerun_keeps_the_layers(tmp_path):
    flowlines, waterbodies = _hydro_source(tmp_path / "nhd.gpkg")
    region = tmp_path / "region"
    first = stage_hydrography(region, flowlines=flowlines, waterbodies=waterbodies,
                              bounds=_florida_box())

    assert json.loads((region / "hydrography.json").read_text()) == first
    assert first["crs"] == project_crs()
    assert first["layers"]["flowline"] == {"path": str(flowlines.path.resolve()),
                                           "layer": "nhdflowline_fl", "source_crs": "EPSG:4269",
                                           "features": 1}
    assert first["layers"]["waterbody"]["features"] == 1
    built = (region / "hydrography.gpkg").stat().st_mtime_ns
    assert stage_hydrography(region, flowlines=flowlines, waterbodies=waterbodies,
                             bounds=_florida_box()) == first
    assert (region / "hydrography.gpkg").stat().st_mtime_ns == built

    x, y = _to_project(*BANGOR)   # a region around Maine's features instead: restaged
    maine = stage_hydrography(region, flowlines=flowlines, waterbodies=waterbodies,
                              bounds=(x - 5_000, y - 5_000, x + 5_000, y + 5_000))
    assert maine["layers"]["flowline"]["features"] == 1 and maine["bounds"] != first["bounds"]
    assert not list(region.glob("*.partial.gpkg"))



# ── the command ─────────────────────────────────────────────────────────────────────────


def _vat_dbf(path, rows):
    """TreeMap's VAT as a .dbf, all 27 columns: numbers as floats, PLT_CN as text."""
    import pyogrio

    from pipeline.s1_initial_state.add_back_ids import VAT_COLUMNS

    frame = pd.DataFrame([{**dict.fromkeys(VAT_COLUMNS, 1.0), "Value": float(tm_id),
                           "TM_ID": float(tm_id), "PLT_CN": plt_cn} for tm_id, plt_cn in rows])
    pyogrio.write_dataframe(frame, path, driver="ESRI Shapefile")
    return path


def test_the_command_builds_both_region_inputs_from_the_declared_kinds_of_source(tmp_path):
    import geopandas as gpd

    fiadb = _fiadb(tmp_path / "fiadb.db")
    # TreeMap's VAT stores Value and TM_ID as numbers; a 19-digit PLT_CN only survives as text.
    vat_dbf = _vat_dbf(tmp_path / "TreeMap2022_CONUS.tif.vat.dbf", [(11, PLOT_11), (22, PLOT_22)])
    fl5 = _run(tmp_path / "v4" / "aoi_5county", [[11]])
    statewide = _run(tmp_path / "v5" / "fl_statewide", [[22]])
    flowlines, waterbodies = _hydro_source(tmp_path / "nhd.gpkg")
    region = tmp_path / "regions" / "fl"

    main(["fia", "--region-dir", str(region), "--repair", str(fl5), "--repair", str(statewide),
          "--fiadb", str(fiadb), "--vat", str(vat_dbf)])
    main(["hydrography", "--region-dir", str(region), "--state", "FL",
          "--counties", str(_counties(tmp_path / "counties.shp")),
          "--flowlines", str(flowlines.path), "--flowline-layer", flowlines.layer,
          "--waterbodies", str(waterbodies.path), "--waterbody-layer", waterbodies.layer])

    assert sorted(r[0] for r in _rows(region / "fia_extract.db", "SELECT CN FROM PLOT")) == sorted(
        [PLOT_11, PLOT_22])
    assert [run["label"] for run in json.loads((region / "fia_extract.json").read_text())["runs"]] == [
        "v4/aoi_5county", "v5/fl_statewide"]
    # Florida here is the two county boxes: Gainesville's stream, and the L inside Baker County.
    assert gpd.read_file(region / "hydrography.gpkg", layer="flowline")["fcode"].tolist() == [46006, 46000]


# ── inputs it cannot trust ──────────────────────────────────────────────────────────────


def test_a_fiadb_without_an_extracted_table_fails_closed_naming_it(tmp_path):
    fiadb = _fiadb(tmp_path / "fiadb.db")
    con = sqlite3.connect(fiadb)
    con.execute("DROP TABLE FVS_TREEINIT_PLOT")
    con.close()
    run = _run(tmp_path / "v5" / "fl_statewide", [[11]])

    with pytest.raises(ValueError, match="no FVS_TREEINIT_PLOT table"):
        write_fia_extract(tmp_path / "region", fiadb=fiadb, runs=[run], vat=VAT)
    assert not (tmp_path / "region" / "fia_extract.db").exists()


def test_an_extract_of_no_run_is_refused(tmp_path):
    with pytest.raises(ValueError, match="at least one raster-repair run"):
        write_fia_extract(tmp_path / "region", fiadb=_fiadb(tmp_path / "fiadb.db"), runs=[], vat=VAT)


def test_a_state_that_is_not_a_code_is_refused_before_it_reaches_the_query(tmp_path):
    with pytest.raises(ValueError, match="postal code or FIPS"):
        state_bounds(_counties(tmp_path / "counties.shp"), "FL' OR '1'='1")



def test_pixels_are_counted_once_across_a_rasters_blocks(tmp_path):
    # 40 x 40 in 16 x 16 tiles: nine blocks. Plot 11 fills the top 30 rows, 22 the rest.
    values = np.full((40, 40), 22, dtype=np.uint32)
    values[:30] = 11
    run = _run(tmp_path / "v5" / "fl_statewide", values, tiled=True, blockxsize=16, blockysize=16)

    manifest = write_fia_extract(tmp_path / "region", fiadb=_fiadb(tmp_path / "fiadb.db"),
                                 runs=[run], vat=VAT)

    assert manifest["runs"][0]["pixels_by_status"] == {"complete": 1600}
    assert manifest["runs"][0]["treemap_values"] == 2


def test_an_extract_removed_by_hand_or_a_new_fiadb_is_rebuilt(tmp_path):
    run = _run(tmp_path / "v5" / "fl_statewide", [[11, 22]])
    region = tmp_path / "region"
    first = write_fia_extract(region, fiadb=_fiadb(tmp_path / "fiadb.db"), runs=[run], vat=VAT)

    (region / "fia_extract.db").unlink()   # the manifest stays behind
    assert write_fia_extract(region, fiadb=tmp_path / "fiadb.db", runs=[run], vat=VAT) == first
    assert (region / "fia_extract.db").exists()

    newer = _fiadb(tmp_path / "fiadb_newer.db", plots=(PLOT_11,))   # a new release lost plot 22
    second = write_fia_extract(region, fiadb=newer, runs=[run], vat=VAT)
    assert second["fiadb"]["path"] == str(newer.resolve())
    assert second["plots_by_status"] == {"complete": 1, "not_in_fiadb": 1}


def test_an_edited_source_restages_the_hydrography_into_a_new_region_folder(tmp_path):
    flowlines, waterbodies = _hydro_source(tmp_path / "nhd.gpkg")
    region = tmp_path / "regions" / "fl"   # neither folder exists yet
    stage_hydrography(region, flowlines=flowlines, waterbodies=waterbodies, bounds=_florida_box())

    import geopandas as gpd
    flow = gpd.read_file(flowlines.path, layer=flowlines.layer)
    flow.loc[0, "fcode"] = 46003   # the source's stream reclassified as intermittent
    flow.to_file(flowlines.path, layer=flowlines.layer)
    again = stage_hydrography(region, flowlines=flowlines, waterbodies=waterbodies,
                              bounds=_florida_box())

    assert again["layers"]["flowline"]["features"] == 1
    assert gpd.read_file(region / "hydrography.gpkg", layer="flowline")["fcode"].tolist() == [46003]


@pytest.mark.filterwarnings("ignore:File .* has GPKG application_id:RuntimeWarning")
def test_the_command_defaults_to_the_declared_sources_under_a_data_root(tmp_path):
    # --data-root stands in for the drive: each source sits where config/data_paths.yaml
    # declares it, relative to /mnt/d.
    import shutil

    import geopandas as gpd

    root = tmp_path / "mirror"
    fiadb = root / "SQLite_FIADB_ENTIRE" / "SQLite_FIADB_ENTIRE.db"
    fiadb.parent.mkdir(parents=True)
    _fiadb(fiadb)
    vat = root / "TreeMap-2022" / "Data" / "TreeMap2022_CONUS.tif.vat.dbf"
    vat.parent.mkdir(parents=True)
    _vat_dbf(vat, [(11, PLOT_11)])
    counties = root / "county_p010g.shp_nt00934" / "countyp010g.shp"
    counties.parent.mkdir(parents=True)
    _counties(counties)
    flowlines, waterbodies = _hydro_source(tmp_path / "nhd.gpkg")
    streams = root / "US SE Streams - FINAL" / "US SE Streams - FINAL" / "Streams By State"
    lakes = root / "US SE Waterbodies Final" / "US SE Streams 10.20.2023" / "US SE Streams"
    for folder, name in ((streams, "nhdplus_epasnapshot2022_fl.gdb"), (lakes, "US SE Streams.gdb")):
        folder.mkdir(parents=True)
        shutil.copy(flowlines.path, folder / name)   # GDAL reads a GeoPackage by its content
    run = _run(tmp_path / "v5" / "fl_statewide", [[11]])
    region = tmp_path / "regions" / "fl"

    main(["--data-root", str(root), "fia", "--region-dir", str(region), "--repair", str(run)])
    main(["--data-root", str(root), "hydrography", "--region-dir", str(region), "--state", "FL"])

    extract = json.loads((region / "fia_extract.json").read_text())
    assert extract["fiadb"]["path"] == str(fiadb.resolve()) and extract["plots"] == 1
    layers = json.loads((region / "hydrography.json").read_text())["layers"]
    assert layers["flowline"]["path"] == str((streams / "nhdplus_epasnapshot2022_fl.gdb").resolve())
    assert layers["waterbody"]["layer"] == "NHDWaterbody_DissolveBoundaries1"
    assert gpd.read_file(region / "hydrography.gpkg", layer="waterbody")["fcode"].tolist() == [39004]



def test_an_add_back_pixels_neighbour_plot_is_extracted_but_never_counted_as_its_trees(tmp_path):
    # A run's own raster stamps add-back pixels (provenance 2 and 4) with their neighbour plot's
    # TM_ID, for forest type and species mix only. 11 is mapped by a published pixel and is the
    # neighbour of one add-back pixel; 22 is only ever a neighbour. Both are extracted (a young
    # plot's stand may take its site from one), but no add-back pixel counts as 22's or 11's.
    run = _run(tmp_path / "v4" / "aoi_5county", [[11, 11, 22, 22]],
               provenance=[[PUBLISHED, ADDED_BACK_YOUNG, ADDED_BACK, ADDED_BACK_YOUNG]])

    manifest = write_fia_extract(tmp_path / "region", fiadb=_fiadb(tmp_path / "fiadb.db"),
                                 runs=[run], vat=VAT)

    assert manifest["plots"] == 2
    summary = manifest["runs"][0]
    assert (summary["plots"], summary["neighbour_only_plots"], summary["add_back_pixels"]) == (1, 1, 3)
    assert summary["pixels_by_status"] == {"complete": 1}
    assert summary["plots_by_status"] == {"complete": 1}


def test_a_feature_without_a_usable_fcode_fails_closed(tmp_path):
    # leto_riparian buffers by FCode: a feature without one would silently get no buffer.
    import geopandas as gpd

    flowlines, waterbodies = _hydro_source(tmp_path / "nhd.gpkg")
    flow = gpd.read_file(flowlines.path, layer=flowlines.layer)
    flow["fcode"] = flow["fcode"].astype("string")
    flow.loc[0, "fcode"] = "perennial"
    flow.to_file(flowlines.path, layer=flowlines.layer)

    with pytest.raises(ValueError, match=r"1 feature.* no usable FCode: perennial"):
        stage_hydrography(tmp_path / "region", flowlines=flowlines, waterbodies=waterbodies,
                          bounds=_florida_box())
    assert not (tmp_path / "region" / "hydrography.gpkg").exists()
    assert not list((tmp_path / "region").glob("*.partial.*"))


def test_an_extract_edited_by_hand_or_missing_its_table_is_rebuilt(tmp_path):
    run = _run(tmp_path / "v5" / "fl_statewide", [[11]])
    fiadb, region = _fiadb(tmp_path / "fiadb.db"), tmp_path / "region"
    write_fia_extract(region, fiadb=fiadb, runs=[run], vat=VAT)

    con = sqlite3.connect(region / "fia_extract.db")
    con.execute("INSERT INTO PLOT VALUES ('999', 12, 2019)")   # no longer what the manifest hashed
    con.commit()
    con.close()
    write_fia_extract(region, fiadb=fiadb, runs=[run], vat=VAT)
    assert [r[0] for r in _rows(region / "fia_extract.db", "SELECT CN FROM PLOT")] == [PLOT_11]

    (region / "fia_extract_missing.csv").unlink()
    write_fia_extract(region, fiadb=fiadb, runs=[run], vat=VAT)
    assert (region / "fia_extract_missing.csv").exists()


def test_the_command_takes_a_relative_region_dir(tmp_path, monkeypatch):
    # e.g. `--region-dir data/regions/fl` from the repo root, as config/leto.yaml names it.
    fiadb = _fiadb(tmp_path / "fiadb.db")
    vat_dbf = _vat_dbf(tmp_path / "TreeMap2022_CONUS.tif.vat.dbf", [(11, PLOT_11)])
    run = _run(tmp_path / "v4" / "aoi_5county", [[11]])
    monkeypatch.chdir(tmp_path)

    main(["fia", "--region-dir", "regions/fl", "--repair", str(run),
          "--fiadb", str(fiadb), "--vat", str(vat_dbf)])

    assert [r[0] for r in _rows(tmp_path / "regions" / "fl" / "fia_extract.db",
                                "SELECT CN FROM PLOT")] == [PLOT_11]
