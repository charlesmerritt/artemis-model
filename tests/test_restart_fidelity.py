"""Tests for the FVS restart-fidelity spike: keyfile generation and arm comparison."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.restart_fidelity import make_keyfiles, paths


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
