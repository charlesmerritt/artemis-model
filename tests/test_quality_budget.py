"""scripts/quality.py mutate --budget-minutes: stop starting files at the budget, exit cleanly."""

import argparse
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "quality", Path(__file__).resolve().parents[1] / "scripts" / "quality.py")
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


@pytest.fixture
def clock(monkeypatch):
    """A fake clock that each mutmut run advances by its file's cost in minutes."""
    now = [0.0]
    cost = {"fake.a": 30, "fake.b": 50, "fake.c": 30}
    calls = []

    def run_mutmut(module, timeout):
        calls.append((module, timeout))
        spent = cost[module] * 60 if timeout is None else min(cost[module] * 60, timeout)
        now[0] += spent
        return spent == cost[module] * 60

    monkeypatch.setattr(quality, "clock", lambda: now[0])
    monkeypatch.setattr(quality, "run_mutmut", run_mutmut)
    monkeypatch.setattr(quality, "mutation_statuses",
                        lambda module: {f"{module}.x__mutmut_1": "killed"})
    return calls


def _args(tmp_path, budget):
    return argparse.Namespace(rotation=False, changed_since=None,
                              paths=["fake/a.py", "fake/b.py", "fake/c.py"],
                              min_score=None, show_survivors=30, summary=tmp_path / "m.md",
                              budget_minutes=budget)


def test_the_budget_cuts_the_file_it_runs_out_in_and_skips_the_rest(tmp_path, clock):
    status = quality.cmd_mutate(_args(tmp_path, budget=60))

    assert status == 0
    assert [m for m, _ in clock] == ["fake.a", "fake.b"]
    assert clock[1][1] == pytest.approx(30 * 60)          # b gets only what is left
    report = (tmp_path / "m.md").read_text()
    assert "`fake/b.py` | stopped at budget" in report
    assert "`fake/c.py` | not reached" in report
    assert "budget of 60 min" in report


def test_without_a_budget_every_file_runs_to_the_end(tmp_path, clock):
    assert quality.cmd_mutate(_args(tmp_path, budget=None)) == 0

    assert [m for m, _ in clock] == ["fake.a", "fake.b", "fake.c"]
    assert all(timeout is None for _, timeout in clock)
    assert "not reached" not in (tmp_path / "m.md").read_text()
