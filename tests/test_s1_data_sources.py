"""Tests for production LETO data-source contracts."""

import sqlite3
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state import data_sources
from pipeline.s1_initial_state.data_sources import (
    ProductionDataPaths,
    load_fia_trees_sqlite,
    load_stand_age_by_plot,
    load_treemap_attributes,
    load_treemap_lookup,
    preflight_production_data,
)


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
        lambda path, read_geometry: pd.DataFrame(
            {"Value": [7], "PLT_CN": [223267700000001.0]}
        ),
    )
    result = load_treemap_lookup(dbf_path)
    assert result.to_dict("records") == [{"VALUE": 7, "PLT_CN": "223267700000001"}]


@pytest.mark.parametrize("bad_plot_id", [7.5, float("inf"), float("nan")])
def test_load_treemap_lookup_rejects_invalid_numeric_plot_ids(
    tmp_path, monkeypatch, bad_plot_id
):
    dbf_path = tmp_path / "lookup.dbf"
    dbf_path.touch()
    monkeypatch.setattr(
        data_sources,
        "read_dataframe",
        lambda path, read_geometry: pd.DataFrame(
            {"Value": [7], "PLT_CN": [bad_plot_id]}
        ),
    )

    with pytest.raises(ValueError, match="finite whole numbers"):
        load_treemap_lookup(dbf_path)


def test_load_fia_trees_sqlite_filters_plots_and_states(tmp_path):
    db_path = tmp_path / "fiadb.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table TREE (CN text, PLT_CN text, STATUSCD text, "
            "INVYR text, STATECD integer, SPCD text, DIA text, HT text, "
            "ACTUALHT text, CR text, TPA_UNADJ text)"
        )
        connection.executemany(
            "insert into TREE values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "t1",
                    "101",
                    "1",
                    "2022",
                    12,
                    "131",
                    "10",
                    "50",
                    "50",
                    "40",
                    "5",
                ),
                (
                    "t2",
                    "202",
                    "1",
                    "2022",
                    13,
                    "131",
                    "9",
                    "45",
                    "45",
                    "30",
                    "4",
                ),
            ],
        )
    result = load_fia_trees_sqlite(db_path, {"101"}, state_codes={12})
    assert result["PLT_CN"].tolist() == ["101"]
    assert result["STATECD"].tolist() == ["12"]


def test_load_treemap_attributes_renames_vat_columns_and_normalizes_plt_cn(
    tmp_path, monkeypatch
):
    dbf_path = tmp_path / "vat.dbf"
    dbf_path.touch()
    monkeypatch.setattr(
        data_sources,
        "read_dataframe",
        lambda path, read_geometry: pd.DataFrame({
            "Value": [10],
            "PLT_CN": [223267700000001.0],
            "FORTYPCD": [161],
            "BALIVE": [90.0],
            "QMD": [6.0],
            "TPA_LIVE": [300.0],
        }),
    )
    result = load_treemap_attributes(dbf_path)
    assert result.to_dict("records") == [{
        "VALUE": 10,
        "PLT_CN": "223267700000001",
        "FORTYPCD": 161.0,
        "BALIVE": 90.0,
        "QMD": 6.0,
        "TPA": 300.0,
    }]


def test_load_treemap_attributes_rejects_missing_vat_columns(tmp_path, monkeypatch):
    dbf_path = tmp_path / "vat.dbf"
    dbf_path.touch()
    monkeypatch.setattr(
        data_sources,
        "read_dataframe",
        lambda path, read_geometry: pd.DataFrame({"Value": [10], "PLT_CN": ["1"]}),
    )
    with pytest.raises(ValueError, match="missing columns"):
        load_treemap_attributes(dbf_path)


def test_load_stand_age_by_plot_takes_the_dominant_live_condition(tmp_path):
    db_path = tmp_path / "fia.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table COND (PLT_CN text, CONDID integer, STDAGE real, "
            "CONDPROP_UNADJ real, COND_STATUS_CD integer)"
        )
        connection.executemany(
            "insert into COND values (?, ?, ?, ?, ?)",
            [
                ("101", 1, 15.0, 0.3, 1),
                ("101", 2, 40.0, 0.7, 1),  # dominant: larger CONDPROP_UNADJ
                ("101", 3, 5.0, 1.0, 2),  # not live forest -- excluded
                ("202", 1, 80.0, 1.0, 1),
            ],
        )
    result = load_stand_age_by_plot(db_path, {"101", "202"})
    by_plot = dict(zip(result["PLT_CN"], result["STDAGE"]))
    assert by_plot == {"101": 40.0, "202": 80.0}


def test_load_stand_age_by_plot_returns_empty_frame_for_no_plots(tmp_path):
    db_path = tmp_path / "fia.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table COND (PLT_CN text, CONDID integer, STDAGE real, "
            "CONDPROP_UNADJ real, COND_STATUS_CD integer)"
        )
    result = load_stand_age_by_plot(db_path, [])
    assert list(result.columns) == ["PLT_CN", "STDAGE"]
    assert len(result) == 0
