"""The executable notes must fail when the policies or examples they teach fail."""

import doctest
import subprocess
import sys
from dataclasses import replace

import pytest

from notes import check


def test_checked_in_notes_run_without_external_data():
    result = subprocess.run(
        [sys.executable, str(check.ROOT / "notes/check.py")],
        cwd=check.ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no_management" in result.stdout
    assert "archived evidence" in result.stdout


def test_failed_doctest_fails_command(monkeypatch):
    monkeypatch.setattr(check.doctest, "testmod", lambda *a, **k: doctest.TestResults(1, 1))
    assert check.main([]) == 1


def test_policy_without_unmanaged_choice_fails_command(monkeypatch):
    original = check.regime_assignment.eligible_prescriptions
    monkeypatch.setattr(
        check.regime_assignment, "eligible_prescriptions",
        lambda *a, **k: [p for p in original(*a, **k) if p != "no_management"],
    )
    assert check.main([]) == 1


def test_empty_keyfile_fails_command(monkeypatch, capsys):
    monkeypatch.setattr(check.regime_library, "render_keyfile", lambda *a, **k: "")
    assert check.main([]) == 1
    assert "keyfile" in capsys.readouterr().err


@pytest.mark.parametrize("state", ["missing", "empty"])
def test_missing_restart_evidence_fails_command(monkeypatch, tmp_path, capsys, state):
    relative = "research/fia_treemap_fortype/outputs/FL_state_level_scaling_comparison.csv"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text((check.ROOT / relative).read_text())
    if state == "empty":
        record = tmp_path / "research/restart_fidelity/outputs/arm.txt"
        record.parent.mkdir(parents=True)
        record.write_text("")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    assert check.main([]) == 1
    assert "restart evidence" in capsys.readouterr().err


@pytest.mark.parametrize("corruption", ["template", "regen_slot", "harvest"])
def test_riparian_management_fails_command(monkeypatch, corruption):
    if corruption == "harvest":
        monkeypatch.setattr(check.regime_templates, "build_thins", lambda *a, **k: [object()])
    else:
        original = check.regime_assignment.assign_prescription
        value = "clearcut" if corruption == "template" else "planted_pine"
        monkeypatch.setattr(
            check.regime_assignment, "assign_prescription",
            lambda *a, **k: replace(original(*a, **k), **{corruption: value}),
        )
    assert check.main([]) == 1
