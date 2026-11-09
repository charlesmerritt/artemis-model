"""Assemble the one multi-state FIA SQLite that the young-plot library reads.

``pipeline.s1_initial_state.young_plot_library.read_young_plot_library`` opens a single
SQLite and filters ``STATECD IN (1, 12, 13)`` (Alabama, Florida, Georgia) in each of its
three queries, so it needs those three states in one file. The declared source,
``/mnt/d/SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db``, is 70.7 GB and does not fit this
sandbox's disk allowance, and R2 only offers per-state FIADB files beside it.

This copies the three tables that library reads -- PLOT (DESIGNCD), COND (STDAGE,
FORTYPCD, CONDPROP_UNADJ) and SEEDLING (SPCD, TPA_UNADJ) -- out of the per-state
databases into one file, each filtered to its own STATECD. Rows are copied verbatim:
no column is cast, so the control numbers keep every digit (AGENTS.md's FIA gotcha).

    python build_fia_al_fl_ga.py --states-dir <dir of SQLite_FIADB_{AL,FL,GA}.db> --out fia_al_fl_ga.db
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

STATES = {"AL": 1, "FL": 12, "GA": 13}
TABLES = ("PLOT", "COND", "SEEDLING")


def build(states_dir: Path, out: Path) -> None:
    if out.exists():
        out.unlink()
    con = sqlite3.connect(f"file:{out}", uri=True)
    try:
        first = True
        for code, statecd in STATES.items():
            src = states_dir / f"SQLite_FIADB_{code}.db"
            if not src.exists():
                raise FileNotFoundError(src)
            con.execute("ATTACH DATABASE ? AS src", (f"file:{src.resolve()}?mode=ro",))
            for table in TABLES:
                if first:
                    con.execute(f"CREATE TABLE {table} AS "
                                f"SELECT * FROM src.{table} WHERE STATECD = ?", (statecd,))
                else:
                    con.execute(f"INSERT INTO {table} "
                                f"SELECT * FROM src.{table} WHERE STATECD = ?", (statecd,))
            con.commit()
            con.execute("DETACH DATABASE src")
            first = False
            print(f"  {code} (STATECD {statecd}) copied")
        for table in TABLES:
            print(f"{table}: {con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:,} rows, "
                  f"states {[r[0] for r in con.execute(f'SELECT DISTINCT STATECD FROM {table} ORDER BY 1')]}")
        con.execute("CREATE INDEX cond_pltcn ON COND(PLT_CN)")
        con.execute("CREATE INDEX seed_pltcn ON SEEDLING(PLT_CN)")
        con.commit()
    finally:
        con.close()
    print(f"wrote {out} ({out.stat().st_size / 1e6:.0f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--states-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build(args.states_dir, args.out)


if __name__ == "__main__":
    main()
