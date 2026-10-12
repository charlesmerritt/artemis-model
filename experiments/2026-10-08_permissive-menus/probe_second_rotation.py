"""Probe: does one FVS keyfile carry two clearcut-and-replant rotations?

`config/management_regimes.yaml` marks the plantation rotations `repeats: true`, but no
keyfile path consumes it (research/regeneration_audit/README.md). The permissive menus in
this experiment express a repeated rotation as more operations in the same keyfile: a
second ThinDBH(proportion=1) and a second `Estab` packet. This probe runs that keyfile and
a one-rotation control on the same plot and prints the harvest and standing volume per
cycle, so the claim that the second rotation regrows and is cut rests on FVS output.

    uv run python experiments/2026-10-08_permissive-menus/probe_second_rotation.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from pipeline.s4_fvs.regime_templates import (  # noqa: E402
    Regeneration, ThinDBH, _KEYFILE, render_estab_block, render_schedule_block,
)

FVS_BIN = REPO / "fvs/bin/FVSsn"
DATA_DB = REPO / "data/interim/timing_library/FVS_Data.db"
PLOT = sys.argv[1] if len(sys.argv) > 1 else "374861300489998"   # Union loblolly, age 19


def keyfile(ops: list[ThinDBH], estabs: list[list[Regeneration]]) -> str:
    blocks = [t.render() for t in ops] + [render_estab_block(r) for r in estabs]
    return _KEYFILE.format(stand_id=f"S{PLOT}", stand_cn=PLOT, regime="probe", mgmt_id="A001",
                           schedule_block=render_schedule_block(2022, 5, 11),
                           activity_block="\n".join(blocks), out_db="FVS_Out.db",
                           in_db="FVS_Data.db", stand_table="FVS_StandInit_Plot",
                           tree_table="FVS_TreeInit_Plot", sp_label="ARTEMIS")


def run(key: str) -> pd.DataFrame:
    tmp = Path(tempfile.mkdtemp(prefix="probe_"))
    try:
        (tmp / "run.key").write_text(key)
        os.symlink(DATA_DB, tmp / "FVS_Data.db")
        subprocess.run([str(FVS_BIN), "--keywordfile=run.key"], cwd=tmp, capture_output=True)
        con = sqlite3.connect(tmp / "FVS_Out.db")
        df = pd.read_sql("SELECT Year, Age, Tpa, MCuFt, RMCuFt, RmvCode FROM FVS_Summary2", con)
        con.close()
        return df
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


one = keyfile([ThinDBH(2027, 1.0)], [[Regeneration(2028)]])
two = keyfile([ThinDBH(2027, 1.0), ThinDBH(2052, 1.0)],
              [[Regeneration(2028)], [Regeneration(2053)]])
for name, key in (("one rotation", one), ("two rotations", two)):
    print(f"--- {name}\n{run(key).to_string(index=False)}")
