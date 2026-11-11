"""The young-plot step's QA report: ``<run>/qa/young_plots/`` (#172, the #166 convention)."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from pipeline.s1_initial_state.add_back_ids import write_add_back_ids
from pipeline.young_plot_qa import (
    QA_DIR,
    UNRESOLVED,
    acres_by_county,
    category_codes,
    coarse_majority,
    coarse_mean,
    write_young_plot_qa,
)
from tests.test_add_back_ids import PX, _manifest, _run, _run_three_rows


def _counties(tmp_path, *polygons):
    """A county layer in the project CRS: (ADMIN_FIPS, NAME, x0, x1) strips over the run's row."""
    path = tmp_path / "counties.shp"
    gpd.GeoDataFrame({"ADMIN_FIPS": [p[0] for p in polygons], "NAME": [p[1] for p in polygons]},
                     geometry=[box(x0, 0, x1, 30) for _, _, x0, x1 in polygons],
                     crs="EPSG:5070").to_file(path)
    return path


def _qa_run(tmp_path, listed=None, *polygons):
    """The add-back test run with ``young_plots/`` written, its manifest naming ``polygons``."""
    run, sources = _run(tmp_path)
    if polygons:
        _manifest(run, listed, obata=str(sources["obata"]), hansen=str(sources["hansen"]),
                  counties=str(_counties(tmp_path, *polygons)))
    write_add_back_ids(run, **sources)
    return run


def _table(run, name):
    return pd.read_csv(run / QA_DIR / name, dtype={"county_fips": str}, keep_default_na=False)


# ── the report on a run folder ─────────────────────────────────────────────────────────


def test_the_report_holds_its_tables_maps_and_an_index_linking_every_one(tmp_path):
    run = _qa_run(tmp_path, ["12001 12001"], ("12001", "Alachua", 0, 180))
    index = write_young_plot_qa(run)

    qa = run / QA_DIR
    assert index == qa / "index.html"
    tables = {p.name for p in qa.glob("*.csv")}
    assert tables == {"checks.csv", "acres_by_age_source.csv", "acres_by_match_level.csv",
                      "acres_by_stand_age.csv", "add_back_acres_by_county.csv"}
    figures = {p.name for p in (qa / "figures").glob("*.png")}
    assert figures == {"age_source.png", "stand_age.png", "match_level.png",
                       "county_age_source.png"}
    page = index.read_text()
    for name in tables | {f"figures/{f}" for f in figures}:
        assert f'"{name}"' in page, name


def test_every_acres_table_sums_to_the_add_back_pixels(tmp_path):
    run = _qa_run(tmp_path, ["12001 12001"], ("12001", "Alachua", 0, 180))
    write_young_plot_qa(run)

    for name in ("acres_by_age_source.csv", "acres_by_match_level.csv", "acres_by_stand_age.csv",
                 "add_back_acres_by_county.csv"):
        table = _table(run, name)
        assert table["pixels"].sum() == 4, name
        assert table["acres"].sum() == pytest.approx(4 * PX), name
    assert _table(run, "acres_by_age_source.csv").set_index("age_source")["pixels"].to_dict() == \
        {"hansen": 2, "obata": 1, "stratum_median": 1}
    checks = _table(run, "checks.csv")
    assert len(checks) == 6 and checks["passed"].astype(str).eq("True").all()


def test_a_run_with_counties_reports_add_back_acres_by_county_and_age_source(tmp_path):
    # Pixels 1-2 (obata, hansen) fall in 12001, pixels 3-4 (hansen, stratum median) in 12077.
    # 12003 is listed but has no add-back pixel; it still gets no row, not a zero row.
    run = _qa_run(tmp_path, ["12001 12001", "12003 Baker", "12077 12077"],   # names as the run wrote them
                  ("12001", "Alachua", 0, 90), ("12077", "Liberty", 90, 180), ("12003", "Baker", 300, 330))
    write_young_plot_qa(run)

    assert _table(run, "add_back_acres_by_county.csv").to_dict("records") == [
        {"county_fips": "12001", "county": "Alachua", "age_source": "hansen", "pixels": 1, "acres": PX},
        {"county_fips": "12001", "county": "Alachua", "age_source": "obata", "pixels": 1, "acres": PX},
        {"county_fips": "12077", "county": "Liberty", "age_source": "hansen", "pixels": 1, "acres": PX},
        {"county_fips": "12077", "county": "Liberty", "age_source": "stratum_median", "pixels": 1,
         "acres": PX},
    ]


def test_add_back_pixels_outside_every_listed_county_are_reported_not_dropped(tmp_path):
    run = _qa_run(tmp_path, ["12001 Alachua"], ("12001", "Alachua", 0, 90))
    write_young_plot_qa(run)

    table = _table(run, "add_back_acres_by_county.csv")
    outside = table[table["county_fips"] == ""]
    assert outside[["county", "age_source", "pixels"]].to_dict("records") == [
        {"county": "outside listed counties", "age_source": "hansen", "pixels": 1},
        {"county": "outside listed counties", "age_source": "stratum_median", "pixels": 1}]
    assert table["pixels"].sum() == 4


def test_a_run_without_counties_in_its_manifest_has_no_county_table_or_chart(tmp_path):
    run = _qa_run(tmp_path)
    write_young_plot_qa(run)
    assert not (run / QA_DIR / "add_back_acres_by_county.csv").exists()
    assert not (run / QA_DIR / "figures" / "county_age_source.png").exists()
    assert (run / QA_DIR / "figures" / "age_source.png").exists()


def test_an_add_back_pixel_with_no_vat_row_is_counted_as_unresolved_not_dropped(tmp_path):
    run = _qa_run(tmp_path)
    path = run / "young_plots" / "treemap2022_improved_vat_extension.csv"
    vat = pd.read_csv(path, dtype=str)
    vat[vat["Value"] != "1000001"].to_csv(path, index=False)   # pixel 1's row goes missing

    table = acres_by_county(run, _counties(tmp_path, ("12001", "Alachua", 0, 180)), ["12001"])
    assert table["pixels"].sum() == 4
    assert table.loc[table["age_source"] == UNRESOLVED, "pixels"].tolist() == [1]


def test_the_county_table_does_not_depend_on_how_many_rows_are_read_at_a_time(tmp_path, monkeypatch):
    import pipeline.s1_initial_state.add_back_ids as module

    monkeypatch.setattr(module, "STRIP_ROWS", 1)
    run, sources = _run_three_rows(tmp_path / "by_row")
    write_add_back_ids(run, **sources)
    write_young_plot_qa(run)
    # Row 0's pixels 1-2 are in 12001 and 3-4 in no county; rows 1-2 are all 12077.
    table = _table(run, "add_back_acres_by_county.csv")
    assert table.groupby("county_fips")["pixels"].sum().to_dict() == {"": 2, "12001": 2, "12077": 8}


# ── maps: many pixels per map cell ─────────────────────────────────────────────────────


def test_a_map_cell_shows_the_most_common_category_of_its_add_back_pixels_ties_to_the_lower():
    rows, cols = np.array([0, 0, 1, 0, 3, 3]), np.array([0, 1, 1, 2, 3, 2])
    codes = np.array([2, 1, 2, 1, 1, 2])
    # factor 2: cell (0,0) holds codes 2, 1, 2 -> 2; cell (0,1) holds 1; cell (1,1) holds
    # 1 and 2, a tie -> 1. Cell (1,0) has no add-back pixel: 0.
    assert coarse_majority(rows, cols, codes, shape=(4, 4), factor=2).tolist() == [[2, 1], [0, 1]]


def test_a_map_cell_shows_the_mean_of_its_add_back_pixels_and_nan_without_any():
    rows, cols = np.array([0, 1, 3]), np.array([0, 0, 3])
    mean = coarse_mean(rows, cols, np.array([4.0, 8.0, 3.0]), shape=(4, 4), factor=2)
    assert mean[0, 0] == 6.0 and mean[1, 1] == 3.0
    assert np.isnan(mean[0, 1]) and np.isnan(mean[1, 0])


def test_a_category_code_is_its_place_in_the_slot_order_not_the_alphabet():
    order = ["obata", "hansen", "stratum_median"]          # slot order, deliberately unsorted
    labels = np.array(["stratum_median", "obata", "hansen", "obata", "unknown"])
    assert category_codes(labels, order).tolist() == [3, 1, 2, 1, 0]
