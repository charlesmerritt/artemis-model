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
    load_leto_attribute_sources,
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


def test_load_leto_attribute_sources_reads_cond_and_tree_for_the_plots(tmp_path):
    db_path = tmp_path / "fia.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table COND (PLT_CN integer, CONDID integer, COND_STATUS_CD integer, "
            "CONDPROP_UNADJ real, STDAGE real, FORTYPCD integer, BALIVE real)")
        connection.executemany("insert into COND values (?, ?, ?, ?, ?, ?, ?)", [
            (1234567890123456789, 1, 1, 1.0, 40.0, 161, 90.0),
            (999, 1, 1, 1.0, 10.0, 161, 50.0)])  # not requested
        connection.execute("create table TREE (PLT_CN integer, CONDID integer, "
                           "STATUSCD integer, DIA real, TPA_UNADJ real)")
        connection.execute("insert into TREE values (1234567890123456789, 1, 1, 8.0, 6.0)")
    cond, trees = load_leto_attribute_sources(db_path, ["1234567890123456789"])
    assert cond["PLT_CN"].tolist() == ["1234567890123456789"]
    assert cond.loc[0, ["STDAGE", "FORTYPCD", "BALIVE"]].tolist() == [40.0, 161, 90.0]
    assert trees.loc[0, ["PLT_CN", "DIA", "TPA_UNADJ"]].tolist() == [
        "1234567890123456789", 8.0, 6.0]


def test_load_leto_attribute_sources_returns_empty_frames_for_no_plots(tmp_path):
    db_path = tmp_path / "fia.db"
    sqlite3.connect(db_path).close()
    cond, trees = load_leto_attribute_sources(db_path, [])
    assert cond.empty and "CONDPROP_UNADJ" in cond.columns
    assert trees.empty and "TPA_UNADJ" in trees.columns
