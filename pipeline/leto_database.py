"""LETO stage 5: the FVS input databases, one per DB_GROUP (owner type, or Riparian), one for
every stand, or both.

Ports aauslander480/Leto `LETO_v1_project/leto/stage5_database.py` (`prepare_stands`,
`prepare_trees`, `safe_filename`), `pipeline._build_databases` with its `database.mode`
(:class:`DatabaseMode`, `leto/pipeline.py:160-165` at 13bcf13), and
`stage2.finalize_database_groups`.

TREE_COUNT is already trees per acre, so every stand is one 1-acre plot:
BASAL_AREA_FACTOR -1, INV_PLOT_SIZE 1, BRK_DBH 0, NUM_PLOTS 1, NONSTK_PLOTS 0.

LETO copies a blank `FVS_Data.db` and fills its FVS_StandInit and FVS_TreeInit tables. That
template is not in this repo, so the two tables are written into a fresh SQLite file; FVS reads
them by name (`regime_templates.render_keyfile(stand_table=..., tree_table=...)`).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pandas as pd

from pipeline.ids import as_id_series
from pipeline.leto_riparian import RIPARIAN

RIPARIAN_GROUP = "Riparian"
UNKNOWN_GROUP = "Unknown"
STAND_GROUP_NAME = "All_Stands"
STAND_TABLE = "FVS_StandInit"
TREE_TABLE = "FVS_TreeInit"
FLORIDA_STATE_CODE = 12


class DatabaseMode(StrEnum):
    """Upstream's ``database.mode``: which databases :func:`build_databases` writes."""

    GROUPED = "grouped"                       # one per DB_GROUP
    COMBINED = "combined"                     # one holding every stand, named All_Stands
    BOTH = "both"                             # the groups, then the combined one

    @property
    def grouped(self) -> bool:
        return self is not DatabaseMode.COMBINED

    @property
    def combined(self) -> bool:
        return self is not DatabaseMode.GROUPED


@dataclass(frozen=True)
class SamplingDesign:
    """FVS plot design; the defaults are LETO's verified TPA design."""

    basal_area_factor: float = -1
    inv_plot_size: float = 1
    brk_dbh: float = 0
    num_plots: int = 1
    nonstk_plots: int = 0


def database_groups(owner_type: pd.Series, riparian: pd.Series) -> pd.Series:
    """Owner type (Unknown when blank), or Riparian for riparian units."""
    groups = owner_type.astype("string").str.strip().replace("", pd.NA).fillna(UNKNOWN_GROUP)
    return groups.where(riparian.to_numpy() != RIPARIAN, RIPARIAN_GROUP).astype(object)


def safe_filename(value) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value).strip())
    return text.strip("_") or UNKNOWN_GROUP


def prepare_stands(stands: pd.DataFrame, *, design: SamplingDesign = SamplingDesign(),
                   variant: str = "SN", state_code: int = FLORIDA_STATE_CODE,
                   default_inv_year: int = 2022) -> pd.DataFrame:
    missing = {"STAND_ID", "INV_YEAR", "MU_ID", "AGE", "DB_GROUP"} - set(stands.columns)
    if missing:
        raise ValueError(f"FVS stand rows are missing fields: {sorted(missing)}")
    out = pd.DataFrame(index=stands.index)
    out["STAND_ID"] = stands["STAND_ID"]
    out["STAND_CN"] = stands["STAND_ID"]
    out["VARIANT"] = variant
    out["INV_YEAR"] = (pd.to_numeric(stands["INV_YEAR"], errors="coerce")
                       .fillna(default_inv_year).astype(int))
    out["STATE"] = state_code
    out["GROUPS"] = STAND_GROUP_NAME
    out["LOCATION"] = as_id_series(stands["MU_ID"])
    out["AGE"] = pd.to_numeric(stands["AGE"], errors="coerce").round().astype("Int64")
    out["BASAL_AREA_FACTOR"] = design.basal_area_factor
    out["INV_PLOT_SIZE"] = design.inv_plot_size
    out["BRK_DBH"] = design.brk_dbh
    out["NUM_PLOTS"] = design.num_plots
    out["NONSTK_PLOTS"] = design.nonstk_plots
    return out.reset_index(drop=True)


def prepare_trees(trees: pd.DataFrame) -> pd.DataFrame:
    missing = ({"STAND_ID", "TREE_COUNT", "SPECIES", "DIAMETER", "HT", "CRRATIO"}
               - set(trees.columns))
    if missing:
        raise ValueError(f"FVS tree rows are missing fields: {sorted(missing)}")
    out = pd.DataFrame(index=trees.index)
    out["STAND_ID"] = trees["STAND_ID"]
    out["STAND_CN"] = trees["STAND_ID"]
    out["PLOT_ID"] = 1
    out["STANDPLOT_ID"] = as_id_series(trees["STAND_ID"]) + "_1"
    out["STANDPLOT_CN"] = out["STANDPLOT_ID"]
    out["TREE_ID"] = trees.groupby("STAND_ID").cumcount() + 1
    out["TREE_COUNT"] = pd.to_numeric(trees["TREE_COUNT"], errors="coerce")
    out["SPECIES"] = trees["SPECIES"].astype(str)
    out["DIAMETER"] = pd.to_numeric(trees["DIAMETER"], errors="coerce")
    out["HT"] = pd.to_numeric(trees["HT"], errors="coerce")
    out["CRRATIO"] = pd.to_numeric(trees["CRRATIO"], errors="coerce")
    required = ["STAND_ID", "STAND_CN", "STANDPLOT_CN", "STANDPLOT_ID", "PLOT_ID", "TREE_ID",
                "TREE_COUNT", "SPECIES", "DIAMETER"]
    out = out.dropna(subset=required)
    return out[required + ["HT", "CRRATIO"]].reset_index(drop=True)


def write_database(path: Path, stand_rows: pd.DataFrame, tree_rows: pd.DataFrame) -> None:
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as con:
        stand_rows.to_sql(STAND_TABLE, con, index=False)
        tree_rows.to_sql(TREE_TABLE, con, index=False)
        con.execute(f"CREATE INDEX idx_tree_stand ON {TREE_TABLE}(STAND_CN)")


def database_path(out_dir: Path, group: str) -> Path:
    return Path(out_dir) / f"FVS_Inventory_{safe_filename(group)}.db"


def build_databases(final_stands: pd.DataFrame, trees: pd.DataFrame, out_dir: Path | str, *,
                    mode: DatabaseMode = DatabaseMode.GROUPED, combined_path: Path | None = None,
                    design: SamplingDesign = SamplingDesign(), variant: str = "SN",
                    state_code: int = FLORIDA_STATE_CODE) -> pd.DataFrame:
    """Write the ``mode``'s databases; return one summary row per database.

    Grouped: ``FVS_Inventory_<group>.db`` per DB_GROUP in ``out_dir``. Combined: every stand in
    ``FVS_Inventory_All_Stands.db`` there, or at ``combined_path`` when given.
    """
    out_dir = Path(out_dir)
    stands = final_stands.copy()
    stands["DB_GROUP"] = (stands["DB_GROUP"].astype("string").str.strip().replace("", pd.NA)
                          .fillna(UNKNOWN_GROUP))
    databases: list[tuple[str, pd.DataFrame, Path]] = []
    if mode.grouped:
        databases += [(group, frame, database_path(out_dir, group))
                      for group, frame in stands.groupby("DB_GROUP", sort=True)]
    if mode.combined:
        databases.append((STAND_GROUP_NAME, stands,
                          Path(combined_path) if combined_path else database_path(out_dir, STAND_GROUP_NAME)))
    tree_ids = as_id_series(trees["STAND_ID"])
    rows = []
    for group, group_stands, path in databases:
        ids = set(as_id_series(group_stands["STAND_ID"]))
        stand_rows = prepare_stands(group_stands, design=design, variant=variant,
                                    state_code=state_code)
        tree_rows = prepare_trees(trees[tree_ids.isin(ids)])
        path.parent.mkdir(parents=True, exist_ok=True)
        write_database(path, stand_rows, tree_rows)
        rows.append({"DB_GROUP": group, "STANDS": len(stand_rows),
                     "ACRES": float(pd.to_numeric(group_stands["Acres"], errors="coerce").sum()),
                     "TREE_RECORDS": len(tree_rows), "OUTPUT_DB": str(path)})
    summary = pd.DataFrame(rows)
    grouped = summary[summary["DB_GROUP"] != STAND_GROUP_NAME] if mode is DatabaseMode.BOTH else summary
    if int(grouped["STANDS"].sum()) != stands["STAND_ID"].nunique():
        raise RuntimeError("Database stand counts do not equal unique final stands.")
    return summary
