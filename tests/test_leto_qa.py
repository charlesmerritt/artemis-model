"""The ``leto`` stage's QA report (#166): ``qa/leto/`` tables, maps and an offline ``index.html``.

Stage tests run :func:`pipeline.leto_stage.run_leto` on the two-county fixture of
``test_leto_stage`` and read what lands in the run folder. Unit tests pin the tables' rules on
hand-built frames.
"""

import json
import re

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, box

from pipeline import leto_layout, leto_qa
from pipeline.leto_riparian import FLORIDA_FIRST_PASS_RULES
from pipeline.run import InventoryLayout
from pipeline.spatial_ref import project_crs
from tests import test_leto_stage as fixture
from tests.test_leto_stage import ALPHA, BETA, PLOT_22, _run, _stands

world = fixture.world                         # the two-county raster-repair run folder

QA = ("qa", "leto")
TABLES = ("area_accounting.csv", "acres_by_class.csv", "size_distribution.csv", "donor_qa.csv",
          "donor_plots_missing.csv", "riparian_by_source.csv", "timings.csv")


def _qa(world):
    return world["run_dir"].joinpath(*QA)


def _table(world, name):
    return pd.read_csv(_qa(world) / name, dtype={"county_fips": str, "scope": str, "PLT_CN": str})


# ── the report in the run folder ───────────────────────────────────────────────────────


def test_every_table_and_map_is_written_for_the_region_and_each_county(world):
    _run(world, [ALPHA, BETA])

    qa = _qa(world)
    for name in TABLES:
        assert (qa / name).is_file(), name
    for scope in ("region", ALPHA, BETA):
        for kind in leto_qa.QaMap:
            png = qa / "figures" / f"{scope}_{kind.value}.png"
            assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", png


def test_index_html_shows_every_table_and_map_and_needs_no_server(world):
    _run(world, [ALPHA, BETA])

    qa = _qa(world)
    html = (qa / "index.html").read_text()
    for csv in qa.glob("*.csv"):
        assert f'href="{csv.name}"' in html, csv.name
    for png in (qa / "figures").glob("*.png"):
        assert f'src="figures/{png.name}"' in html, png.name
    assert not re.search(r"""(src|href)=["'](https?:)?//""", html)   # nothing fetched


def test_the_leto_layouts_stage_4_tables_join_the_report(world):
    _run(world, [ALPHA, BETA], layout=InventoryLayout.LETO, species_lookup=fixture.SPECIES)

    qa = _qa(world)
    html = (qa / "index.html").read_text()
    for name in leto_layout.QA_TABLES:
        assert (qa / name).is_file() and f'href="{name}"' in html, name
    assert html.index('href="timings.csv"') > html.index(f'href="{leto_layout.IMPUTATION_SUMMARY}"')


def test_acres_by_class_reconcile_with_stands_csv_per_county(world):
    _run(world, [ALPHA, BETA])

    table = _table(world, "acres_by_class.csv")
    stands = _stands(world)
    scheduled = table[table["owner_class"] != "masked"]
    want = stands.groupby(["county_fips", "owner_class", "unit_class"])["acres"].sum()
    got = scheduled.set_index(["county_fips", "owner_class", "unit_class"])["acres"]
    pd.testing.assert_series_equal(got.sort_index(), want.sort_index(), check_names=False,
                                   atol=0.01)
    units = scheduled.groupby("county_fips")["units"].sum()
    assert units.to_dict() == stands.groupby("county_fips").size().to_dict()
    assert (table["owner_class"] == "masked").any()          # masked land shown, not scheduled


def test_size_distributions_reconcile_with_the_area_accounting(world):
    _run(world, [ALPHA, BETA])

    sizes = _table(world, "size_distribution.csv").set_index(["scope", "layer"])
    accounting = _table(world, "area_accounting.csv").set_index("county_fips")
    for fips in (ALPHA, BETA):
        parents, units = sizes.loc[(fips, "parent_stand")], sizes.loc[(fips, "unit")]
        assert parents["count"] == accounting.loc[fips, "parent_stands"]
        assert parents["acres"] == pytest.approx(accounting.loc[fips, "parent_stand_acres"], abs=0.01)
        assert units["count"] == accounting.loc[fips, "units"]
        assert units["acres"] == pytest.approx(accounting.loc[fips, "unit_acres"], abs=0.01)
        assert parents["p10_acres"] <= parents["median_acres"] <= parents["p90_acres"]
    region = sizes.loc[("region", "unit")]
    assert region["count"] == accounting["units"].sum()
    assert region["acres"] == pytest.approx(_stands(world)["acres"].sum()
                                            + accounting["masked_acres"].sum(), abs=0.01)


def test_riparian_acres_by_buffer_source_reconcile_with_the_riparian_units(world):
    _run(world, [ALPHA, BETA])

    table = _table(world, "riparian_by_source.csv")
    stands = _stands(world)
    riparian = stands[stands["unit_class"] == "riparian"].groupby("county_fips")["acres"].sum()
    assert table.groupby("county_fips")["acres"].sum().to_dict() == pytest.approx(riparian.to_dict(),
                                                                                  abs=0.01)
    assert set(table["layer"]) <= {"flowline", "waterbody", "area", "none"}
    assert not table["fcode"].astype(str).str.endswith(".0").any()      # written as integers
    sources = set(zip(table["layer"], table["label"]))
    assert ("flowline", "Perennial Stream") in sources and ("waterbody", "Perennial Lake/Pond") in sources


def test_donor_qa_counts_every_parent_and_the_representative_plots_share(world):
    _run(world, [ALPHA, BETA])

    donors = _table(world, "donor_qa.csv").set_index("scope")
    accounting = _table(world, "area_accounting.csv").set_index("county_fips")
    for fips in (ALPHA, BETA):
        row = donors.loc[fips]
        assert row["parent_stands"] == accounting.loc[fips, "parent_stands"]
        assert 1 <= row["donor_plots_median"] <= row["donor_plots_max"]
        assert 0 < row["representative_share_p10"] <= row["representative_share_median"] <= 1
        assert row["plots_missing_from_fia"] == 0
    assert donors.loc["region", "parent_stands"] == accounting["parent_stands"].sum()


def test_a_donor_plot_missing_from_the_fia_extract_is_listed_with_its_exact_plt_cn(world):
    import sqlite3

    with sqlite3.connect(world["region"] / "fia_extract.db") as con:     # its FVS rows stay
        con.execute("DELETE FROM COND WHERE PLT_CN = ?", (PLOT_22,))
    _run(world, BETA)

    missing = _table(world, "donor_plots_missing.csv")
    assert missing["PLT_CN"].tolist() == [PLOT_22]                     # 19 digits, intact
    assert (missing["county_fips"] == BETA).all() and missing["cells"].item() > 0
    assert _table(world, "donor_qa.csv").set_index("scope").loc[BETA, "plots_missing_from_fia"] == 1


def test_timings_and_peak_memory_are_recorded_per_county(world):
    _run(world, [ALPHA, BETA])

    timings = _table(world, "timings.csv").set_index("county_fips")
    assert sorted(timings.index) == [ALPHA, BETA]
    steps = [c for c in timings.columns if c.endswith("_seconds") and c != "total_seconds"]
    assert {"clip_seconds", "segment_seconds", "riparian_seconds", "maps_seconds"} <= set(steps)
    assert (timings[steps] >= 0).all().all()
    assert (timings["total_seconds"] >= timings[steps].sum(axis=1) - 0.01).all()
    assert (timings["peak_rss_mb"] > 0).all()
    assert set(timings["memory_scope"]) == {"process"}                 # serial: one shared process


def test_parallel_counties_measure_memory_in_their_own_process(world):
    _run(world, [ALPHA, BETA], workers=2)

    assert set(_table(world, "timings.csv")["memory_scope"]) == {"county"}


def test_a_skipped_county_keeps_its_qa_and_the_report_still_covers_it(world):
    _run(world, [ALPHA, BETA])
    first = _table(world, "timings.csv")
    _run(world, [ALPHA, BETA])

    pd.testing.assert_frame_equal(_table(world, "timings.csv"), first)
    assert (_qa(world) / "figures" / f"{ALPHA}_{leto_qa.QaMap.OWNER_CLASS.value}.png").is_file()
    record = json.loads((world["run_dir"] / "leto.json").read_text())
    assert {e["status"] for e in record["counties"].values()} == {"done"}


# ── the rules, on hand-built frames ────────────────────────────────────────────────────


def test_a_size_row_gives_count_quantiles_and_the_small_tail():
    row = leto_qa.size_row(pd.Series([0.2, 0.5, 0.9, 3.0, 4.0, 10.0, 20.0, 40.0, 80.0, 160.0]),
                           one_cell=2)

    assert row["count"] == 10 and row["acres"] == pytest.approx(318.6)
    assert row["median_acres"] == pytest.approx(7.0)
    assert row["p10_acres"] == pytest.approx(np.percentile([0.2, 0.5, 0.9, 3, 4, 10, 20, 40, 80, 160], 10))
    assert row["under_1_acre"] == 3 and row["under_5_acres"] == 5 and row["one_cell"] == 2


def test_an_empty_layer_has_a_zero_count_and_blank_quantiles():
    row = leto_qa.size_row(pd.Series([], dtype=float), one_cell=0)

    assert row["count"] == 0 and np.isnan(row["median_acres"])


def test_the_representative_share_is_the_largest_donors_share_of_the_parents_cells():
    weights = pd.DataFrame({"SEG_ID": [1, 1, 2], "TREEMAP_VALUE": [5, 6, 7],
                            "WEIGHT": [0.6, 0.4, 1.0], "WEIGHT_ORIGINAL": [0.55, 0.37, 0.9]})

    share = leto_qa.representative_share(weights)

    assert share.to_dict() == {1: 0.55, 2: 0.9}


def _riparian_units(*geoms):
    frame = gpd.GeoDataFrame({"SEG_ID": range(1, len(geoms) + 1)}, geometry=list(geoms),
                             crs=project_crs())
    frame["Acres"] = frame.area / 4046.8564224
    return frame


def test_each_riparian_unit_goes_whole_to_the_widest_buffer_that_holds_it():
    stream = LineString([(0, 0), (0, 1000)])                     # perennial: 75 ft
    ditch = LineString([(5, 0), (5, 1000)])                      # intermittent: 35 ft, inside it
    pond = box(500, 0, 600, 100)                                 # perennial pond: 75 ft
    layers = {"flowline": gpd.GeoDataFrame({"fcode": [46006, 46003]}, geometry=[stream, ditch],
                                           crs=project_crs()),
              "waterbody": gpd.GeoDataFrame({"fcode": [39004]}, geometry=[pond], crs=project_crs())}
    units = _riparian_units(box(-10, 0, 10, 1000), box(480, 0, 495, 100),
                            box(5000, 0, 5010, 10))    # the last: a speckle merged in, no buffer

    table = leto_qa.riparian_by_source(units, layers, FLORIDA_FIRST_PASS_RULES)

    by = table.set_index(["layer", "label"])
    assert by.loc[("flowline", "Perennial Stream"), "acres"] == pytest.approx(units.Acres[0], abs=1e-3)
    assert by.loc[("waterbody", "Perennial Lake/Pond"), "acres"] == pytest.approx(units.Acres[1], abs=1e-3)
    assert ("flowline", "Intermittent Stream") not in by.index
    assert by.loc[("none", "outside every buffer"), "acres"] == pytest.approx(units.Acres[2], abs=1e-3)
    assert table["acres"].sum() == pytest.approx(units["Acres"].sum(), abs=1e-3)
    assert by.loc[("flowline", "Perennial Stream"), "buffer_feet"] == 75.0


def test_no_riparian_unit_is_an_empty_table_with_the_columns():
    table = leto_qa.riparian_by_source(_riparian_units(box(0, 0, 10, 10)).iloc[:0], {},
                                       FLORIDA_FIRST_PASS_RULES)

    assert table.empty and {"layer", "fcode", "label", "buffer_feet", "pieces", "acres"} <= set(table)


def test_the_index_lays_out_tables_later_tickets_add_after_the_known_ones(tmp_path):
    pd.DataFrame({"county_fips": ["12001"], "note": ["x"]}).to_csv(tmp_path / "zz_later.csv",
                                                                   index=False)
    pd.DataFrame({"county_fips": ["12001"], "county": ["Alpha"], "acres": [1.0]}).to_csv(
        tmp_path / "area_accounting.csv", index=False)
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "region_owner_class.png").write_bytes(b"\x89PNG")
    (tmp_path / "figures" / "12001_owner_class.png").write_bytes(b"\x89PNG")

    leto_qa.write_index(tmp_path)

    html = (tmp_path / "index.html").read_text()
    assert html.index('href="area_accounting.csv"') < html.index('href="zz_later.csv"')
    assert html.index("Region overview") < html.index("<summary>12001 Alpha</summary>")
    assert 'src="figures/region_owner_class.png"' in html
    assert 'src="figures/12001_owner_class.png"' in html
    assert "Owner class" in html                                         # the map's caption


def test_a_map_not_named_for_a_county_or_the_region_is_shown_apart(tmp_path):
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "speckle_merges.png").write_bytes(b"\x89PNG")
    (tmp_path / "figures" / "12001_owner_class.png").write_bytes(b"\x89PNG")

    leto_qa.write_index(tmp_path)

    html = (tmp_path / "index.html").read_text()
    other = html.index("Other maps")
    assert other < html.index('src="figures/speckle_merges.png"') < html.index("<summary>12001")
    assert "<summary>speckle" not in html


def test_a_stopwatch_times_each_step_since_the_last():
    clock = leto_qa.Stopwatch()
    clock.lap("one")
    clock.lap("two")

    timings = clock.seconds
    assert list(timings) == ["one", "two"] and all(s >= 0 for s in timings.values())
    assert clock.total() >= sum(timings.values())

