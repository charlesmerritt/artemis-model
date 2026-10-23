"""`build_fvs_inputs` production path: LETO v1 Stage 4 (inventory) then Stage 5 (databases)."""

import sqlite3

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from pipeline.leto_riparian import RIPARIAN, UPLAND
from pipeline.s4_fvs import build_fvs_inputs as bfi

LOOKUP = {"131": "LP"}


def _units():
    return gpd.GeoDataFrame({
        "MU_ID": [1, 2], "Acres": [4.0, 1.0], "STDAGE_MEAN": [20.0, 30.0],
        "OWNER_CLASS": ["private_family", "private_family"], "RIPARIAN": [UPLAND, RIPARIAN]},
        geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10)], crs="EPSG:5070")


def _weights():
    return pd.DataFrame({"MU_ID": ["1", "2"], "PLT_CN": ["P1", "P1"], "WEIGHT": [1.0, 1.0]})


def _fia():
    return pd.DataFrame({"CN": ["a"], "PLT_CN": ["P1"], "STATUSCD": ["1"], "SPCD": ["131"],
                         "DIA": ["8"], "HT": ["50"], "ACTUALHT": ["50"], "CR": ["40"],
                         "TPA_UNADJ": ["6"]})


def test_db_group_comes_from_the_owner_field_and_riparian_flag():
    groups = bfi.with_database_groups(_units(), owner_field="OWNER_CLASS")["DB_GROUP"]
    assert groups.tolist() == ["private_family", "Riparian"]


def test_existing_db_group_is_kept():
    units = _units().assign(DB_GROUP=["A", "B"])
    assert bfi.with_database_groups(units, owner_field="OWNER_CLASS")["DB_GROUP"].tolist() == [
        "A", "B"]


def test_leto_fvs_inputs_write_inventory_csvs_and_one_database_per_group(tmp_path):
    inventory, summary = bfi.build_leto_fvs_inputs(
        _units(), _weights(), _fia(), LOOKUP, tmp_path, owner_field="OWNER_CLASS")
    assert inventory.final_stands["STAND_ID"].tolist() == ["MU_1", "MU_2"]
    assert summary["DB_GROUP"].tolist() == ["Riparian", "private_family"]
    assert (tmp_path / "FVS_TreeInit.csv").exists()
    assert (tmp_path / "QA_Imputation_Summary.csv").exists()
    with sqlite3.connect(tmp_path / "FVS_Inventory_private_family.db") as con:
        stand = pd.read_sql("SELECT * FROM FVS_StandInit", con).iloc[0]
        tree = pd.read_sql("SELECT * FROM FVS_TreeInit", con).iloc[0]
    assert (stand["STAND_ID"], stand["AGE"], stand["STATE"], stand["BASAL_AREA_FACTOR"]) == (
        "MU_1", 20, 12, -1)
    assert (tree["STANDPLOT_ID"], tree["TREE_COUNT"], tree["SPECIES"]) == ("MU_1_1", 6.0, "LP")
