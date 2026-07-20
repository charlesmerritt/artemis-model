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


def test_parallel_demo_builds_one_stand_worker_keyfile():
    """The parallel launcher gives each worker a single-stand keyfile."""
    from research.restart_fidelity import parallel_demo

    cn, sid = make_keyfiles.MULTI_STANDS[0]
    kf = parallel_demo._worker_keyfile("pw1", sid, cn, "pw1.db")
    assert kf.count("StandCN") == 1
    assert cn in kf and sid in kf
    assert "pw1.db" in kf
    assert kf.rstrip().endswith("Stop")


# --- DuckDB arm comparison -------------------------------------------------


def _make_arm(
    tmp_path: Path,
    name: str,
    ba: float,
    carbon: float | None,
    rmv_codes: tuple[int, ...] = (0,),
) -> Path:
    """Build a tiny SQLite DB shaped like FVS output.

    RmvCode mirrors real FVS_Summary2: 0 for an unmanaged year, and a 1/2 pair
    (pre-removal / post-removal) for a cut year.
    """
    import sqlite3

    db = tmp_path / f"{name}.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE FVS_Summary2 "
        "(StandID TEXT, Year INT, RmvCode INT, Tpa REAL, BA REAL, SDI REAL)"
    )
    for code in rmv_codes:
        # Post-removal rows carry less standing volume, as after a real cut.
        scale = 0.7 if code == 2 else 1.0
        conn.execute(
            "INSERT INTO FVS_Summary2 VALUES ('S1', 2004, ?, ?, ?, ?)",
            (code, 100.0 * scale, ba * scale, 50.0 * scale),
        )
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


def test_managed_arms_join_on_rmvcode_not_year_alone(tmp_path):
    """Two identical MANAGED arms must show zero delta.

    A cut year emits RmvCode 1 (pre) and 2 (post). Joining on Year alone
    cross-joins pre against post, so identical arms would report a huge false
    delta -- the exact false-positive this guards.
    """
    arms = {
        "a": _make_arm(tmp_path, "a", 80.0, 10.0, rmv_codes=(1, 2)),
        "b": _make_arm(tmp_path, "b", 80.0, 10.0, rmv_codes=(1, 2)),
    }
    df = compare_arms.diff_summary(_con(arms), "a", "b")
    assert sorted(df["RmvCode"]) == [1, 2]  # both rows compared, not cross-joined
    assert df["ba_delta"].abs().max() == 0.0


def test_managed_vs_unmanaged_arm_raises_rather_than_reporting_no_delta(tmp_path):
    """Disjoint RmvCodes share no basis; that must fail loudly, not read as a pass."""
    arms = {
        "a": _make_arm(tmp_path, "a", 80.0, 10.0, rmv_codes=(1, 2)),
        "b": _make_arm(tmp_path, "b", 80.0, 10.0, rmv_codes=(0,)),
    }
    with pytest.raises(compare_arms.NoComparableRows):
        compare_arms.diff_summary(_con(arms), "a", "b")


def test_disjoint_years_raise_instead_of_reporting_zero_differences(tmp_path):
    """An arm that died early shares no (StandID, Year) rows -- not a pass."""
    import sqlite3

    arms = {"a": _make_arm(tmp_path, "a", 80.0, 10.0), "b": _make_arm(tmp_path, "b", 80.0, 10.0)}
    conn = sqlite3.connect(arms["b"])
    conn.execute("UPDATE FVS_Summary2 SET Year = 2009")
    conn.commit()
    conn.close()
    with pytest.raises(compare_arms.NoComparableRows):
        compare_arms.diff_summary(_con(arms), "a", "b")


def test_attach_rejects_unsafe_alias(tmp_path):
    """Aliases are spliced into SQL text, so they must be validated identifiers."""
    with pytest.raises(ValueError):
        compare_arms.attach_arms(duckdb.connect(), {"a; DROP TABLE x": _make_arm(tmp_path, "a", 1.0, None)})


def test_attach_tolerates_quote_in_db_path(tmp_path):
    """A quote in the DB path must be escaped, not break the ATTACH statement."""
    odd = tmp_path / "it's a dir"
    odd.mkdir()
    db = _make_arm(odd, "a", 80.0, 10.0)
    con = duckdb.connect()
    compare_arms.attach_arms(con, {"a": db})
    assert con.execute("SELECT COUNT(*) FROM a.FVS_Summary2").fetchone()[0] == 1


def test_spike_dir_win_is_derived_from_wsl_path():
    """The two spellings must name one directory; derivation makes that hold."""
    assert paths.SPIKE_DIR_WIN == paths.to_windows(paths.SPIKE_DIR_WSL)


def test_launch_raises_catchable_error_when_run_dir_missing(tmp_path, monkeypatch):
    """launch() is a library function: it must raise, not kill the process."""
    from research.restart_fidelity import parallel_demo

    monkeypatch.setattr(paths, "SPIKE_DIR_WSL", tmp_path / "absent")
    with pytest.raises(parallel_demo.RunDirNotStaged):
        parallel_demo.launch()


def test_launch_raises_when_input_db_missing(tmp_path, monkeypatch):
    from research.restart_fidelity import parallel_demo

    monkeypatch.setattr(paths, "SPIKE_DIR_WSL", tmp_path)
    with pytest.raises(parallel_demo.RunDirNotStaged, match="input DB missing"):
        parallel_demo.launch()
