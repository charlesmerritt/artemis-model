"""Tests for the management-injection (gate) spike: keyfile generation.

The gate proves fvsCutNow at a barrier is faithful. These tests cover the pure
keyfile logic; the FVS runs themselves are executed by hand (Windows only).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.restart_fidelity import make_cut_keyfiles as mck


def test_thin_keyfile_has_thindbh_line():
    kf = mck.build_cut_keyfile("g1", "g1.db", thin_year=2004, thin_prop=0.30)
    assert "ThinDBH" in kf
    # year, all-diameter span, and the 0.30 proportion must all be present.
    assert "2004" in kf
    assert "0.30" in kf
    assert "999" in kf


def test_nocut_keyfile_has_no_thin():
    """G2/G3 carry no scheduled thin -- the cut is injected at runtime."""
    kf = mck.build_cut_keyfile("g2", "g2.db")
    assert "ThinDBH" not in kf


def test_cut_keyfile_has_no_carbon():
    """Carbon is out of scope and off; gate keyfiles must not enable FFE."""
    kf = mck.build_cut_keyfile("g1", "g1.db", thin_year=2004, thin_prop=0.30)
    assert "FMIn" not in kf
    assert "CarbReDB" not in kf
    assert "Summary" in kf


def test_cut_keyfile_uses_fixture_and_schedule():
    kf = mck.build_cut_keyfile("g2", "g2.db")
    assert mck.STAND_CN in kf
    assert "InvYear       1999" in kf
    assert "NumCycle      4" in kf
    assert "%Stand_CN%" in kf


def test_thindbh_is_fixed_width_formatted():
    """FVS reads keyword fields in 10-col groups; the line must align."""
    line = mck._thindbh_line(2004, 0.30)
    assert line.startswith("ThinDBH")
    # keyword occupies cols 1-10, then five 10-wide fields = 60 chars.
    assert len(line) == 60
