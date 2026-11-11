"""New TreeMap IDs for add-back pixels: a dated stand age and a young plot each (#88, ADR 0003)."""

import json

import numpy as np
import pandas as pd
import pyogrio
import pytest
import rasterio
from rasterio.transform import Affine

from pipeline.ids import read_id_csv
from pipeline.s1_initial_state.add_back_ids import (
    EXTENSION_ID_COLUMNS,
    NEW_ID_KEY,
    VAT_COLUMNS,
    AgeSource,
    TreelessYoungPlots,
    UnmatchedYoungPlots,
    number_keys,
    read_vat,
    pixel_stand_ages,
    write_add_back_ids,
)
from pipeline.s1_initial_state.add_back_stand_age import CutYearDetector as D
from pipeline.s1_initial_state.county_improvement import TreeMapProvenance as TP
from pipeline.s1_initial_state.young_plot_library import TREE_COLUMNS, YoungPlotLibrary


def test_a_dated_pixels_stand_age_counts_from_the_year_after_the_cut_and_a_2022_cut_is_0():
    years = np.array([2014, 2021, 2022, 2001])
    detector = np.array([D.OBATA, D.HANSEN, D.OBATA | D.HANSEN, D.HANSEN])
    ages, sources = pixel_stand_ages(years, detector, strata=np.array([1, 1, 1, 1]))
    assert ages.tolist() == [7, 0, 0, 20]
    assert sources.tolist() == [AgeSource.OBATA, AgeSource.HANSEN, AgeSource.OBATA_HANSEN,
                                AgeSource.HANSEN]


def test_every_combination_of_the_three_detectors_names_its_age_source():
    years = np.array([2014] * 7)
    detector = np.array([D.LANDFIRE, D.OBATA | D.LANDFIRE, D.HANSEN | D.LANDFIRE,
                         D.OBATA | D.HANSEN | D.LANDFIRE, D.OBATA, D.HANSEN, D.OBATA | D.HANSEN])
    _, sources = pixel_stand_ages(years, detector, strata=np.ones(7, dtype=int))
    assert sources.tolist() == ["landfire", "obata_landfire", "hansen_landfire",
                                "obata_hansen_landfire", "obata", "hansen", "obata_hansen"]
    assert all(isinstance(s, AgeSource) for s in sources)


def test_an_undated_pixel_takes_the_median_stand_age_of_its_stratums_dated_pixels():
    # Stratum 1's dated ages are 2, 7, 7, 9 (cut 2019, 2014, 2014, 2012): the lower median, 7,
    # is an age a dated pixel has. Stratum 3's only dated age is 0, and stratum 4's are 4, 6:
    # its lower median is 4. The undated pixels never count toward a median.
    years = np.array([2019, 2014, 2014, 2012, 0, 2022, 0, 2017, 2015, 0, 0])
    detector = np.array([D.OBATA] * 4 + [0, D.HANSEN, 0, D.OBATA, D.OBATA, 0, 0])
    strata = np.array([1, 1, 1, 1, 1, 3, 3, 4, 4, 4, 1])
    ages, sources = pixel_stand_ages(years, detector, strata)
    assert ages.tolist() == [2, 7, 7, 9, 7, 0, 0, 4, 6, 4, 7]
    assert [s is AgeSource.STRATUM_MEDIAN for s in sources] == [
        False] * 4 + [True, False, True, False, False, True, True]


def test_an_undated_pixel_in_a_stratum_with_no_dated_pixel_is_an_error():
    with pytest.raises(ValueError, match="stratum 2: 2 undated add-back pixels"):
        pixel_stand_ages(np.array([2014, 0, 0]), np.array([D.OBATA, 0, 0]), np.array([1, 2, 2]))


def _key_rows(rows):
    """Add-back pixels as (neighbour TM_ID, young TM_ID, stand age, cut year, age source)."""
    frame = pd.DataFrame(rows, columns=NEW_ID_KEY)
    frame["CUT_YEAR"] = frame["CUT_YEAR"].astype("Int64")
    return frame


def test_each_distinct_key_gets_one_new_id_numbered_from_1_000_000_in_sorted_key_order():
    pixels = _key_rows([
        ("36935", "5", 7, 2014, AgeSource.OBATA),
        ("2623", "5", 7, 2014, AgeSource.OBATA),
        ("36935", "5", 7, 2014, AgeSource.OBATA),
        ("36935", "5", 7, 2014, AgeSource.HANSEN),          # the age source alone makes a new key
        ("36935", "5", 7, None, AgeSource.STRATUM_MEDIAN),  # undated: no cut year, sorts last
    ])
    keys, values = number_keys(pixels, treemap_ids=["2623", "36935", "5"])
    assert keys["TM_ID"].tolist() == ["1000000", "1000001", "1000002", "1000003"]
    assert keys["NEIGHBOUR_TM_ID"].tolist() == ["2623", "36935", "36935", "36935"]
    assert keys["AGE_SOURCE"].tolist() == ["obata", "hansen", "obata", "stratum_median"]
    assert keys["pixels"].tolist() == [1, 1, 2, 1]
    assert values.tolist() == [1_000_002, 1_000_000, 1_000_002, 1_000_001, 1_000_003]


def test_a_new_id_never_collides_with_a_treemap_value():
    pixels = _key_rows([("36935", "5", 7, 2014, AgeSource.OBATA),
                        ("36935", "5", 8, 2013, AgeSource.OBATA)])
    with pytest.raises(ValueError, match="1000001"):
        number_keys(pixels, treemap_ids=["36935", "1000001"])


# ── the step on a run folder ────────────────────────────────────────────────────────────

LOBLOLLY, SLASH = 161, 142
LOBLOLLY_PINE, SLASH_PINE = 131, 111
NODATA = 4294967295
YOUNG_7, YOUNG_0, YOUNG_6 = "1234567890123456789", "100000000000002", "100000000000003"  # PLT_CN
NEIGHBOUR_PLT_CN = {"36935": "200000000000001", "2623": "200000000000002"}


def _write(path, bands, nodata):
    bands = np.asarray(bands)
    bands = bands[None] if bands.ndim == 2 else bands
    with rasterio.open(path, "w", driver="GTiff", height=bands.shape[1], width=bands.shape[2],
                       count=bands.shape[0], dtype=bands.dtype, crs="EPSG:5070",
                       transform=Affine(30, 0, 0, 0, -30, 30), nodata=nodata) as dst:
        dst.write(bands)


def _read(path):
    with rasterio.open(path) as src:
        return src.read(1)


def _vat_row(tm_id, plt_cn, fortypcd, **values):
    row = dict.fromkeys(VAT_COLUMNS, 1.0)
    row.update(Value=tm_id, TM_ID=tm_id, PLT_CN=plt_cn, Count=500.0, FORTYPCD=fortypcd,
               ForTypName=f"type {fortypcd}", FldTypName=f"type {fortypcd}", **values)
    return row


def _run(tmp_path, *, young_plots=((YOUNG_7, LOBLOLLY, 7), (YOUNG_0, LOBLOLLY, 0), (YOUNG_6, SLASH, 6))):
    """A one-row run: a published pixel, four add-back pixels and an unmapped one.

    Neighbour plot 36935 (loblolly) surrounds pixels 1, 2 and 4; 2623 (slash) pixel 3.
    Pixel 1 is cut 2014 by Obata (stand age 7), pixel 2 in 2021 by Hansen, later than
    Obata's 2014 (age 0), pixel 3 in 2015 by Hansen (age 6). Pixel 4 is undated: stratum 1's
    dated ages are 7 and 0, so it takes their lower median, 0.
    """
    run = tmp_path / "aoi_5county"
    run.mkdir()
    _write(run / "treemap2022_provenance.tif", np.array([[
        TP.PUBLISHED, TP.ADDED_BACK_YOUNG, TP.ADDED_BACK_YOUNG, TP.ADDED_BACK, TP.ADDED_BACK_YOUNG,
        TP.UNMAPPED_LAND]], dtype=np.uint8), 255)
    _write(run / "treemap2022_improved.tif",
           np.array([[36935, 36935, 36935, 2623, 36935, NODATA]], dtype=np.uint32), NODATA)
    _write(run / "bookend_strata.tif", np.array([[0, 1, 1, 2, 1, 5]], dtype=np.uint8), 255)
    obata, hansen = tmp_path / "obata.tif", tmp_path / "hansen.tif"
    _write(obata, np.array([[0, 2014, 2014, 0, 0, 0]], dtype=np.uint16), 0)
    _write(hansen, np.array([[[0, 0, 21, 15, 0, 0]], [[90] * 6]], dtype=np.uint8), None)

    tm_ids = {plt_cn: str(101 + i) for i, (plt_cn, _, _) in enumerate(young_plots)}
    plots = pd.DataFrame([(tm_ids[p], p, f, a) for p, f, a in young_plots],
                         columns=["TM_ID", "PLT_CN", "FORTYPCD", "STDAGE"])
    young_trees = [(YOUNG_7, LOBLOLLY_PINE, 300.0), (YOUNG_7, SLASH_PINE, 75.0),
                   (YOUNG_6, SLASH_PINE, 400.0)]
    trees = pd.DataFrame([(tm_ids[p], p, 1, tpa, sp, 2.0, 12.0, 40.0)
                          for p, sp, tpa in young_trees if p in tm_ids], columns=TREE_COLUMNS)
    seedlings = pd.DataFrame([(YOUNG_0, LOBLOLLY_PINE, 1200.0)], columns=["PLT_CN", "SPCD", "TPA_UNADJ"])
    library = YoungPlotLibrary(plots, trees, seedlings)

    vat = pd.DataFrame([_vat_row("36935", NEIGHBOUR_PLT_CN["36935"], LOBLOLLY, STDAGE=30),
                        _vat_row("2623", NEIGHBOUR_PLT_CN["2623"], SLASH)]
                       + [_vat_row(tm_ids[p], p, f, BALIVE=float(a)) for p, f, a in young_plots])
    tree_table = tmp_path / "tree_table.csv"
    pd.DataFrame([("36935", NEIGHBOUR_PLT_CN["36935"], 1, 100.0, LOBLOLLY_PINE, 9.0, 60.0, 40.0),
                  ("2623", NEIGHBOUR_PLT_CN["2623"], 1, 100.0, SLASH_PINE, 9.0, 60.0, 40.0)],
                 columns=TREE_COLUMNS).to_csv(tree_table, index=False)
    return run, dict(vat=vat, library=library, tree_table=tree_table, obata=obata, hansen=hansen)


def test_every_add_back_pixel_gets_a_new_id_that_resolves_to_a_vat_extension_row(tmp_path):
    run, sources = _run(tmp_path)
    published_run = _read(run / "treemap2022_improved.tif")

    write_add_back_ids(run, **sources)

    out = run / "young_plots"
    improved = _read(out / "treemap2022_improved.tif")
    # Keys in order: (2623, slash age 6), (36935, loblolly age 7), then the two age-0 keys of
    # 36935 and its age-0 young plot, the dated one before the undated one.
    assert improved.tolist() == [[36935, 1_000_001, 1_000_002, 1_000_000, 1_000_003, NODATA]]
    assert _read(out / "treemap2022_provenance.tif").tolist() == [[
        TP.PUBLISHED, TP.ADDED_BACK_YOUNG, TP.ADDED_BACK_YOUNG, TP.ADDED_BACK_YOUNG,
        TP.ADDED_BACK_YOUNG, TP.UNMAPPED_LAND]]
    vat = pd.read_csv(out / "treemap2022_improved_vat_extension.csv", dtype=str)
    assert vat["Value"].tolist() == ["1000000", "1000001", "1000002", "1000003"]
    assert (vat["TM_ID"] == vat["Value"]).all()
    assert (_read(run / "treemap2022_improved.tif") == published_run).all()   # the run is untouched


def test_a_vat_extension_row_copies_its_young_plot_and_records_the_age_and_both_plots(tmp_path):
    run, sources = _run(tmp_path)
    write_add_back_ids(run, **sources)

    path = run / "young_plots" / "treemap2022_improved_vat_extension.csv"
    vat = read_id_csv(path, id_columns=EXTENSION_ID_COLUMNS)
    assert list(vat.columns[:27]) == VAT_COLUMNS
    age_7 = vat.set_index("TM_ID").loc["1000001"]
    assert age_7["PLT_CN"] == YOUNG_7 == age_7["YOUNG_PLT_CN"]   # FIA joins reach the young plot
    assert age_7["YOUNG_TM_ID"] == "101"
    assert (age_7["NEIGHBOUR_TM_ID"], age_7["NEIGHBOUR_PLT_CN"]) == ("36935", NEIGHBOUR_PLT_CN["36935"])
    assert (age_7["FORTYPCD"], age_7["BALIVE"], age_7["Count"]) == (LOBLOLLY, 7.0, 1)
    assert (age_7["STAND_AGE_2022"], age_7["CUT_YEAR"], age_7["AGE_SOURCE"]) == (7, 2014, "obata")
    assert (age_7["MATCH_LEVEL"], age_7["AGE_GAP"]) == ("type", 0)
    undated = vat.set_index("TM_ID").loc["1000003"]
    assert pd.isna(undated["CUT_YEAR"]) and undated["AGE_SOURCE"] == "stratum_median"
    # PLT_CN is text: 19 digits a float would round, written as they are.
    assert f",{YOUNG_7}," in path.read_text()


def test_the_tree_extension_holds_each_new_ids_young_plot_trees_and_seedlings_at_age_0_to_2(tmp_path):
    run, sources = _run(tmp_path)
    write_add_back_ids(run, **sources)

    trees = read_id_csv(run / "young_plots" / "treemap2022_improved_tree_extension.csv")
    by_id = {tm_id: t for tm_id, t in trees.groupby("TM_ID")}
    assert sorted(by_id) == ["1000000", "1000001", "1000002", "1000003"]
    assert by_id["1000001"]["SPCD"].tolist() == [LOBLOLLY_PINE, SLASH_PINE]   # the age-7 plot's trees
    assert set(by_id["1000001"]["PLT_CN"]) == {YOUNG_7}
    for age_0 in ("1000002", "1000003"):                     # its seedlings, as small live trees
        seedling = by_id[age_0].iloc[0]
        assert len(by_id[age_0]) == 1
        assert (seedling["PLT_CN"], seedling["SPCD"], seedling["TPA_UNADJ"]) == (YOUNG_0, LOBLOLLY_PINE, 1200.0)
        assert (seedling["STATUSCD"], seedling["DIA"], seedling["HT"]) == (1, 0.1, 1.0)


PX = 0.2224  # acres per pixel


def test_validation_reports_acres_by_stand_age_and_match_level_and_that_the_outputs_join(tmp_path):
    run, sources = _run(tmp_path)
    write_add_back_ids(run, **sources)

    report = json.loads((run / "young_plots" / "validation.json").read_text())
    assert report["acres_by_stand_age"] == pytest.approx({"0": 2 * PX, "6": PX, "7": PX})
    assert report["acres_by_match_level"] == pytest.approx({"type": 4 * PX})
    assert report["acres_by_age_source"] == pytest.approx(
        {"hansen": 2 * PX, "obata": PX, "stratum_median": PX})
    assert report["checks"] == {
        "every_add_back_pixel_resolves_to_a_vat_row": True,
        "every_vat_row_is_full": True,
        "every_new_id_has_trees": True,
        "no_new_id_is_a_treemap_value": True,
        "vat_counts_match_the_raster": True,
        "other_pixels_unchanged": True,
    }
    assert report["counts"] == {"add_back_pixels": 4, "new_ids": 4, "young_plots": 3,
                                "neighbour_plots": 2}


def test_a_pixel_with_no_young_plot_is_an_error_naming_its_forest_type_and_nothing_is_written(tmp_path):
    # Without the slash plot, pixel 3 (neighbour plot slash, stand age 6) has no young plot
    # of its type, group or parent pool within 2 years: the softwoods left are aged 9 and 0.
    run, sources = _run(tmp_path, young_plots=((YOUNG_7, LOBLOLLY, 9), (YOUNG_0, LOBLOLLY, 0)))
    with pytest.raises(UnmatchedYoungPlots, match=r"1 add-back pixels .* FORTYPCD 142: 1 px"):
        write_add_back_ids(run, **sources)
    assert not (run / "young_plots").exists()


def test_read_vat_keeps_treemaps_27_columns_with_its_ids_as_exact_strings(tmp_path):
    path = tmp_path / "treemap.tif.vat.dbf"
    row = _vat_row(36935.0, 2.0000000000001e14, LOBLOLLY)   # the DBF stores IDs as floats
    pyogrio.write_dataframe(pd.DataFrame([row]), path)
    vat = read_vat(path)
    assert list(vat.columns) == VAT_COLUMNS
    assert vat[["Value", "TM_ID", "PLT_CN"]].iloc[0].tolist() == ["36935", "36935", "200000000000010"]


def _manifest(run, listed=None, **inputs):
    methods = {"bookends": "run", "obata_disturbance": "run", "hansen_loss": "run"}
    manifest = {"methods": methods, "inputs": inputs}
    if listed is not None:
        manifest["counties"] = listed
    (run / "manifest.json").write_text(json.dumps(manifest))


def test_a_detector_the_run_used_but_not_passed_or_found_is_an_error_not_undated_pixels(tmp_path):
    # Without Obata, pixel 1 (cut by Obata alone) would silently take a stratum median.
    run, sources = _run(tmp_path)
    _manifest(run, obata=str(tmp_path / "elsewhere" / "lastDist.tif"), hansen=str(sources["hansen"]))
    with pytest.raises(ValueError, match="--obata-tif"):
        write_add_back_ids(run, **{**sources, "obata": None})
    assert not (run / "young_plots").exists()


def test_a_detector_not_passed_falls_back_to_the_path_the_runs_manifest_records(tmp_path):
    run, sources = _run(tmp_path)
    _manifest(run, obata=str(sources["obata"]), hansen=str(sources["hansen"]))
    report = write_add_back_ids(run, **{**sources, "obata": None, "hansen": None})
    assert report["acres_by_age_source"] == pytest.approx(
        {"hansen": 2 * PX, "obata": PX, "stratum_median": PX})


def test_landfire_dates_an_otherwise_undated_pixel_instead_of_a_stratum_median(tmp_path, landfire_dist):
    # Pixel 4 is undated by Obata and Hansen. A 2021 LANDFIRE clearcut dates it: age 0, and
    # the age-0 loblolly plot is its young plot.
    run, sources = _run(tmp_path)
    root = landfire_dist("2022", "21", [[0, 0, 0, 0, 421, 0]],
                         pd.DataFrame([(421, 2021, "Clearcut")],
                                      columns=["VALUE", "DIST_YEAR", "DIST_TYPE"]),
                         transform=Affine(30, 0, 0, 0, -30, 30))
    report = write_add_back_ids(run, **sources, landfire=root)
    assert report["acres_by_age_source"] == pytest.approx(
        {"hansen": 2 * PX, "obata": PX, "landfire": PX})
    vat = read_id_csv(run / "young_plots" / "treemap2022_improved_vat_extension.csv",
                      id_columns=EXTENSION_ID_COLUMNS)
    dated = vat[vat["AGE_SOURCE"] == "landfire"].iloc[0]
    assert (dated["CUT_YEAR"], dated["STAND_AGE_2022"]) == (2021, 0)
    assert report["cut_year_detectors"] == {"obata": str(sources["obata"]),
                                            "hansen": str(sources["hansen"]), "landfire": str(root)}


def test_a_young_plot_with_no_trees_is_an_error_and_nothing_is_written(tmp_path):
    # A slash plot of age 6 with no live trees: pixel 3's new ID would have an empty tree list.
    treeless = "100000000000009"
    run, sources = _run(tmp_path, young_plots=((YOUNG_7, LOBLOLLY, 7), (YOUNG_0, LOBLOLLY, 0),
                                               (treeless, SLASH, 6)))
    with pytest.raises(TreelessYoungPlots, match=rf"1 add-back pixels .* {treeless}"):
        write_add_back_ids(run, **sources)
    assert not (run / "young_plots").exists()


def _counties(tmp_path, *polygons):
    """A county layer in the project CRS: (ADMIN_FIPS, NAME, x0, x1) strips over the run's row."""
    import geopandas as gpd
    from shapely.geometry import box

    path = tmp_path / "counties.shp"
    gpd.GeoDataFrame({"ADMIN_FIPS": [p[0] for p in polygons], "NAME": [p[1] for p in polygons]},
                     geometry=[box(x0, 0, x1, 30) for _, _, x0, x1 in polygons],
                     crs="EPSG:5070").to_file(path)
    return path


def test_a_run_with_counties_reports_add_back_acres_by_county_and_age_source(tmp_path):
    # Pixels 1-2 (obata, hansen) fall in 12001, pixels 3-4 (hansen, stratum median) in 12077.
    # 12003 is listed but has no add-back pixel; it still gets no row, not a zero row.
    run, sources = _run(tmp_path)
    counties = _counties(tmp_path, ("12001", "Alachua", 0, 90), ("12077", "Liberty", 90, 180),
                         ("12003", "Baker", 300, 330))
    _manifest(run, ["12001 12001", "12003 Baker", "12077 12077"],   # names as the run wrote them
              obata=str(sources["obata"]), hansen=str(sources["hansen"]), counties=str(counties))

    write_add_back_ids(run, **sources)

    table = pd.read_csv(run / "young_plots" / "add_back_acres_by_county.csv", dtype={"county_fips": str})
    assert table.to_dict("records") == [
        {"county_fips": "12001", "county": "Alachua", "age_source": "hansen", "pixels": 1, "acres": PX},
        {"county_fips": "12001", "county": "Alachua", "age_source": "obata", "pixels": 1, "acres": PX},
        {"county_fips": "12077", "county": "Liberty", "age_source": "hansen", "pixels": 1, "acres": PX},
        {"county_fips": "12077", "county": "Liberty", "age_source": "stratum_median", "pixels": 1,
         "acres": PX},
    ]


def test_add_back_pixels_outside_every_listed_county_are_reported_not_dropped(tmp_path):
    run, sources = _run(tmp_path)
    counties = _counties(tmp_path, ("12001", "Alachua", 0, 90))
    _manifest(run, ["12001 Alachua"], obata=str(sources["obata"]), hansen=str(sources["hansen"]),
              counties=str(counties))

    write_add_back_ids(run, **sources)

    table = pd.read_csv(run / "young_plots" / "add_back_acres_by_county.csv",
                        dtype={"county_fips": str}, keep_default_na=False)
    outside = table[table["county_fips"] == ""]
    assert outside[["county", "age_source", "pixels"]].to_dict("records") == [
        {"county": "outside listed counties", "age_source": "hansen", "pixels": 1},
        {"county": "outside listed counties", "age_source": "stratum_median", "pixels": 1}]
    assert table["pixels"].sum() == 4


def test_a_run_without_counties_in_its_manifest_writes_no_county_table(tmp_path):
    run, sources = _run(tmp_path)
    write_add_back_ids(run, **sources)
    assert not (run / "young_plots" / "add_back_acres_by_county.csv").exists()


def test_default_inputs_are_the_declared_drive_paths_or_their_place_under_a_mirror(tmp_path):
    from pipeline.s1_initial_state.add_back_ids import default_inputs

    on_drive = default_inputs(None)
    assert on_drive["db"].as_posix() == "/mnt/d/SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db"
    mirrored = default_inputs(tmp_path)
    assert mirrored == {
        "vat": tmp_path / "TreeMap-2022/Data/TreeMap2022_CONUS.tif.vat.dbf",
        "tree_table": tmp_path / "TreeMap-2022/Data/TreeMap2022_CONUS_Tree_Table.csv",
        "db": tmp_path / "SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db"}


def test_an_add_back_pixel_with_no_vat_row_is_counted_as_unresolved_not_dropped(tmp_path):
    from pipeline.s1_initial_state.add_back_ids import UNRESOLVED, acres_by_county

    run, sources = _run(tmp_path)
    counties = _counties(tmp_path, ("12001", "Alachua", 0, 180))
    write_add_back_ids(run, **sources)
    path = run / "young_plots" / "treemap2022_improved_vat_extension.csv"
    vat = pd.read_csv(path, dtype=str)
    vat[vat["Value"] != "1000001"].to_csv(path, index=False)   # pixel 1's row goes missing

    table = acres_by_county(run, counties, ["12001"])
    assert table["pixels"].sum() == 4
    assert table.loc[table["age_source"] == UNRESOLVED, "pixels"].tolist() == [1]


def test_a_county_that_is_not_five_digits_is_refused_before_the_layer_is_read(tmp_path):
    from pipeline.s1_initial_state.add_back_ids import acres_by_county

    run, sources = _run(tmp_path)
    write_add_back_ids(run, **sources)
    with pytest.raises(ValueError, match="5 digits"):
        acres_by_county(run, tmp_path / "missing.shp", ["12001", "12001' OR '1'='1"])


def _three_rows(path):
    """Rewrite a one-row test raster as three copies of that row, every band."""
    with rasterio.open(path) as src:
        bands, nodata = src.read(), src.nodata
    _write(path, np.repeat(bands, 3, axis=1), nodata)


def _run_three_rows(tmp_path):
    tmp_path.mkdir()
    run, sources = _run(tmp_path)
    for name in ("treemap2022_provenance.tif", "treemap2022_improved.tif", "bookend_strata.tif"):
        _three_rows(run / name)
    _three_rows(sources["obata"])
    _three_rows(sources["hansen"])
    counties = tmp_path / "counties.shp"
    import geopandas as gpd
    from shapely.geometry import box
    gpd.GeoDataFrame({"ADMIN_FIPS": ["12001", "12077"], "NAME": ["Alachua", "Liberty"]},
                     geometry=[box(0, 0, 90, 30), box(0, -60, 180, 0)],   # row 0 west; rows 1-2
                     crs="EPSG:5070").to_file(counties)
    _manifest(run, ["12001 12001", "12077 12077"], obata=str(sources["obata"]),
              hansen=str(sources["hansen"]), counties=str(counties))
    return run, sources


def _outputs(run):
    out = run / "young_plots"
    return {p.name: (_read(p).tolist() if p.suffix == ".tif" else p.read_text())
            for p in sorted(out.iterdir())}


def test_the_outputs_do_not_depend_on_how_many_rows_are_read_at_a_time(tmp_path, monkeypatch):
    import pipeline.s1_initial_state.add_back_ids as module

    whole, sources = _run_three_rows(tmp_path / "whole")
    write_add_back_ids(whole, **sources)
    monkeypatch.setattr(module, "STRIP_ROWS", 1)
    by_row, sources = _run_three_rows(tmp_path / "by_row")
    write_add_back_ids(by_row, **sources)

    expected, got = _outputs(whole), _outputs(by_row)
    assert sorted(got) == sorted(expected)
    for name in expected:
        if name != "validation.json":   # it records the detector paths, which differ
            assert got[name] == expected[name], name
    report = json.loads((by_row / "young_plots" / "validation.json").read_text())
    assert all(report["checks"].values())
    assert report["counts"]["add_back_pixels"] == 12
    # Row 0's pixels 1-2 are in 12001 and 3-4 in no county; rows 1-2 are all 12077.
    table = pd.read_csv(by_row / "young_plots" / "add_back_acres_by_county.csv",
                        dtype={"county_fips": str}, keep_default_na=False)
    assert table.groupby("county_fips")["pixels"].sum().to_dict() == {"": 2, "12001": 2, "12077": 8}
