"""
Read an FVS Online output database into one tidy stand-year frame.

An `FVSOut.db` written by FVS Online holds the run in two tables this stage needs:

    FVS_Cases      one row per (stand, management ID) case — StandID, Stand_CN, SamplingWt
    FVS_Summary2   one row per (case, cycle year) — Age, ForTyp, BA, Tpa, QMD, TopHt, ...

`load_stand_years` joins them, decodes the geography the StandID carries, and hands back a
frame whose acre weight and identifiers are already correct to group on. Two things it
refuses to do quietly:

* **Identifier precision.** `Stand_CN` is a FIA control number up to 19 digits. SQLite
  hands a REAL-typed column back as a float, which damages it. The query casts on the
  stored value (`CAST(Stand_CN AS TEXT)`) and the result goes through
  `pipeline.ids.as_id_series`, which raises rather than return a plausible-looking key
  that joins to nothing. See `pipeline/ids.py`.

* **Unbalanced cycles.** Stands enter an FVS Online run at their own FIA inventory year,
  so the early years hold whichever stands happened to be measured then.
  `balanced_years` reports the years every case covers, and `reporting_years` applies the
  `year_grid` policy from `config/fvs_outputs.yaml`.

Usage:
    from pipeline.s6_outputs import fvs_out_db

    stand_years = fvs_out_db.load_stand_years("/mnt/d/Artemis_project_fvs_copy/FVSOut.db")
    grid = fvs_out_db.reporting_years(stand_years)
"""

from __future__ import annotations

import logging
import sqlite3
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from pipeline.ids import as_id_series

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "fvs_outputs.yaml"

# FVS StandID built from FIA: STATECD(2) + INVYR(2) + COUNTYCD(3) + PLOT(5).
STAND_ID_WIDTH = 12

# Columns pulled from FVS_Summary2. Age and ForTyp carry the report; the stand metrics
# ride along so a figure can be cut by structure without a second read of the database.
_SUMMARY_COLUMNS = (
    "Year", "Age", "Tpa", "BA", "QMD", "TopHt", "SDI", "ForTyp", "SizeCls", "StkCls",
    "MCuFt", "RmvCode",
)

REQUIRED_TABLES = ("FVS_Cases", "FVS_Summary2")


class FvsOutputError(RuntimeError):
    """The output database is not one this stage can report on."""


@lru_cache(maxsize=None)
def load_output_config(path: str | None = None) -> dict:
    """Load and cache `config/fvs_outputs.yaml`."""
    with open(path or CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {name for (name,) in rows}


def load_cases(conn: sqlite3.Connection) -> pd.DataFrame:
    """One row per FVS case, with `Stand_CN` read as an exact string."""
    cases = pd.read_sql_query(
        """
        SELECT CaseID,
               CAST(Stand_CN AS TEXT) AS Stand_CN,
               CAST(StandID  AS TEXT) AS StandID,
               MgmtID, RunTitle, Variant, Groups, SamplingWt
        FROM FVS_Cases
        """,
        conn,
    )
    cases["Stand_CN"] = as_id_series(cases["Stand_CN"], column="Stand_CN")
    return cases


def load_summary(conn: sqlite3.Connection) -> pd.DataFrame:
    """One row per (case, cycle year) from FVS_Summary2."""
    columns = ", ".join(_SUMMARY_COLUMNS)
    return pd.read_sql_query(f"SELECT CaseID, {columns} FROM FVS_Summary2", conn)


def decode_stand_id(stand_ids: pd.Series) -> pd.DataFrame:
    """
    Split an FIA-built FVS StandID into the geography it encodes.

    `010012900136` is state 01, inventory year 2000, county 129, plot 00136. A StandID of
    some other width is not one of these — its fields come back as NA rather than as a
    slice of the wrong characters.
    """
    text = stand_ids.astype("string").str.strip()
    decodable = text.str.fullmatch(r"\d{%d}" % STAND_ID_WIDTH).fillna(False)
    if not decodable.all():
        logger.warning(
            "%d of %d StandIDs are not %d digits; their state/county fields are NA",
            (~decodable).sum(), len(text), STAND_ID_WIDTH,
        )
    field = text.where(decodable)
    two_digit_year = pd.to_numeric(field.str[2:4], errors="coerce")
    return pd.DataFrame({
        "state_fips": field.str[0:2],
        "county_fips": field.str[0:2] + field.str[4:7],
        "plot_number": field.str[7:12],
        # FIA inventories run from 1990; a two-digit year is 19xx only above that.
        "inventory_year": (two_digit_year + 2000).where(two_digit_year <= 90,
                                                        two_digit_year + 1900),
    }, index=stand_ids.index)


def load_stand_years(db_path: str | Path, *, config: dict | None = None) -> pd.DataFrame:
    """
    The run as one stand-year frame: case attributes, cycle metrics, decoded geography.

    Keep code 0 for unmanaged cycles and code 2 for the post-removal state of managed
    cycles. Code 1 is the pre-removal state and must not contribute additional acres.
    Each stand must have one case so management alternatives cannot inflate acreage.
    """
    config = config or load_output_config()
    db_path = Path(db_path)
    if not db_path.exists():
        raise FvsOutputError(f"FVS output database not found: {db_path}")

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        missing = [t for t in REQUIRED_TABLES if t not in _table_names(conn)]
        if missing:
            raise FvsOutputError(
                f"{db_path} has no {', '.join(missing)} table. An FVS Online run writes "
                "these only when the Summary2 output is requested; re-run the project "
                "with that output enabled."
            )
        cases = load_cases(conn)
        summary = load_summary(conn)

    validate_landscape_cases(cases)
    summary = select_cycle_states(summary)

    stand_years = summary.merge(cases, on="CaseID", how="inner", validate="many_to_one")
    orphans = len(summary) - len(stand_years)
    if orphans:
        raise FvsOutputError(
            f"{orphans} FVS_Summary2 rows name a CaseID absent from FVS_Cases; "
            f"{db_path} is incomplete."
        )

    stand_years = pd.concat([stand_years, decode_stand_id(stand_years["StandID"])], axis=1)

    if config["weights"]["drop_zero_weight"]:
        weightless = stand_years["SamplingWt"].fillna(0) <= 0
        if weightless.any():
            cases_dropped = stand_years.loc[weightless, "CaseID"].nunique()
            logger.warning(
                "dropping %d rows from %d case(s) with no sampling weight: they cannot be "
                "expanded to acres", int(weightless.sum()), cases_dropped,
            )
            stand_years = stand_years[~weightless]

    return stand_years.reset_index(drop=True)


def validate_landscape_cases(frame: pd.DataFrame) -> None:
    """Reject multiple case trajectories for one stand, including disjoint year grids."""
    cases = frame[["StandID", "CaseID"]].drop_duplicates()
    ambiguous = cases.groupby("StandID", dropna=False)["CaseID"].nunique(dropna=False)
    ambiguous = ambiguous[ambiguous > 1]
    if not ambiguous.empty:
        raise FvsOutputError(
            f"{len(ambiguous)} stand(s) have multiple FVS cases. S6 requires one case "
            "per stand; select one management alternative per stand in the input "
            "database before reporting. Alternatives cannot be added as landscape acres."
        )


def select_cycle_states(summary: pd.DataFrame) -> pd.DataFrame:
    """Choose post-removal code 2 over code 0 and reject missing or duplicate states."""
    keys = ["CaseID", "Year"]
    if not summary["RmvCode"].isin([0, 1, 2]).all():
        raise FvsOutputError("FVS_Summary2 has an unsupported RmvCode")
    states = summary[summary["RmvCode"].isin([0, 2])].copy()
    if states.duplicated([*keys, "RmvCode"]).any():
        raise FvsOutputError("FVS_Summary2 has duplicate cycle states for a case-year")
    states = states.sort_values("RmvCode").drop_duplicates(keys, keep="last")
    if len(states) != len(summary[keys].drop_duplicates()):
        raise FvsOutputError("FVS_Summary2 has a pre-removal cycle without a final state")
    logger.info("excluded %d pre-removal or superseded cycle rows", len(summary) - len(states))
    return states


def balanced_years(stand_years: pd.DataFrame) -> list[int]:
    """Years in which every case in the run reports."""
    cases = stand_years["CaseID"].nunique()
    per_year = stand_years.groupby("Year")["CaseID"].nunique()
    return sorted(int(year) for year in per_year[per_year == cases].index)


def reporting_years(stand_years: pd.DataFrame, *, config: dict | None = None) -> list[int]:
    """
    The year grid the canonical tables are computed on, per the `year_grid` policy.

    In `balanced` mode a run whose cases never line up is an error rather than a thin
    report: an age-class distribution over an unbalanced year describes which stands were
    measured that year, not the landscape.
    """
    config = config or load_output_config()
    policy = config["year_grid"]
    if policy["mode"] == "all":
        return sorted(int(y) for y in stand_years["Year"].unique())
    if policy["mode"] != "balanced":
        raise FvsOutputError(f"unknown year_grid.mode {policy['mode']!r}")

    years = balanced_years(stand_years)
    if len(years) < policy["min_years"]:
        raise FvsOutputError(
            f"only {len(years)} year(s) are covered by all "
            f"{stand_years['CaseID'].nunique()} cases, fewer than the {policy['min_years']} "
            "config/fvs_outputs.yaml requires. The run's cycles do not line up; report it "
            "with year_grid.mode: all and say so, or re-run with a common start year."
        )
    return years


def run_identity(stand_years: pd.DataFrame) -> dict:
    """The provenance block every emitted table and figure is stamped with."""
    return {
        "run_title": sorted(stand_years["RunTitle"].dropna().unique().tolist()),
        "variant": sorted(stand_years["Variant"].dropna().unique().tolist()),
        "mgmt_ids": sorted(stand_years["MgmtID"].dropna().unique().tolist()),
        "cases": int(stand_years["CaseID"].nunique()),
        "stands": int(stand_years["StandID"].nunique()),
        "first_year": int(stand_years["Year"].min()),
        "last_year": int(stand_years["Year"].max()),
        "sampling_weight_acres": float(
            stand_years.drop_duplicates("CaseID")["SamplingWt"].sum()
        ),
    }
