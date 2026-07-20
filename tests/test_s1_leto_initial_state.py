"""Tests for LETO tabular initial-state transformations."""

from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.leto_initial_state import (
    build_management_unit_crosswalk,
    filter_and_normalize_weights,
    load_fia_tree_files,
    load_species_lookup,
)


@pytest.fixture
def management_units():
    return pd.DataFrame(
        {
            "MU_ID": [1, 2],
            "Acres": [100.0, 50.0],
            "OWN_CODE": [4, 5],
            "OWN_TYPE": ["family", "corporate"],
            "SMZ_Pct": [10.0, 5.0],
        }
    )


@pytest.fixture
def plot_weights():
    return pd.DataFrame(
        {
            "MU_ID": ["1", "1", "1", "2", "2"],
            "TM_VALUE": [11, 12, 13, 21, 22],
            "CELL_COUNT": [80, 15, 5, 96, 4],
            "TOTAL_CELLS": [100, 100, 100, 100, 100],
            "WEIGHT": [0.80, 0.15, 0.05, 0.96, 0.04],
            "PLT_CN": ["101", "102", "103", "201", "202"],
        }
    )


def test_crosswalk_uses_majority_plot_and_legacy_columns(management_units, plot_weights):
    crosswalk = build_management_unit_crosswalk(management_units, plot_weights)

    assert crosswalk.columns.tolist() == [
        "Stand_ID",
        "MU_ID",
        "Acres",
        "PLT_CN",
        "OWN_CODE",
        "OWN_TYPE",
        "SMZ_Pct",
    ]
    assert crosswalk["Stand_ID"].tolist() == ["1", "2"]
    assert crosswalk["MU_ID"].tolist() == ["1", "2"]
    assert crosswalk["PLT_CN"].tolist() == ["101", "201"]


def test_weights_keep_threshold_boundary_and_renormalize(
    management_units, plot_weights
):
    crosswalk = build_management_unit_crosswalk(management_units, plot_weights)

    normalized = filter_and_normalize_weights(plot_weights, crosswalk)

    unit_one = normalized.loc[normalized["MU_ID"] == "1"]
    unit_two = normalized.loc[normalized["MU_ID"] == "2"]
    assert unit_one["PLT_CN"].tolist() == ["101", "102", "103"]
    assert unit_one["WEIGHT"].tolist() == pytest.approx([0.80, 0.15, 0.05])
    assert unit_two["PLT_CN"].tolist() == ["201"]
    assert unit_two["WEIGHT"].tolist() == pytest.approx([1.0])
    assert unit_two.iloc[0]["Stand_ID"] == "2"
    assert unit_two.iloc[0]["OWN_TYPE"] == "corporate"


def test_load_species_lookup_normalizes_fia_codes(tmp_path):
    workbook = tmp_path / "species.xlsx"
    pd.DataFrame(
        {"FIA CODE": [7, 131], "SN_Mapped_To": ["OS", "LP"]}
    ).to_excel(workbook, sheet_name="EasternSpeciesTranslator", index=False)

    lookup = load_species_lookup(workbook, "EasternSpeciesTranslator")

    assert lookup == {"007": "OS", "131": "LP"}


def test_load_fia_tree_files_combines_states_and_preserves_plot_ids(tmp_path):
    florida = tmp_path / "FL_TREE.csv"
    georgia = tmp_path / "GA_TREE.csv"
    pd.DataFrame({"PLT_CN": ["9007199254740993"], "CN": ["fl-tree"]}).to_csv(
        florida, index=False
    )
    pd.DataFrame({"PLT_CN": ["9007199254740995"], "CN": ["ga-tree"]}).to_csv(
        georgia, index=False
    )

    trees = load_fia_tree_files([florida, georgia])

    assert trees["PLT_CN"].tolist() == ["9007199254740993", "9007199254740995"]
    assert trees["CN"].tolist() == ["fl-tree", "ga-tree"]


def test_crosswalk_reports_missing_management_unit_columns(plot_weights):
    units = pd.DataFrame({"MU_ID": [1], "Acres": [10.0]})

    with pytest.raises(ValueError, match="Management units missing columns"):
        build_management_unit_crosswalk(units, plot_weights)


def test_crosswalk_rejects_duplicate_management_units(plot_weights):
    units = pd.DataFrame(
        {
            "MU_ID": [1, 1],
            "Acres": [10.0, 10.0],
            "OWN_CODE": [4, 4],
            "OWN_TYPE": ["family", "family"],
            "SMZ_Pct": [0.0, 0.0],
        }
    )

    with pytest.raises(ValueError, match="MU_ID values must be non-null and unique"):
        build_management_unit_crosswalk(units, plot_weights)


def test_load_fia_tree_files_requires_at_least_one_path():
    with pytest.raises(ValueError, match="At least one FIA TREE.csv path is required"):
        load_fia_tree_files([])


def test_load_species_lookup_rejects_conflicting_mappings(tmp_path):
    workbook = tmp_path / "species.xlsx"
    pd.DataFrame(
        {"FIA CODE": [131, 131], "SN_Mapped_To": ["LP", "LL"]}
    ).to_excel(workbook, sheet_name="EasternSpeciesTranslator", index=False)

    with pytest.raises(
        ValueError, match="one FIA code to multiple FVS species"
    ):
        load_species_lookup(workbook, "EasternSpeciesTranslator")
