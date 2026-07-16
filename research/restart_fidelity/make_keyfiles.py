"""Generate FVS keyfiles for the four restart-fidelity spike arms.

All arms share one fixture stand and an identical 1999-2019 schedule (four
5-year cycles, no management), so any difference between arms is attributable
to the state-transfer mechanism alone.

Carbon is enabled deliberately: CarbReDB writes the FVS_Carbon table, and it
errors unless FFE is active, so the FMIn section is mandatory (verified in
Open-FVS dbsin.f option 29 / fmin.f options 44 and 46).
"""

from __future__ import annotations

ARMS: tuple[str, ...] = ("a", "b", "c", "d")

STAND_CN = "43393151010478"
STAND_ID = "010006100083"
INV_YEAR = 1999
CYCLE_YEARS = 5

_TEMPLATE = """\
!!title: restart_fidelity_arm_{arm}
StdIdent
{stand_id}               RestartFidelity_arm_{arm}
StandCN
{stand_cn}
MgmtId
A001
InvYear       {inv_year}
TimeInt                 {cycle_years}
NumCycle      {num_cycle}

FMIn
CarbRept
CarbCalc          0         0
End

DataBase
DSNOut
{out_db}
Summary        2
CarbReDB
End

Database
DSNIn
{in_db}
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
Stop
"""


def build_keyfile(arm: str, out_db: str, num_cycle: int = 4) -> str:
    """Return the keyfile text for one arm.

    `arm` is one of ARMS; `out_db` is the SQLite output filename FVS writes,
    relative to the run directory.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; expected one of {ARMS}")
    return _TEMPLATE.format(
        arm=arm,
        stand_id=STAND_ID,
        stand_cn=STAND_CN,
        inv_year=INV_YEAR,
        cycle_years=CYCLE_YEARS,
        num_cycle=num_cycle,
        out_db=out_db,
        in_db="FVS_Data.db",
    )
