"""Compare restart-fidelity spike arms with DuckDB.

Each arm writes its own SQLite FVSOut.db. DuckDB attaches them all and diffs
in one query, so no arm is ever loaded into memory wholesale.

Carbon is diffed separately from base metrics on purpose: a summary-only
comparison would show a restart arm passing while FVS_Carbon is silently
corrupt, which is the exact failure this spike exists to catch.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

SUMMARY = "FVS_Summary2"
CARBON = "FVS_Carbon"


class CarbonTableMissing(RuntimeError):
    """Raised when an arm has no FVS_Carbon table.

    Never downgrade this to a warning: absent carbon must not be mistaken for
    unchanged carbon.
    """


def attach_arms(con: duckdb.DuckDBPyConnection, arm_dbs: dict[str, Path]) -> None:
    """Attach each arm's SQLite output under its own alias."""
    con.execute("INSTALL sqlite; LOAD sqlite;")
    for alias, db in arm_dbs.items():
        con.execute(f"ATTACH '{db}' AS {alias} (TYPE sqlite, READ_ONLY);")


def _has_table(con: duckdb.DuckDBPyConnection, alias: str, table: str) -> bool:
    rows = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_catalog = ? AND table_name = ?",
        [alias, table],
    ).fetchall()
    return len(rows) > 0


def assert_carbon_present(con: duckdb.DuckDBPyConnection, alias: str) -> None:
    if not _has_table(con, alias, CARBON):
        raise CarbonTableMissing(
            f"arm {alias!r} has no {CARBON} table - the run did not enable "
            f"FMIn/CarbRept + CarbReDB, so carbon cannot be compared"
        )


def diff_summary(con: duckdb.DuckDBPyConnection, left: str, right: str) -> pd.DataFrame:
    """Per-year base-metric deltas (left - right)."""
    return con.execute(
        f"""
        SELECT l.Year AS Year,
               l.BA  - r.BA  AS ba_delta,
               l.Tpa - r.Tpa AS tpa_delta,
               l.SDI - r.SDI AS sdi_delta
        FROM {left}.{SUMMARY} l
        JOIN {right}.{SUMMARY} r USING (StandID, Year)
        ORDER BY l.Year
        """
    ).df()


def diff_carbon(con: duckdb.DuckDBPyConnection, left: str, right: str) -> pd.DataFrame:
    """Per-year carbon-pool deltas (left - right), unpivoted to one row per pool."""
    assert_carbon_present(con, left)
    assert_carbon_present(con, right)

    pools = [
        c[0]
        for c in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_catalog = ? AND table_name = ? "
            "AND column_name NOT IN ('StandID', 'Year', 'CaseID')",
            [left, CARBON],
        ).fetchall()
    ]
    if not pools:
        raise CarbonTableMissing(f"arm {left!r} {CARBON} has no pool columns")

    unions = "\nUNION ALL\n".join(
        f"""SELECT l.Year AS Year, '{p}' AS pool,
                   l."{p}" AS left_value, r."{p}" AS right_value,
                   l."{p}" - r."{p}" AS delta
            FROM {left}.{CARBON} l
            JOIN {right}.{CARBON} r USING (StandID, Year)"""
        for p in pools
    )
    return con.execute(f"SELECT * FROM ({unions}) ORDER BY Year, pool").df()
