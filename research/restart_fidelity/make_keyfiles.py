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

# Multi-stand fixture: five SN stands sharing Inv_Year 2019, so one barrier year
# lands on the same cycle boundary for every stand. 28-70 tree records each.
# Used to test the claim that a restart file stores and rehydrates ALL stands --
# the mechanism a global barrier depends on.
MULTI_STANDS: tuple[tuple[str, str], ...] = (
    ("448939744489998", "121705900034"),
    ("448939819489998", "121705900036"),
    ("448939827489998", "121708300141"),
    ("448939977489998", "121700100063"),
    ("448940000489998", "121708900049"),
)
MULTI_INV_YEAR = 2019

_STAND_BLOCK = """\
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
"""


def _block(arm: str, stand_id: str, stand_cn: str, inv_year: int, out_db: str, num_cycle: int) -> str:
    return _STAND_BLOCK.format(
        arm=arm,
        stand_id=stand_id,
        stand_cn=stand_cn,
        inv_year=inv_year,
        cycle_years=CYCLE_YEARS,
        num_cycle=num_cycle,
        out_db=out_db,
        in_db="FVS_Data.db",
    )


def build_keyfile(arm: str, out_db: str, num_cycle: int = 4) -> str:
    """Return the single-stand keyfile text for one arm.

    `arm` is one of ARMS; `out_db` is the SQLite output filename FVS writes,
    relative to the run directory.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; expected one of {ARMS}")
    header = f"!!title: restart_fidelity_arm_{arm}\n"
    return header + _block(arm, STAND_ID, STAND_CN, INV_YEAR, out_db, num_cycle) + "Stop\n"


def build_multistand_keyfile(arm: str, out_db: str, num_cycle: int = 4) -> str:
    """Return a keyfile covering every stand in MULTI_STANDS.

    One `Process` block per stand, a single trailing `Stop` for the file. This is
    the shape a global barrier needs: `putstd` is called per stand at the stop
    point and accumulates every stand into one restart file (cmdline.f:179).
    """
    header = f"!!title: restart_fidelity_multistand_arm_{arm}\n"
    blocks = "".join(
        _block(arm, sid, cn, MULTI_INV_YEAR, out_db, num_cycle) for cn, sid in MULTI_STANDS
    )
    return header + blocks + "Stop\n"
