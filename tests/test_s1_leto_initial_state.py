"""Tests for LETO tabular initial-state transformations."""

from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.leto_initial_state import (
    build_management_unit_crosswalk,
    build_initial_state,
    build_stand_rows,
    filter_and_normalize_weights,
    impute_missing_tree_rows,
    load_fia_tree_files,
    load_species_lookup,
    run_leto_initial_state,
    write_initial_state,
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


def test_imputation_requires_projected_crs():
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"]},
        geometry=[box(0, 0, 1, 1), box(2, 0, 3, 1)],
        crs="EPSG:4326",
    )
    crosswalk = pd.DataFrame({"MU_ID": ["1", "2"]})
    trees = pd.DataFrame({"STAND_ID": ["MU_1"], "MU_ID": ["1"]})

    with pytest.raises(ValueError, match="projected CRS"):
        impute_missing_tree_rows(units, crosswalk, trees)


def test_imputation_requires_a_runnable_donor():
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1"]}, geometry=[box(0, 0, 10, 10)], crs="EPSG:5070"
    )
    crosswalk = pd.DataFrame({"MU_ID": ["1"]})
    trees = pd.DataFrame(columns=["STAND_ID", "MU_ID"])

    with pytest.raises(ValueError, match="No runnable management unit"):
        impute_missing_tree_rows(units, crosswalk, trees)


def test_stand_rows_include_only_tree_bearing_units(management_units, plot_weights):
    crosswalk = build_management_unit_crosswalk(management_units, plot_weights)
    trees = pd.DataFrame({"STAND_ID": ["MU_1"], "MU_ID": ["1"]})

    stands = build_stand_rows(crosswalk, trees)

    assert stands["STAND_ID"].tolist() == ["MU_1"]
    assert stands["VARIANT"].tolist() == ["SN"]
    assert stands["INV_YEAR"].tolist() == [2022]
    assert stands["STATE"].tolist() == ["FL"]


def test_build_and_write_initial_state_outputs(tmp_path):
    units = gpd.GeoDataFrame(
        {
            "MU_ID": ["1", "2"],
            "Acres": [10.0, 20.0],
            "OWN_CODE": [4, 5],
            "OWN_TYPE": ["family", "corporate"],
            "SMZ_Pct": [0.0, 10.0],
        },
        geometry=[box(0, 0, 10, 10), box(100, 0, 110, 10)],
        crs="EPSG:5070",
    )
    weights = pd.DataFrame(
        {
            "MU_ID": ["1", "2"],
            "TM_VALUE": [10, 20],
            "CELL_COUNT": [1, 1],
            "TOTAL_CELLS": [1, 1],
            "WEIGHT": [1.0, 1.0],
            "PLT_CN": ["101", "201"],
        }
    )
    fia_trees = pd.DataFrame(
        {
            "CN": ["tree-1"],
            "PLT_CN": ["101"],
            "STATUSCD": ["1"],
            "INVYR": ["2020"],
            "SPCD": ["131"],
            "DIA": ["10"],
            "HT": ["50"],
            "ACTUALHT": ["50"],
            "CR": ["40"],
            "TPA_UNADJ": ["5"],
        }
    )

    tables = build_initial_state(units, weights, fia_trees, {"131": "LP"})
    paths = write_initial_state(tables, tmp_path / "outputs")

    assert tables.stands["STAND_ID"].tolist() == ["MU_1", "MU_2"]
    assert tables.trees["TREE_SOURCE"].tolist() == [
        "FIA_WEIGHTED_DIRECT",
        "IMPUTED_NEAREST",
    ]
    pd.testing.assert_frame_equal(tables.weights, weights)
    assert tables.missing_stands.empty
    assert {path.name for path in paths.values()} == {
        "MU_FVS_Crosswalk.csv",
        "MU_PLT_CN_Weights.csv",
        "FVS_StandInit.csv",
        "FVS_TreeInit.csv",
        "MU_FVS_Stands_No_Live_Trees.csv",
    }
    assert all(path.exists() for path in paths.values())


def test_run_leto_initial_state_reads_inputs_and_writes_outputs(tmp_path):
    units_path = tmp_path / "units.gpkg"
    gpd.GeoDataFrame(
        {
            "MU_ID": ["1"],
            "Acres": [10.0],
            "OWN_CODE": [4],
            "OWN_TYPE": ["family"],
            "SMZ_Pct": [0.0],
        },
        geometry=[box(0, 0, 30, 30)],
        crs="EPSG:5070",
    ).to_file(units_path, layer="management_units")

    treemap_path = tmp_path / "treemap.tif"
    with rasterio.open(
        treemap_path,
        "w",
        driver="GTiff",
        height=1,
        width=1,
        count=1,
        dtype="int32",
        crs="EPSG:5070",
        transform=from_origin(0, 30, 30, 30),
        nodata=-9999,
    ) as destination:
        destination.write(np.array([[10]], dtype="int32"), 1)

    lookup_path = tmp_path / "treemap_lookup.csv"
    pd.DataFrame({"VALUE": [10], "PLT_CN": ["9007199254740993"]}).to_csv(
        lookup_path, index=False
    )
    species_path = tmp_path / "species.xlsx"
    pd.DataFrame({"FIA CODE": [131], "SN_Mapped_To": ["LP"]}).to_excel(
        species_path, sheet_name="EasternSpeciesTranslator", index=False
    )
    trees_path = tmp_path / "FL_TREE.csv"
    pd.DataFrame(
        {
            "CN": ["tree-1"],
            "PLT_CN": ["9007199254740993"],
            "STATUSCD": ["1"],
            "INVYR": ["2020"],
            "SPCD": ["131"],
            "DIA": ["10"],
            "HT": ["50"],
            "ACTUALHT": ["50"],
            "CR": ["40"],
            "TPA_UNADJ": ["5"],
        }
    ).to_csv(trees_path, index=False)

    result = run_leto_initial_state(
        management_units_path=units_path,
        management_units_layer="management_units",
        treemap_path=treemap_path,
        treemap_lookup_path=lookup_path,
        species_crosswalk_path=species_path,
        species_crosswalk_sheet="EasternSpeciesTranslator",
        fia_tree_paths=[trees_path],
        output_dir=tmp_path / "outputs",
    )

    assert result.stands["STAND_ID"].tolist() == ["MU_1"]
    assert result.trees["PLT_CN"].tolist() == ["9007199254740993"]
    assert (tmp_path / "outputs" / "MU_PLT_CN_Weights.csv").exists()
