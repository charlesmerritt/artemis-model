"""Compare restart-fidelity spike arms with DuckDB.

Each arm writes its own SQLite FVSOut.db. DuckDB attaches them all and diffs
in one query, so no arm is ever loaded into memory wholesale.

Carbon is diffed separately from base metrics on purpose: a summary-only
comparison would show a restart arm passing while FVS_Carbon is silently
corrupt, which is the exact failure this spike exists to catch.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

SUMMARY = "FVS_Summary2"
CARBON = "FVS_Carbon"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CarbonTableMissing(RuntimeError):
    """Raised when an arm has no FVS_Carbon table.

    Never downgrade this to a warning: absent carbon must not be mistaken for
    unchanged carbon.
    """


class NoComparableRows(RuntimeError):
    """Raised when a diff join matched no rows.

    A 0-row diff reads like "no differences found" but actually means the
    comparison had no basis at all. Same principle as CarbonTableMissing:
    absent evidence must not be mistaken for evidence of agreement.
    """


def _ident(name: str) -> str:
    """Quote a value used as a SQL identifier, rejecting anything unquotable.

    ATTACH aliases and column names cannot be bind parameters, so they are
    spliced into SQL text. Callers pass hardcoded arm letters today, but a CLI
    wrapper would make that an injection vector -- validate at the boundary.
    """
    if not _IDENT_RE.match(name):
        raise ValueError(f"not a usable SQL identifier: {name!r}")
    return f'"{name}"'


def _literal(value: str) -> str:
    """Quote a value used as a SQL string literal (paths, pool labels)."""
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def attach_arms(con: duckdb.DuckDBPyConnection, arm_dbs: dict[str, Path]) -> None:
    """Attach each arm's SQLite output under its own alias."""
    con.execute("INSTALL sqlite; LOAD sqlite;")
    for alias, db in arm_dbs.items():
        con.execute(f"ATTACH {_literal(str(db))} AS {_ident(alias)} (TYPE sqlite, READ_ONLY);")


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


def _check_joined(df: pd.DataFrame, left: str, right: str, table: str) -> pd.DataFrame:
    """Reject an empty diff: no matching rows is a failed comparison, not a pass."""
    if df.empty:
        raise NoComparableRows(
            f"joining {left!r} and {right!r} on {table} matched no rows - the arms "
            f"share no (StandID, Year) basis. Common causes: an arm died early, "
            f"StandID formatting differs, or (for {SUMMARY}) one arm is managed "
            f"(RmvCode 1/2) and the other is not (RmvCode 0)."
        )
    return df


def diff_summary(con: duckdb.DuckDBPyConnection, left: str, right: str) -> pd.DataFrame:
    """Per-year base-metric deltas (left - right).

    RmvCode is part of the join key, not just StandID/Year: FVS_Summary2 emits
    TWO rows for a cut year -- RmvCode 1 (pre-removal) and 2 (post-removal) --
    against 0 for an unmanaged year. Joining on Year alone cross-joins pre
    against post and manufactures large false deltas between arms that are in
    fact identical. See outputs/gate_cut_injection.txt.
    """
    df = con.execute(
        f"""
        SELECT l.Year AS Year,
               l.RmvCode AS RmvCode,
               l.BA  - r.BA  AS ba_delta,
               l.Tpa - r.Tpa AS tpa_delta,
               l.SDI - r.SDI AS sdi_delta
        FROM {_ident(left)}.{SUMMARY} l
        JOIN {_ident(right)}.{SUMMARY} r USING (StandID, Year, RmvCode)
        ORDER BY l.Year, l.RmvCode
        """
    ).df()
    return _check_joined(df, left, right, SUMMARY)


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

    # FVS_Carbon has no RmvCode -- one row per (StandID, Year) -- so unlike
    # FVS_Summary2 there is no pre/post pair to disambiguate here.
    unions = "\nUNION ALL\n".join(
        f"""SELECT l.Year AS Year, {_literal(p)} AS pool,
                   l.{_ident(p)} AS left_value, r.{_ident(p)} AS right_value,
                   l.{_ident(p)} - r.{_ident(p)} AS delta
            FROM {_ident(left)}.{CARBON} l
            JOIN {_ident(right)}.{CARBON} r USING (StandID, Year)"""
        for p in pools
    )
    df = con.execute(f"SELECT * FROM ({unions}) ORDER BY Year, pool").df()
    return _check_joined(df, left, right, CARBON)
