"""Demonstrate that revised test oracles reject faults their old versions accepted.

Run from the repository root: .venv/bin/python research/code_quality/probe_oracles.py
All faults use in-process monkeypatches; production files stay untouched.
"""

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "066aee62e9f1aedf29177eda01b9655295a60443"
sys.path.insert(0, str(ROOT))


def load_test(relative, name, revision=None):
    path = ROOT / relative
    if revision:
        source = subprocess.run(
            ["git", "show", f"{revision}:{relative}"], cwd=ROOT,
            check=True, text=True, capture_output=True,
        ).stdout
        spec = importlib.util.spec_from_loader(name, loader=None)
        module = importlib.util.module_from_spec(spec)
        module.__file__ = str(path)
        sys.modules[name] = module
        exec(compile(source, str(path), "exec"), module.__dict__)
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def rejected(label, check):
    try:
        check()
    except AssertionError:
        print(f"REJECTED: {label}")
        return
    raise AssertionError(f"fault escaped replacement test: {label}")


def main():
    offset_path = "tests/test_weekly_artifact_20260907_offsets.py"
    old_offsets = load_test(offset_path, "old_offsets", BASELINE)
    offsets = load_test(offset_path, "new_offsets")
    driver = offsets._load(offsets.LIB_DRIVER, "offset_fault_driver")
    offsets.test_shift_moves_years_and_only_years(driver, "selection_harvest", 10)
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(driver.YEAR_PARAMS, "selection_harvest", ("start_year",))
        old_offsets.test_shift_moves_years_and_only_years(driver, "selection_harvest", 10)
        rejected("end_year omitted from YEAR_PARAMS", lambda:
                 offsets.test_shift_moves_years_and_only_years(driver, "selection_harvest", 10))

    timing_path = "tests/test_weekly_artifact_20260914_timing.py"
    old_timing = load_test(timing_path, "old_timing", BASELINE)
    timing = load_test(timing_path, "new_timing")
    cases = timing.test_offset_zero_is_the_identity.pytestmark[0].args[1]
    for case in cases:
        timing.test_offset_zero_is_the_identity(*case)
    for module in (old_timing.m, timing.m):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(module, "build_thins", lambda *a, **k: [])
            patch.setattr(module, "build_regeneration", lambda *a, **k: [])
            if module is old_timing.m:
                old_timing.test_offset_zero_is_the_identity()
            else:
                rejected("shared builder emits no harvest", lambda:
                         timing.test_offset_zero_is_the_identity(*cases[0]))
    for module in (old_timing.m, timing.m):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(module, "build_regeneration", lambda *a, **k: [])
            if module is old_timing.m:
                old_timing.test_regeneration_moves_with_its_harvest()
            else:
                rejected("shared builder emits no regeneration", timing.test_regeneration_moves_with_its_harvest)

    convergence_path = "tests/test_weekly_artifact_20260928_convergence.py"
    old_convergence = load_test(convergence_path, "old_convergence", BASELINE)
    convergence = load_test(convergence_path, "new_convergence")
    driver = convergence._load(convergence.DRIVER, "convergence_fault_driver")
    convergence.test_the_baseline_cell_is_the_published_setting_and_is_unique(driver)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(driver, "CONFIG_COOLING", 0.98)
        old_convergence.test_the_baseline_cell_is_the_published_setting_and_is_unique(driver)
        rejected("published baseline relabelled to cooling 0.98", lambda:
                 convergence.test_the_baseline_cell_is_the_published_setting_and_is_unique(driver))
    viewer_path = "viewer/tests/test-artemis-panel-core.mjs"
    old_viewer = subprocess.run(
        ["git", "show", f"{BASELINE}:{viewer_path}"], cwd=ROOT,
        check=True, text=True, capture_output=True,
    ).stdout
    core = (ROOT / "viewer/artemis-panel-core.js").read_text().replace(
        'const CATALOG_SCHEMA = "artemis.viewer.catalog/1";',
        'const CATALOG_SCHEMA = "artemis.viewer.catalog/2";',
    )
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        (workspace / "tests").mkdir()
        (workspace / "artemis-panel-core.js").write_text(core)
        test = workspace / "tests/test.mjs"
        test.write_text(old_viewer)
        old = subprocess.run(["node", str(test)], capture_output=True, text=True)
        assert old.returncode == 0, old.stdout + old.stderr
        test.write_text((ROOT / viewer_path).read_text())
        new = subprocess.run(["node", str(test)], capture_output=True, text=True)
        assert new.returncode != 0 and "AssertionError" in new.stderr, new.stdout + new.stderr
        print("REJECTED: viewer silently switches supported schema to version 2")
    print("All five old checks accepted their faults; all five replacements rejected them.")


if __name__ == "__main__":
    main()
