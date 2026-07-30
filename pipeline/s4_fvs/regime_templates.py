"""
FVS management-regime templates (Phase 3.1).

Renders per-stand FVS keyfiles for a small library of silvicultural regimes. Every harvest
is expressed with the **`ThinDBH` keyword** — the one management keyword already verified
against real FVS runs in this project (`research/restart_fidelity/make_cut_keyfiles.py`):

    ThinDBH   <year>   <min_dbh>   <max_dbh>   <proportion>   <species>

read in fixed 10-column fields, where ``proportion`` is the fraction of TPA removed across
the DBH window (min→max), species 0 = all. Building every regime from this one verified
keyword keeps the generated keyfiles trustworthy:

    - **no_management** — no cut (the baseline).
    - **clearcut** — remove all trees (proportion 1.0, DBH 0–999) at a target year.
    - **thin_from_below** — remove a proportion of the *small* trees (DBH 0–``max_dbh``).
    - **selection_harvest** — a light proportional thin repeated on an interval.
    - **plantation_rotation** — a commercial thin, then a final clearcut at rotation age.

Regeneration keywords (`PLANT`/`NATREGEN`), `ThinBBA`/shelterwood, and the FFE/carbon block
are intentionally **not** emitted here — they need their FVS field layouts verified first,
and are a documented follow-up. The schedule/DataBase scaffolding mirrors the verified
keyfiles exactly.

Usage:
    from pipeline.s4_fvs.regime_templates import render_keyfile
    key = render_keyfile(stand_id="MU_123", stand_cn="MU_123",
                         regime="thin_from_below", params={"year": 2032, "max_dbh": 8, "proportion": 0.4})
"""

from __future__ import annotations

from dataclasses import dataclass

# Default projection schedule (matches config/projection.yaml: 50 yr, 5-yr cycles from 2022).
DEFAULT_INV_YEAR = 2022
DEFAULT_CYCLE_YEARS = 5
DEFAULT_NUM_CYCLE = 10


@dataclass(frozen=True)
class ThinDBH:
    """One proportional thin across a DBH window (FVS ThinDBH keyword)."""
    year: int
    proportion: float
    min_dbh: float = 0.0
    max_dbh: float = 999.0
    species: int = 0

    def render(self) -> str:
        if not (0.0 <= self.proportion <= 1.0):
            raise ValueError(f"proportion must be in [0, 1], got {self.proportion}")
        return (
            f"{'ThinDBH':<10}"
            f"{self.year:>10d}"
            f"{self.min_dbh:>10.0f}"
            f"{self.max_dbh:>10.0f}"
            f"{self.proportion:>10.2f}"
            f"{self.species:>10.0f}"
        )


# ---- regime → list[ThinDBH] builders -------------------------------------------------

def no_management(params: dict) -> list[ThinDBH]:
    return []


def clearcut(params: dict) -> list[ThinDBH]:
    return [ThinDBH(year=int(params["year"]), proportion=1.0)]


def thin_from_below(params: dict) -> list[ThinDBH]:
    return [ThinDBH(
        year=int(params["year"]),
        proportion=float(params.get("proportion", 0.35)),
        min_dbh=0.0,
        max_dbh=float(params.get("max_dbh", 8.0)),
    )]


def selection_harvest(params: dict) -> list[ThinDBH]:
    """Light proportional thins every ``interval`` years across a window of years."""
    start = int(params["start_year"])
    end = int(params.get("end_year", start + 30))
    interval = int(params.get("interval", 10))
    proportion = float(params.get("proportion", 0.2))
    return [ThinDBH(year=y, proportion=proportion) for y in range(start, end + 1, interval)]


def plantation_rotation(params: dict) -> list[ThinDBH]:
    """A commercial thin from below, then a final clearcut at rotation age."""
    return [
        ThinDBH(year=int(params["thin_year"]), proportion=float(params.get("thin_proportion", 0.4)),
                max_dbh=float(params.get("thin_max_dbh", 8.0))),
        ThinDBH(year=int(params["clearcut_year"]), proportion=1.0),
    ]


REGIMES = {
    "no_management": no_management,
    "clearcut": clearcut,
    "thin_from_below": thin_from_below,
    "selection_harvest": selection_harvest,
    "plantation_rotation": plantation_rotation,
}


def build_thins(regime: str, params: dict) -> list[ThinDBH]:
    """Return the ordered ThinDBH operations for a regime."""
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime!r}; choices: {sorted(REGIMES)}")
    return REGIMES[regime](params)


_KEYFILE = """\
!!title: {stand_id}_{regime}
StdIdent
{stand_id}               {regime}
StandCN
{stand_cn}
MgmtId
{mgmt_id}
InvYear   {inv_year:>10d}
TimeInt   {cycle_years:>10d}
NumCycle  {num_cycle:>10d}
{thin_block}
DataBase
DSNOut
{out_db}
Summary        2
End

Database
DSNIn
{in_db}
StandSQL
SELECT * FROM {stand_table} WHERE Stand_CN = '%Stand_CN%'
EndSQL
TreeSQL
SELECT * FROM {tree_table} WHERE Stand_CN = '%Stand_CN%'
EndSQL
END
SPLabel
  {sp_label}
Process
"""


def render_keyfile(
    stand_id: str,
    stand_cn: str,
    regime: str,
    params: dict | None = None,
    *,
    mgmt_id: str = "A001",
    inv_year: int = DEFAULT_INV_YEAR,
    cycle_years: int = DEFAULT_CYCLE_YEARS,
    num_cycle: int = DEFAULT_NUM_CYCLE,
    out_db: str = "FVS_Out.db",
    in_db: str = "FVS_Data.db",
    stand_table: str = "FVS_StandInit_Plot",
    tree_table: str = "FVS_TreeInit_Plot",
    sp_label: str = "ARTEMIS",
) -> str:
    """Render a complete single-stand FVS keyfile for the given regime."""
    thins = build_thins(regime, params or {})
    thin_block = "\n".join(t.render() for t in thins)
    return _KEYFILE.format(
        stand_id=stand_id, stand_cn=stand_cn, regime=regime, mgmt_id=mgmt_id,
        inv_year=inv_year, cycle_years=cycle_years, num_cycle=num_cycle,
        thin_block=thin_block, out_db=out_db, in_db=in_db,
        stand_table=stand_table, tree_table=tree_table, sp_label=sp_label,
    )
