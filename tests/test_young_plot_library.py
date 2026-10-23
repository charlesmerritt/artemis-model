"""The young-plot library: real FIA plots an add-back pixel can carry (#87, ADR 0003)."""

import sqlite3

import pandas as pd
import pyogrio
import pytest

from pipeline.s1_initial_state.young_plot_library import (
    MatchLevel,
    YoungPlotLibrary,
    aoi_forest_types,
    read_young_plot_library,
)

LOBLOLLY, SLASH, LONGLEAF = 161, 142, 141


def _library(plots: list[tuple[str, int, int]], trees: list[tuple[str, int, float]] = (),
             seedlings: list[tuple[str, int, float]] = ()) -> YoungPlotLibrary:
    """Plots as (PLT_CN, FORTYPCD, STDAGE); trees and seedlings as (PLT_CN, SPCD, TPA)."""
    plot_frame = pd.DataFrame(plots, columns=["PLT_CN", "FORTYPCD", "STDAGE"])
    plot_frame["TM_ID"] = [str(i + 1) for i in range(len(plot_frame))]
    tm_id = dict(zip(plot_frame["PLT_CN"], plot_frame["TM_ID"]))
    tree_frame = pd.DataFrame(list(trees), columns=["PLT_CN", "SPCD", "TPA_UNADJ"])
    tree_frame["TM_ID"] = tree_frame["PLT_CN"].map(tm_id)
    tree_frame["STATUSCD"], tree_frame["DIA"], tree_frame["HT"] = 1, 2.0, 12.0
    return YoungPlotLibrary(plot_frame, tree_frame,
                            pd.DataFrame(list(seedlings), columns=["PLT_CN", "SPCD", "TPA_UNADJ"]))


NO_MIX = pd.Series(dtype=float)


def test_a_plot_of_the_neighbour_plots_forest_type_and_the_stand_age_matches_exactly():
    library = _library([("100000000000001", SLASH, 7), ("100000000000002", LOBLOLLY, 7)])
    match = library.match(LOBLOLLY, 7, NO_MIX)
    assert match.level is MatchLevel.TYPE
    assert match.age_gap == 0
    assert match.plt_cn == "100000000000002"


LOBLOLLY_PINE, SWEETGUM = 131, 611


def test_among_plots_of_the_same_age_gap_the_closest_species_mix_wins():
    # Neighbour plot: 80% loblolly pine, 20% sweetgum by live TPA. Plot A (all loblolly) is
    # 0.2 away (total variation), plot B (half and half) 0.3, so A wins although its
    # PLT_CN sorts later and it is listed second.
    library = _library(
        [("100000000000001", LOBLOLLY, 7), ("100000000000009", LOBLOLLY, 7)],
        trees=[("100000000000009", LOBLOLLY_PINE, 400.0),
               ("100000000000001", LOBLOLLY_PINE, 200.0), ("100000000000001", SWEETGUM, 200.0)])
    neighbour_mix = pd.Series({LOBLOLLY_PINE: 0.8, SWEETGUM: 0.2})
    assert library.match(LOBLOLLY, 7, neighbour_mix).plt_cn == "100000000000009"


def test_the_nearest_age_wins_over_a_closer_species_mix():
    library = _library(
        [("100000000000001", LOBLOLLY, 9), ("100000000000002", LOBLOLLY, 8)],
        trees=[("100000000000001", LOBLOLLY_PINE, 400.0), ("100000000000002", SWEETGUM, 400.0)])
    match = library.match(LOBLOLLY, 7, pd.Series({LOBLOLLY_PINE: 1.0}))
    assert (match.plt_cn, match.age_gap) == ("100000000000002", 1)


def test_a_full_tie_goes_to_the_lowest_plt_cn_as_a_string_not_a_number():
    # 14 digits beat 15 as numbers, but "1..." sorts before "9..." as text.
    library = _library([("99999999999999", LOBLOLLY, 7), ("100000000000001", LOBLOLLY, 7)])
    assert library.match(LOBLOLLY, 7, NO_MIX).plt_cn == "100000000000001"


def test_with_no_plot_of_the_type_within_two_years_the_forest_type_group_is_searched():
    # Slash at stand age 0: the library's slash plots start at age 3, so the longleaf/slash
    # group's longleaf plot at age 1 is the match. Loblolly is another group.
    library = _library([("100000000000001", SLASH, 3), ("100000000000002", LONGLEAF, 1),
                        ("100000000000003", LOBLOLLY, 0)])
    match = library.match(SLASH, 0, NO_MIX)
    assert (match.level, match.age_gap, match.plt_cn) == (MatchLevel.GROUP, 1, "100000000000002")


def test_a_plot_of_the_type_at_two_years_beats_a_plot_of_the_group_at_the_exact_age():
    library = _library([("100000000000001", LONGLEAF, 0), ("100000000000002", SLASH, 2)])
    match = library.match(SLASH, 0, NO_MIX)
    assert (match.level, match.age_gap, match.plt_cn) == (MatchLevel.TYPE, 2, "100000000000002")


def test_with_no_plot_in_the_group_within_two_years_the_pixel_is_unmatched():
    library = _library([("100000000000001", SLASH, 10), ("100000000000003", LOBLOLLY, 0)])
    match = library.match(SLASH, 0, NO_MIX)
    assert (match.level, match.plt_cn, match.tm_id) == (MatchLevel.UNMATCHED, None, None)


SLASH_PINE = 111


def _young_slash_plot() -> YoungPlotLibrary:
    return _library([("100000000000001", SLASH, 1)],
                    trees=[("100000000000001", SLASH_PINE, 75.0)],
                    seedlings=[("100000000000001", SLASH_PINE, 600.0), ("100000000000001", SWEETGUM, 150.0)])


def test_at_stand_age_0_to_2_the_young_plots_seedlings_become_small_live_trees():
    library = _young_slash_plot()
    trees = library.tree_list(library.match(SLASH, 2, NO_MIX), stand_age=2)   # 2: the last such age
    rows = sorted(zip(trees["SPCD"], trees["TPA_UNADJ"], trees["DIA"], trees["HT"]))
    assert rows == [(SLASH_PINE, 75.0, 2.0, 12.0),       # the plot's own tree row
                    (SLASH_PINE, 600.0, 0.1, 1.0),       # seedlings: DBH 0.1 in, height 1 ft
                    (SWEETGUM, 150.0, 0.1, 1.0)]
    assert (trees["STATUSCD"] == 1).all()                # a live filter keeps the seedlings


def test_an_unmatched_pixel_has_no_tree_list():
    library = _library([("100000000000001", SLASH, 10)])
    with pytest.raises(ValueError, match="unmatched"):
        library.tree_list(library.match(SLASH, 0, NO_MIX), stand_age=0)


def test_from_stand_age_3_the_tree_list_is_the_young_plots_trees_only():
    # The pixel's age decides, not the plot's FIA age (1 here, matched at gap 2).
    library = _young_slash_plot()
    trees = library.tree_list(library.match(SLASH, 3, NO_MIX), stand_age=3)
    assert list(zip(trees["SPCD"], trees["TPA_UNADJ"])) == [(SLASH_PINE, 75.0)]


def test_at_stand_age_0_to_2_the_species_mix_counts_the_seedlings():
    # Neither plot has a tree of 1 in DBH; their seedlings tell the slash plot apart.
    library = _library([("100000000000001", SLASH, 1), ("100000000000002", SLASH, 1)],
                       seedlings=[("100000000000001", SWEETGUM, 600.0), ("100000000000002", SLASH_PINE, 600.0)])
    assert library.match(SLASH, 1, pd.Series({SLASH_PINE: 1.0})).plt_cn == "100000000000002"


def test_coverage_reports_each_forest_type_and_ages_fallback_and_how_many_plots_it_chose_from():
    library = _library([("100000000000001", SLASH, 3), ("100000000000002", SLASH, 3),
                        ("100000000000003", LONGLEAF, 1)])
    report = library.coverage([SLASH], stand_ages=[0, 3, 9])
    assert report.to_dict("records") == [
        {"FORTYPCD": SLASH, "stand_age": 0, "level": MatchLevel.GROUP, "age_gap": 1, "candidates": 1},
        {"FORTYPCD": SLASH, "stand_age": 3, "level": MatchLevel.TYPE, "age_gap": 0, "candidates": 2},
        {"FORTYPCD": SLASH, "stand_age": 9, "level": MatchLevel.UNMATCHED, "age_gap": 0, "candidates": 0},
    ]


def _sources(tmp_path):
    """A VAT (float PLT_CN, as TreeMap's), a tree table and an FIA SQLite for five plots.

    Plot 1 is the only young plot. Plot 2 has two conditions, plot 3 another design, plot 4
    is outside AL/FL/GA, and plot 5 is not in TreeMap's library.
    """
    vat = tmp_path / "treemap.tif.vat.dbf"
    pyogrio.write_dataframe(pd.DataFrame({"Value": [11.0, 12.0, 13.0, 14.0],
                                          "PLT_CN": [100000000000001.0, 2.0e14, 3.0e14, 4.0e14],
                                          "FORTYPCD": [142.0, 142.0, 142.0, 142.0]}),
                            vat, driver="ESRI Shapefile")
    tree_table = tmp_path / "tree_table.csv"
    pd.DataFrame({"TM_ID": [11, 11, 12], "PLT_CN": ["100000000000001", "100000000000001", "200000000000000"],
                  "STATUSCD": [1, 2, 1], "TPA_UNADJ": [75.0, 6.0, 75.0], "SPCD": [111, 111, 111],
                  "COMMON_NAME": ["slash pine"] * 3, "DIA": [1.5, 9.0, 2.0], "HT": [9.0, 40.0, 11.0],
                  "CR": [60, 10, 55]}).to_csv(tree_table, index=False)
    db = tmp_path / "fia.db"
    con = sqlite3.connect(db)
    plots = ["100000000000001", "200000000000000", "300000000000000", "400000000000000", "500000000000000"]
    pd.DataFrame({"CN": plots, "STATECD": [12, 12, 12, 45, 12], "DESIGNCD": [1, 1, 311, 1, 1]}
                 ).to_sql("PLOT", con, index=False)
    pd.DataFrame({"PLT_CN": plots, "CONDID": 1, "STATECD": [12, 12, 12, 45, 12], "INVYR": 2015,
                  "FORTYPCD": 142, "STDAGE": 1, "STDORGCD": 1, "COND_STATUS_CD": 1,
                  "CONDPROP_UNADJ": [1.0, 0.6, 1.0, 1.0, 1.0]}).to_sql("COND", con, index=False)
    pd.DataFrame({"PLT_CN": plots, "CONDID": 1, "STATECD": [12, 12, 12, 45, 12], "SPCD": 111,
                  "TPA_UNADJ": 74.965}).to_sql("SEEDLING", con, index=False)
    con.close()
    return vat, db, tree_table


def test_the_library_holds_treemaps_single_condition_national_design_plots_in_al_fl_ga(tmp_path):
    library = read_young_plot_library(*_sources(tmp_path))
    assert library.plots.to_dict("records") == [
        {"TM_ID": "11", "PLT_CN": "100000000000001", "FORTYPCD": 142, "STDAGE": 1}]
    assert list(zip(library.trees["TM_ID"], library.trees["DIA"])) == [("11", 1.5)]   # live trees only
    assert list(zip(library.seedlings["PLT_CN"], library.seedlings["TPA_UNADJ"])) == [
        ("100000000000001", 74.965)]


def test_an_aois_forest_types_are_those_of_the_treemap_plots_its_raster_maps(tmp_path):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    raster = tmp_path / "aoi.tif"
    with rasterio.open(raster, "w", driver="GTiff", width=3, height=1, count=1, dtype="uint32",
                       nodata=0, crs="EPSG:5070", transform=from_origin(0, 30, 30, 30)) as dst:
        dst.write(np.array([[13, 0, 11]], dtype="uint32"), 1)
    vat = tmp_path / "treemap.tif.vat.dbf"
    pyogrio.write_dataframe(pd.DataFrame({"Value": [11.0, 12.0, 13.0], "FORTYPCD": [161.0, 142.0, 608.0]}),
                            vat, driver="ESRI Shapefile")
    assert aoi_forest_types(raster, vat) == [161, 608]
