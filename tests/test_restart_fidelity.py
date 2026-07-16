"""Tests for the FVS restart-fidelity spike: keyfile generation and arm comparison."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.restart_fidelity import compare_arms, make_keyfiles, paths


def test_to_windows_translates_mnt_c():
    assert paths.to_windows(Path("/mnt/c/FVS/artemis_spike/a.key")) == r"C:\FVS\artemis_spike\a.key"


def test_keyfile_has_carbon_keywords():
    # CarbReDB errors unless FFE is active, so FMIn must be present.
    kf = make_keyfiles.build_keyfile("a", "arm_a.db")
    assert "FMIn" in kf
    assert "CarbRept" in kf
    assert "CarbReDB" in kf
    assert kf.index("FMIn") < kf.index("CarbRept")


def test_keyfile_uses_verified_fixture_and_schema():
    kf = make_keyfiles.build_keyfile("a", "arm_a.db")
    assert "43393151010478" in kf
    assert "FVS_StandInit_Plot" in kf
    assert "FVS_TreeInit_Plot" in kf
    # The input DB placeholder is %Stand_CN%, not %StandCN%.
    assert "%Stand_CN%" in kf
    assert "%StandCN%" not in kf


def test_keyfile_is_four_five_year_cycles():
    kf = make_keyfiles.build_keyfile("a", "arm_a.db")
    assert "InvYear       1999" in kf
    assert "TimeInt                 5" in kf
    assert "NumCycle      4" in kf


def test_keyfile_names_its_output_db():
    assert "arm_c.db" in make_keyfiles.build_keyfile("c", "arm_c.db")


def test_multistand_keyfile_has_one_block_per_stand():
    kf = make_keyfiles.build_multistand_keyfile("m", "arm_m.db")
    # One Process per stand, one trailing Stop for the whole file.
    assert kf.count("Process") == len(make_keyfiles.MULTI_STANDS)
    assert kf.count("StandCN") == len(make_keyfiles.MULTI_STANDS)
    assert kf.rstrip().endswith("Stop")
    assert kf.count("Stop\n") == 1
    for cn, sid in make_keyfiles.MULTI_STANDS:
        assert cn in kf
        assert sid in kf


def test_multistand_keyfile_uses_2019_schedule():
    kf = make_keyfiles.build_multistand_keyfile("m", "arm_m.db")
    assert "InvYear       2019" in kf
    assert "NumCycle      4" in kf


# --- DuckDB arm comparison -------------------------------------------------


def _make_arm(tmp_path: Path, name: str, ba: float, carbon: float | None) -> Path:
    """Build a tiny SQLite DB shaped like FVS output."""
    import sqlite3

    db = tmp_path / f"{name}.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE FVS_Summary2 (StandID TEXT, Year INT, Tpa REAL, BA REAL, SDI REAL)")
    conn.execute("INSERT INTO FVS_Summary2 VALUES ('S1', 2004, 100.0, ?, 50.0)", (ba,))
    if carbon is not None:
        conn.execute("CREATE TABLE FVS_Carbon (StandID TEXT, Year INT, Aboveground_Total REAL)")
        conn.execute("INSERT INTO FVS_Carbon VALUES ('S1', 2004, ?)", (carbon,))
    conn.commit()
    conn.close()
    return db


def _con(arms: dict[str, Path]):
    con = duckdb.connect()
    compare_arms.attach_arms(con, arms)
    return con


def test_identical_arms_have_zero_summary_delta(tmp_path):
    arms = {"a": _make_arm(tmp_path, "a", 80.0, 10.0), "b": _make_arm(tmp_path, "b", 80.0, 10.0)}
    df = compare_arms.diff_summary(_con(arms), "a", "b")
    assert df["ba_delta"].abs().max() == 0.0


def test_summary_delta_detects_difference(tmp_path):
    arms = {"a": _make_arm(tmp_path, "a", 80.0, 10.0), "b": _make_arm(tmp_path, "b", 85.0, 10.0)}
    df = compare_arms.diff_summary(_con(arms), "a", "b")
    assert df["ba_delta"].abs().max() == pytest.approx(5.0)


def test_carbon_diff_is_reported_even_when_summary_matches(tmp_path):
    """The silent-corruption case: base metrics identical, carbon diverged."""
    arms = {"a": _make_arm(tmp_path, "a", 80.0, 10.0), "b": _make_arm(tmp_path, "b", 80.0, 7.5)}
    con = _con(arms)
    assert compare_arms.diff_summary(con, "a", "b")["ba_delta"].abs().max() == 0.0
    carbon = compare_arms.diff_carbon(con, "a", "b")
    assert carbon["delta"].abs().max() == pytest.approx(2.5)


def test_missing_carbon_table_fails_loudly(tmp_path):
    """A missing FVS_Carbon must raise, never read as 'no difference'."""
    arms = {"a": _make_arm(tmp_path, "a", 80.0, 10.0), "b": _make_arm(tmp_path, "b", 80.0, None)}
    con = _con(arms)
    with pytest.raises(compare_arms.CarbonTableMissing):
        compare_arms.assert_carbon_present(con, "b")
