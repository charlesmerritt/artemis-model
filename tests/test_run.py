"""The ARTEMIS runner: a run directory's stands, through the library, to a scheduled plan."""

import json
import sqlite3
import stat

import pandas as pd
import pytest

from pipeline import run
from pipeline.s4_fvs import trajectory_library as tl

CUT = "pine_plantation_short_rotation"


def _stands():
    return pd.DataFrame({
        "unit_id": ["a", "b", "r"], "PLT_CN": ["1234567890123456789", "2", "2"],
        "county": ["Baker"] * 3, "owner_class": ["corporate"] * 3, "OWN_CODE": [4, 4, 4],
        "FORTYPCD": [161, 161, 161], "forest_branch": ["pine"] * 3, "stand_age": [30, 30, 30],
        "acres": [1.0, 1.0, 1.0], "unit_class": ["managed", "managed", "riparian"],
    })


def _write_scheduled_run_dir(d):
    """A run directory after the library stage: two options per managed stand."""
    stands = _stands()
    stands.to_csv(d / run.STANDS, index=False)
    rows, cycles = [], []
    for unit, plt in zip(stands["unit_id"], stands["PLT_CN"]):
        options = ["no_management"] if unit == "r" else ["no_management", CUT]
        for presc in options:
            rows.append({"unit_id": unit, "PLT_CN": plt, "prescription": presc,
                         "base_prescription": presc, "run_hash": f"{plt}-{presc}"})
    for plt, cut_cycle in [("1234567890123456789", 1), ("2", 2)]:
        for presc in ["no_management", CUT]:
            for cycle in range(0, 12):
                removed = 100.0 if presc == CUT and cycle == cut_cycle else 0.0
                cycles.append({"run_hash": f"{plt}-{presc}", "PLT_CN": plt, "cycle": cycle,
                               "calendar_year": 2022 + 5 * cycle, "Age": 30, "BA": 100.0,
                               "MCuFt": 1000.0, "removed_merch_cuft_per_ac": removed})
    pd.DataFrame(rows).to_csv(d / run.LIBRARY, index=False)
    pd.DataFrame(cycles).to_csv(d / run.CYCLES, index=False)


def test_read_stands_rejects_a_run_dir_missing_a_contract_column(tmp_path):
    _stands().drop(columns="forest_branch").to_csv(tmp_path / run.STANDS, index=False)

    with pytest.raises(ValueError, match="forest_branch"):
        run.read_stands(tmp_path)


def test_read_stands_keeps_control_numbers_exact(tmp_path):
    _stands().to_csv(tmp_path / run.STANDS, index=False)

    assert run.read_stands(tmp_path)["PLT_CN"].tolist()[0] == "1234567890123456789"


def test_the_library_stage_writes_the_library_and_records_failed_runs(tmp_path):
    _stands().to_csv(tmp_path / run.STANDS, index=False)
    with sqlite3.connect(tmp_path / run.FVS_DATA) as con:
        con.execute("create table FVS_TreeInit_Plot (STAND_CN text, SPECIES text, HISTORY int, "
                    "TREE_COUNT real, DIAMETER real)")
    fake = tmp_path / "FVSsn"
    fake.write_text("#!/bin/sh\nexit 2\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    run.main(["library", str(tmp_path), "--workers", "1", "--fvs-bin", str(fake)])

    lib = pd.read_csv(tmp_path / run.LIBRARY, dtype={"PLT_CN": str})
    assert lib.loc[lib["unit_id"] == "r", "prescription"].tolist() == ["no_management"]
    failures = pd.read_csv(tmp_path / run.FAILURES)
    assert set(failures["run_hash"]) == set(lib["run_hash"])


def test_the_schedule_stage_writes_the_best_plan_and_its_summary(tmp_path):
    _write_scheduled_run_dir(tmp_path)

    run.main(["schedule", str(tmp_path), "--restarts", "2", "--workers", "1"])

    plan = pd.read_csv(tmp_path / run.PLAN, dtype={"PLT_CN": str})
    summary = json.loads((tmp_path / run.SUMMARY).read_text())
    assert set(plan["stand_id"]) == {"a", "b", "r"}
    assert plan.loc[plan["stand_id"] == "r", "prescription"].item() == "no_management"
    assert summary["stands"] == 3 and summary["restarts"] == 2
    assert summary["objective_best"] <= summary["objective_greedy"]
    assert (tmp_path / run.DEVIATION).exists() and (tmp_path / run.SEEDS).exists()


def test_the_inventory_layout_picks_the_keyfile_tables():
    assert run.InventoryLayout.PLOT.tables is tl.PLOT_TABLES
    assert run.InventoryLayout.LETO.tables is tl.LETO_TABLES
