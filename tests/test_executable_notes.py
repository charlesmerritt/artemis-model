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
    config = check.regime_assignment.load_regimes_config()

    def missing_unmanaged(owner, branch=None, config=None):
        menu = original(owner, branch, config)
        if branch is not None and config["owner_classes"][owner]["default"][branch] != "no_management":
            return [p for p in menu if p != "no_management"]
        return menu

    assert any(spec["default"]["pine"] != "no_management"
               for spec in config["owner_classes"].values())
    monkeypatch.setattr(
        check.regime_assignment, "eligible_prescriptions",
        missing_unmanaged,
    )
    assert check.main([]) == 1


def test_empty_keyfile_fails_command(monkeypatch, capsys):
    monkeypatch.setattr(check.regime_library, "render_keyfile", lambda *a, **k: "")
    assert check.main([]) == 1
    assert "keyfile" in capsys.readouterr().err


def test_managed_example_without_harvest_fails_command(monkeypatch, capsys):
    monkeypatch.setattr(check.regime_library, "render_keyfile", lambda *a, **k: "Process\nStop\n")
    assert check.main([]) == 1
    assert "harvest" in capsys.readouterr().err


@pytest.mark.parametrize("state", ["missing", "empty"])
def test_missing_restart_evidence_fails_command(monkeypatch, tmp_path, capsys, state):
    relative = "research/fia_treemap_fortype/outputs/FL_state_level_scaling_comparison.csv"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text((check.ROOT / relative).read_text())
    if state == "empty":
        record = tmp_path / "research/restart_fidelity/outputs" / "arm.txt"
        record.parent.mkdir(parents=True)
        record.write_text("")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    assert check.main([]) == 1
    assert "restart evidence" in capsys.readouterr().err


@pytest.mark.parametrize("corruption", ["template", "regen_slot", "harvest"])
def test_riparian_management_fails_command(monkeypatch, corruption):
    # Isolate the policy guard: mutated assignment can also break a usage doctest.
    monkeypatch.setattr(check.doctest, "testmod", lambda *a, **k: doctest.TestResults(0, 0))
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
