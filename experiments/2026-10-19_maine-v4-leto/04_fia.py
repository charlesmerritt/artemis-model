"""Step 4: FIA COND and TREE rows for every plot the Maine v4 raster references.

The plots are the TreeMap VAT's ``PLT_CN`` for every ``Value`` on the young-plots raster, plus
each young plot's ``PLT_CN`` from the VAT extension. LETO stage 1 reads them
(:func:`leto_attributes.plot_lookup`). ``PLT_CN`` is read and written as text, never through a
float (AGENTS.md). One scan per table, joined in SQLite against a temp table of the plots.

    uv run python experiments/2026-10-19_maine-v4-leto/04_fia.py
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

sys.path.insert(0, str(c.REPO))
from pipeline.ids import read_id_csv  # noqa: E402
from pipeline.s1_initial_state.add_back_ids import (  # noqa: E402
    EXTENSION_ID_COLUMNS,
    OUT_DIR,
    VAT_EXTENSION_CSV,
    read_vat,
)

YOUNG = c.RUN_DIR / OUT_DIR
PLOTS_TXT = c.WORK / "plt_cns.txt"
COLUMNS = {"COND": ("STATECD", "CONDID", "COND_STATUS_CD", "CONDPROP_UNADJ", "STDAGE", "FORTYPCD",
                    "BALIVE"),
           "TREE": ("CONDID", "STATUSCD", "DIA", "TPA_UNADJ")}
FIA_CSV = {"COND": c.WORK / "fia_cond.csv", "TREE": c.WORK / "fia_tree.csv"}


def referenced_plots() -> set[str]:
    with rasterio.open(YOUNG / "treemap2022_improved.tif") as src:
        values = src.read(1)
        values = np.unique(values[values != src.nodata])
    vat = read_vat(Path(str(c.TREEMAP_TIF) + ".vat.dbf"))
    plots = set(vat.loc[vat["Value"].isin({str(v) for v in values}), "PLT_CN"])
    extension = read_id_csv(YOUNG / VAT_EXTENSION_CSV, id_columns=EXTENSION_ID_COLUMNS)
    return plots | set(extension["PLT_CN"])


def extract(con: sqlite3.Connection, table: str) -> int:
    cols = ", ".join(f"t.{name}" for name in COLUMNS[table])
    rows = con.execute(f"SELECT CAST(t.PLT_CN AS TEXT), {cols} FROM {table} t "
                       "JOIN temp.need n ON n.cn = CAST(t.PLT_CN AS TEXT)")
    with open(FIA_CSV[table], "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(("PLT_CN",) + COLUMNS[table])
        kept = 0
        for row in rows:
            writer.writerow(row)
            kept += 1
    return kept


def main() -> None:
    plots = referenced_plots()
    PLOTS_TXT.write_text("\n".join(sorted(plots)) + "\n")
    print(f"{len(plots):,} plots", flush=True)
    record: dict = {"plots": len(plots)}
    con = sqlite3.connect(f"file:{c.FIADB}?mode=ro&immutable=1", uri=True)
    con.execute("CREATE TEMP TABLE need (cn TEXT PRIMARY KEY)")
    con.executemany("INSERT INTO temp.need VALUES (?)", ((p,) for p in sorted(plots)))
    for table in COLUMNS:
        with c.measured(table, record):
            record[f"{table}_rows"] = extract(con, table)
        print(record, flush=True)
    found = set(read_id_csv(FIA_CSV["COND"], id_columns=("PLT_CN",))["PLT_CN"])
    record["plots_without_cond"] = len(plots - found)
    c.write_json(c.WORK / "04_fia.json", record)
    print(record)


if __name__ == "__main__":
    main()
