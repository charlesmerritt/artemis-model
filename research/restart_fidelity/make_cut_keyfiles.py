"""Keyfiles for the management-injection (gate) spike.

The gate proves that applying a cut at a barrier is faithful. It compares:
  G1  a scheduled ThinDBH (authoritative in-FVS proportional thin)
  G2  fvsCutNow(p) injected in-process at stop point 2
  G3  fvsCutNow(p) injected after a stop/restart barrier

fvsCutNow is documented as "implemented using the ThinPrsc keyword" and sets the
cut proportion of every tree record, so a ThinDBH spanning all diameters at the
same proportion is the matched native baseline (initre.f option 29: field 4 is
"PROPORTION OF SELECTED TREES REMOVED").

Carbon is out of scope (config carbon_extension=false), so these keyfiles omit
the FFE block entirely -- cleaner and faster than the restart-fidelity arms.
"""

from __future__ import annotations

# Reuse the verified single-stand fixture and schedule.
from research.restart_fidelity.make_keyfiles import (
    CYCLE_YEARS,
    INV_YEAR,
    MULTI_INV_YEAR,
    STAND_CN,
    STAND_ID,
)

_HEADER = "!!title: cut_spike_arm_{arm}\n"

_SCHEDULE = """\
StdIdent
{stand_id}               CutSpike_arm_{arm}
StandCN
{stand_cn}
MgmtId
A001
InvYear       {inv_year}
TimeInt                 {cycle_years}
NumCycle      {num_cycle}
"""

_OUTPUT_AND_INPUT = """\

DataBase
DSNOut
{out_db}
Summary        2
End

Database
DSNIn
FVS_Data.db
StandSQL
SELECT *
FROM FVS_StandInit_Plot
WHERE Stand_CN = '%Stand_CN%'
EndSQL
TreeSQL
SELECT *
FROM FVS_TreeInit_Plot
WHERE Stand_CN ='%Stand_CN%'
EndSQL
END
SPLabel
  All_FIA_Plots
Process
"""


def _thindbh_line(thin_year: int, thin_prop: float) -> str:
    """A fixed-width ThinDBH keyword: remove `thin_prop` of TPA across all trees.

    FVS reads keyword fields in 10-column groups. Fields: year, min DBH (0),
    max DBH (999 = all), proportion removed, species (0 = all).
    """
    return (
        f"{'ThinDBH':<10}"
        f"{thin_year:>10d}"
        f"{0:>10.0f}"
        f"{999:>10.0f}"
        f"{thin_prop:>10.2f}"
        f"{0:>10.0f}"
    )


def build_cut_keyfile(
    arm: str,
    out_db: str,
    thin_year: int | None = None,
    thin_prop: float | None = None,
    num_cycle: int = 4,
) -> str:
    """Single-stand gate keyfile, no carbon.

    If `thin_year`/`thin_prop` are given, a scheduled ThinDBH is inserted (arm G1
    baseline). Otherwise no management is scheduled and the cut is injected at
    runtime via fvsCutNow (arms G2/G3).
    """
    parts = [
        _HEADER.format(arm=arm),
        _SCHEDULE.format(
            arm=arm,
            stand_id=STAND_ID,
            stand_cn=STAND_CN,
            inv_year=INV_YEAR,
            cycle_years=CYCLE_YEARS,
            num_cycle=num_cycle,
        ),
    ]
    if thin_year is not None and thin_prop is not None:
        parts.append("\n" + _thindbh_line(thin_year, thin_prop) + "\n")
    parts.append(_OUTPUT_AND_INPUT.format(out_db=out_db))
    parts.append("Stop\n")
    return "".join(parts)


def build_multistand_cut_keyfile(
    arm: str,
    out_db: str,
    stands: tuple[tuple[str, str], ...],
    num_cycle: int = 4,
) -> str:
    """Multi-stand gate keyfile, no carbon, no scheduled thin.

    `stands` is a sequence of (stand_cn, stand_id). Cuts are injected per stand
    at runtime (fvsCutNow / fvsAddActivity), so the orchestrator can cut some
    stands in a bundle and not others. All stands share the 2019 schedule.
    """
    parts = [_HEADER.format(arm=arm)]
    for cn, sid in stands:
        parts.append(
            _SCHEDULE.format(
                arm=arm,
                stand_id=sid,
                stand_cn=cn,
                inv_year=MULTI_INV_YEAR,
                cycle_years=CYCLE_YEARS,
                num_cycle=num_cycle,
            )
        )
        parts.append(_OUTPUT_AND_INPUT.format(out_db=out_db))
    parts.append("Stop\n")
    return "".join(parts)
