"""The configured mutation workspace must collect the same local test suite."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mutation_workspace(tmp_path):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mutmut"]
    for relative in [*config["source_paths"], *config["also_copy"], "pyproject.toml"]:
        source = ROOT / relative
        destination = tmp_path / relative
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return tmp_path


def _collect(workspace, *paths):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths],
        cwd=workspace, env=environment, capture_output=True, text=True,
    )


def test_configured_mutation_workspace_contains_fixtures_and_collects_suite(mutation_workspace):
    # These are inputs the tests read directly, independent of mutmut's configuration.
    for relative in [
        "weekly-artifact/2026-09-07/make_offset_library.py",
        "weekly-artifact/2026-09-14/make_timing_library.py",
        "weekly-artifact/2026-09-28/make_converged_plan.py",
        "research/restart_fidelity/outputs/arm_d_vs_a.txt",
        "research/fia_treemap_fortype/outputs/FL_state_level_scaling_comparison.csv",
        "notebooks/LETO_Initial_State_Walkthrough.ipynb",
        "docs/research/leto-vs-boundary-overlay.md",
        "docs/superpowers/specs/2026-07-20-s1-segmentation-synthesis-design.md",
    ]:
        assert (mutation_workspace / relative).is_file(), relative
    result = _collect(mutation_workspace, "tests", "pipeline", "scripts/quality.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tests collected" in result.stdout


def test_missing_driver_is_rejected_during_isolated_collection(mutation_workspace):
    driver = mutation_workspace / "weekly-artifact/2026-09-14/make_timing_library.py"
    driver.unlink(missing_ok=True)
    result = _collect(mutation_workspace, "tests/test_weekly_artifact_20260914_timing.py")
    assert result.returncode != 0
    assert "FileNotFoundError" in result.stdout + result.stderr
    assert "make_timing_library.py" in result.stdout + result.stderr
