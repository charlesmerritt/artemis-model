"""Stage 5 FVS databases pinned to LETO v1 `leto/stage5_database.py` and `pipeline._build_databases`
(aauslander480/Leto @ d2b92ee)."""

import sqlite3

import pandas as pd
import pytest

from pipeline import leto_database as db
from pipeline.leto_riparian import RIPARIAN, UPLAND


def _stands():
    return pd.DataFrame({"STAND_ID": ["MU_1", "MU_2", "MU_3"], "MU_ID": ["1", "2", "3"],
                         "INV_YEAR": [2022, 2022, 2022], "AGE": [12, 30, 55],
                         "Acres": [4.0, 1.0, 2.0],
                         "DB_GROUP": ["Family", "Riparian", "Family"]})


def _trees():
    return pd.DataFrame({"STAND_ID": ["MU_1", "MU_1", "MU_2", "MU_3"],
                         "TREE_COUNT": [1.5, 2.0, 3.0, 4.0], "SPECIES": ["LP", "SU", "LP", "LP"],
                         "DIAMETER": [8.0, 5.0, 6.0, 7.0], "HT": [50.0, None, 40.0, 45.0],
                         "CRRATIO": [40.0, 35.0, 30.0, 30.0]})


def test_database_group_is_the_owner_type_or_riparian():
    # stage2.finalize_database_groups
    groups = db.database_groups(pd.Series(["Family", None, "Corporate"]),
                                pd.Series([UPLAND, UPLAND, RIPARIAN]))
    assert groups.tolist() == ["Family", "Unknown", "Riparian"]


def test_default_sampling_design_reads_tree_count_as_trees_per_acre():
    # configs/maine_test.py "database"
    assert db.SamplingDesign() == db.SamplingDesign(
        basal_area_factor=-1, inv_plot_size=1, brk_dbh=0, num_plots=1, nonstk_plots=0)


def test_stand_rows_match_prepare_stands():
    out = db.prepare_stands(_stands())
    first = out.iloc[0]
    assert out.columns.tolist() == [
        "STAND_ID", "STAND_CN", "VARIANT", "INV_YEAR", "STATE", "GROUPS", "LOCATION", "AGE",
        "BASAL_AREA_FACTOR", "INV_PLOT_SIZE", "BRK_DBH", "NUM_PLOTS", "NONSTK_PLOTS"]
    assert (first["STAND_CN"], first["VARIANT"], first["STATE"], first["GROUPS"],
            first["LOCATION"], first["AGE"]) == ("MU_1", "SN", 12, "All_Stands", "1", 12)
    assert (first["BASAL_AREA_FACTOR"], first["INV_PLOT_SIZE"], first["BRK_DBH"],
            first["NUM_PLOTS"], first["NONSTK_PLOTS"]) == (-1, 1, 0, 1, 0)


def test_stand_rows_require_routing_fields():
    with pytest.raises(ValueError, match="DB_GROUP"):
        db.prepare_stands(_stands().drop(columns="DB_GROUP"))


def test_tree_rows_are_one_plot_per_stand_with_sequential_ids():
    out = db.prepare_trees(_trees())
    assert out.columns.tolist() == [
        "STAND_ID", "STAND_CN", "STANDPLOT_CN", "STANDPLOT_ID", "PLOT_ID", "TREE_ID",
        "TREE_COUNT", "SPECIES", "DIAMETER", "HT", "CRRATIO"]
    assert out["STANDPLOT_ID"].tolist() == ["MU_1_1", "MU_1_1", "MU_2_1", "MU_3_1"]
    assert out["TREE_ID"].tolist() == [1, 2, 1, 1]
    assert set(out["PLOT_ID"]) == {1}
    assert len(out) == 4  # a missing HT is kept; only required fields drop rows


def test_one_database_per_group_with_a_summary(tmp_path):
    summary = db.build_databases(_stands(), _trees(), tmp_path)
    assert summary["DB_GROUP"].tolist() == ["Family", "Riparian"]
    assert summary["STANDS"].tolist() == [2, 1]
    assert summary["ACRES"].tolist() == [6.0, 1.0]
    assert summary["TREE_RECORDS"].tolist() == [3, 1]
    with sqlite3.connect(tmp_path / "FVS_Inventory_Riparian.db") as con:
        stands = pd.read_sql("SELECT * FROM FVS_StandInit", con)
        trees = pd.read_sql("SELECT * FROM FVS_TreeInit", con)
    assert stands["STAND_ID"].tolist() == ["MU_2"]
    assert trees["STANDPLOT_ID"].tolist() == ["MU_2_1"]


def test_group_names_become_safe_filenames(tmp_path):
    stands = _stands().assign(DB_GROUP=["Family / Indiv.", "Riparian", "Family / Indiv."])
    db.build_databases(stands, _trees(), tmp_path)
    assert (tmp_path / "FVS_Inventory_Family_Indiv.db").exists()
