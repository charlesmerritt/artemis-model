"""The convergence sweep behind the 2026-09-28 plan.

`weekly-artifact/2026-09-28/make_converged_plan.py` answers the question `2026-09-14` ended
on: how much of that week's objective was the decision space, and how much was a search that
had stopped converging. It changes two numbers — `restarts` and `cooling_factor` — and
nothing else, and it imports last week's `Landscape`, `Objective` and `anneal` rather than
copying them so that "nothing else changed" is a property of the code rather than a claim in
prose.

The driver's own end-to-end checks are the strong ones — the rebuilt FVS library must equal
2026-09-14's published `trajectory_index.csv` run for run, and the (cooling 0.95, 5 restarts)
cell must recover that artifact's published objective — but both need a 13,035-run FVS batch
behind them. What this file pins is the reporting logic that turns a set of searches into the
artifact's tables, on inputs small enough to check by inspection.

The properties, stated as the driver states them:

* the arms **nest**: best-of-5 is the best of the first five seeds of best-of-20, so the 2x2
  is read off one set of searches and no search is run twice to fill a cell;
* the restart curve is **monotone non-increasing** and consumes seeds in a fixed order, so
  `best_of_first_r` is what a run configured with `restarts: R` would actually have reported,
  not the best of an arbitrary subset;
* the baseline cell is **uniquely identified** as the setting 2026-09-14 published, because
  the reproduction check keys on it;
* the library-identity check is a **hard gate** — it raises on a missing run, an extra run, or
  a harvest difference above tolerance — while the reproduction check is reported, which is
  the split the driver argues for;
* the sweep refuses settings that would make its own comparison meaningless: fewer restarts
  than the previous artifact ran, or a cooling factor set that omits the config's own.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "weekly-artifact/2026-09-28/make_converged_plan.py"

pytestmark = pytest.mark.skipif(not DRIVER.exists(),
                                reason="2026-09-28 artifact not present")


def _load(path: Path, name: str):
    """Load a driver by path — `weekly-artifact/2026-09-28` is not an importable name."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def drv():
    return _load(DRIVER, "artemis_converge_driver")


def _runs(objectives_by_cooling: dict[float, list[float]]) -> list[dict]:
    """Synthetic sweep results: one dict per search, shaped as `anneal` returns them."""
    out = []
    for cooling, objs in objectives_by_cooling.items():
        for i, o in enumerate(objs):
            out.append({"cooling_factor": cooling, "seed": 42 + i, "objective": o,
                        "levels": 100 + i, "proposed": 1000, "accepted": 500,
                        "accept_rate": 0.5, "initial_temperature": 0.05,
                        "seconds": 10.0 + i})
    return out


# --------------------------------------------------------------------------------------
# the search is imported, not copied
# --------------------------------------------------------------------------------------

def test_the_search_is_the_previous_artifacts_search(drv):
    """`anneal` and the objective come from 2026-09-14, so "only the parameters changed"."""
    assert drv.AP.__file__.endswith("weekly-artifact/2026-09-14/make_annealed_plan.py")
    for attr in ("Landscape", "Objective", "anneal", "greedy_seed", "plan_frame"):
        assert hasattr(drv.AP, attr)


def test_the_driver_defines_no_search_of_its_own(drv):
    """A local `anneal` would silently make the week-on-week comparison meaningless."""
    assert "anneal" not in vars(drv), "this driver must not define its own anneal"
    assert "Objective" not in vars(drv)
    assert "Landscape" not in vars(drv)


# --------------------------------------------------------------------------------------
# the 2x2 is read off one set of searches
# --------------------------------------------------------------------------------------

def test_arms_nest_so_best_of_5_is_the_first_five_seeds(drv):
    objs = [70.0, 63.0, 68.0, 66.0, 69.0, 61.0, 62.0]
    arms = drv.arm_table(_runs({0.95: objs}), [0.95], [42 + i for i in range(len(objs))])
    five = arms[arms["restarts"] == 5].iloc[0]
    seven = arms[arms["restarts"] == len(objs)].iloc[0]
    assert five["objective_best"] == min(objs[:5]) == 63.0
    assert seven["objective_best"] == min(objs) == 61.0
    # More restarts can only help: the best of a superset is no worse.
    assert seven["objective_best"] <= five["objective_best"]


def test_the_baseline_cell_is_the_published_setting_and_is_unique(drv):
    objs = [70.0, 63.0, 68.0, 66.0, 69.0, 61.0]
    seeds = [42 + i for i in range(len(objs))]
    arms = drv.arm_table(_runs({0.95: objs, 0.98: objs[::-1]}), [0.95, 0.98], seeds)
    flagged = arms[arms["is_20260914_setting"]]
    assert len(flagged) == 1
    assert float(flagged.iloc[0]["cooling_factor"]) == drv.CONFIG_COOLING
    assert int(flagged.iloc[0]["restarts"]) == drv.PREV_RESTARTS


def test_seed_spread_is_reported_per_arm(drv):
    objs = [70.0, 63.0, 68.0, 66.0, 69.0]
    arms = drv.arm_table(_runs({0.95: objs}), [0.95], [42 + i for i in range(len(objs))])
    row = arms.iloc[0]
    assert row["seed_spread"] == pytest.approx(max(objs) - min(objs))
    assert row["seed_spread_pct_of_best"] == pytest.approx(
        100.0 * (max(objs) - min(objs)) / min(objs))
    assert row["best_seed"] == 43


def test_an_arm_short_of_its_restart_count_is_not_reported(drv):
    """Three searches cannot fill a five-restart cell; the row is dropped, not padded."""
    arms = drv.arm_table(_runs({0.95: [70.0, 63.0, 68.0]}), [0.95], [42, 43, 44])
    assert list(arms["restarts"]) == [3]


# --------------------------------------------------------------------------------------
# the restart curve
# --------------------------------------------------------------------------------------

def test_restart_curve_is_monotone_non_increasing(drv):
    objs = [70.0, 63.0, 68.0, 66.0, 61.0, 62.0]
    seeds = [42 + i for i in range(len(objs))]
    curve = drv.restart_curve(_runs({0.95: objs}), [0.95], seeds)
    best = list(curve.sort_values("restarts")["best_of_first_r"])
    assert best == [70.0, 63.0, 63.0, 63.0, 61.0, 61.0]
    assert all(b <= a for a, b in zip(best, best[1:]))


def test_restart_curve_consumes_seeds_in_a_fixed_order(drv):
    """`best_of_first_r` must be what `restarts: R` would have reported, not any R of them."""
    objs = [70.0, 63.0, 68.0]
    seeds = [42, 43, 44]
    runs = _runs({0.95: objs})
    curve = drv.restart_curve(list(reversed(runs)), [0.95], seeds)
    assert list(curve["seed"]) == seeds
    assert list(curve["best_of_first_r"]) == [70.0, 63.0, 63.0]


def test_restart_curve_keeps_the_cooling_factors_apart(drv):
    seeds = [42, 43]
    curve = drv.restart_curve(_runs({0.95: [70.0, 69.0], 0.98: [60.0, 59.0]}),
                              [0.95, 0.98], seeds)
    slow = curve[curve["cooling_factor"] == 0.98].sort_values("restarts")
    assert list(slow["best_of_first_r"]) == [60.0, 59.0]
    assert len(curve) == 4


# --------------------------------------------------------------------------------------
# the library-identity gate
# --------------------------------------------------------------------------------------

def _cycles_frame(rows: dict[str, dict[int, float]]) -> pd.DataFrame:
    """A `trajectory_cycles.csv` shaped as the batch writes it.

    Keyed on (PLT_CN, prescription) with the harvest in `removed_merch_cuft_per_ac` — the
    run id is the driver's to rebuild, which is the part worth pinning: a control number
    that round-tripped through a float would break the join silently.
    """
    return pd.DataFrame([{"PLT_CN": run.split("::")[0], "prescription": run.split("::")[1],
                          "cycle": c, "removed_merch_cuft_per_ac": v}
                         for run, per_cycle in rows.items()
                         for c, v in per_cycle.items()])


def test_library_gate_accepts_an_identical_rebuild(drv, tmp_path, monkeypatch):
    published = pd.DataFrame([{"fvs_run_id": "A::p", "PLT_CN": "1", "cuft_cycle_1": 5.0,
                               "cuft_cycle_2": 0.0}])
    index = tmp_path / "trajectory_index.csv"
    published.to_csv(index, index=False)
    monkeypatch.setattr(drv, "PREV_INDEX", index)
    out = drv.check_library_matches_published(_cycles_frame({"A::p": {1: 5.0, 2: 0.0}}), 2)
    assert out["runs_compared"] == 1
    assert out["max_abs_diff_harvest_cuft_per_ac"] == pytest.approx(0.0)


def test_library_gate_raises_on_a_changed_harvest(drv, tmp_path, monkeypatch):
    published = pd.DataFrame([{"fvs_run_id": "A::p", "PLT_CN": "1", "cuft_cycle_1": 5.0}])
    index = tmp_path / "trajectory_index.csv"
    published.to_csv(index, index=False)
    monkeypatch.setattr(drv, "PREV_INDEX", index)
    with pytest.raises(AssertionError, match="not the one 2026-09-14 published"):
        drv.check_library_matches_published(_cycles_frame({"A::p": {1: 6.0}}), 1)


def test_library_gate_raises_on_a_missing_or_extra_run(drv, tmp_path, monkeypatch):
    published = pd.DataFrame([{"fvs_run_id": "A::p", "PLT_CN": "1", "cuft_cycle_1": 5.0},
                              {"fvs_run_id": "B::p", "PLT_CN": "2", "cuft_cycle_1": 1.0}])
    index = tmp_path / "trajectory_index.csv"
    published.to_csv(index, index=False)
    monkeypatch.setattr(drv, "PREV_INDEX", index)
    with pytest.raises(AssertionError, match="not the one 2026-09-14 published"):
        drv.check_library_matches_published(_cycles_frame({"A::p": {1: 5.0}}), 1)


def test_library_gate_tolerates_only_a_csv_round_trip(drv, tmp_path, monkeypatch):
    """1e-13 is a float round-trip; 1e-3 ft³/ac is a different simulation."""
    published = pd.DataFrame([{"fvs_run_id": "A::p", "PLT_CN": "1", "cuft_cycle_1": 5.0}])
    index = tmp_path / "trajectory_index.csv"
    published.to_csv(index, index=False)
    monkeypatch.setattr(drv, "PREV_INDEX", index)
    ok = drv.check_library_matches_published(_cycles_frame({"A::p": {1: 5.0 + 1e-13}}), 1)
    assert ok["runs_compared"] == 1
    with pytest.raises(AssertionError):
        drv.check_library_matches_published(_cycles_frame({"A::p": {1: 5.001}}), 1)


# --------------------------------------------------------------------------------------
# the settings the sweep refuses
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("argv, message", [
    (["--restarts", "3"], "at least 5"),
    (["--coolings", "0.98"], "must include the config"),
    (["--coolings", "1.5,0.95"], "must cool"),
    (["--workers", "0"], "at least 1"),
])
def test_the_sweep_refuses_a_setting_that_would_void_its_comparison(drv, monkeypatch, argv,
                                                                    message):
    monkeypatch.setattr(sys, "argv", ["make_converged_plan.py", *argv])
    with pytest.raises(SystemExit, match=message):
        drv.main()


def test_library_gate_rebuilds_the_run_id_without_going_through_a_float(drv, tmp_path,
                                                                        monkeypatch):
    """A 19-digit PLT_CN must survive as a string, or the join drops every run.

    `str(int(x))` on a control number read as float64 is the bug AGENTS.md records; here it
    would show up as "0 runs compared" rather than as a wrong number, so the gate must see
    the full-width id.
    """
    plt = "473803917489998123"
    published = pd.DataFrame([{"fvs_run_id": f"{plt}::p", "PLT_CN": plt,
                               "cuft_cycle_1": 5.0}])
    index = tmp_path / "trajectory_index.csv"
    published.to_csv(index, index=False)
    monkeypatch.setattr(drv, "PREV_INDEX", index)
    out = drv.check_library_matches_published(_cycles_frame({f"{plt}::p": {1: 5.0}}), 1)
    assert out["runs_compared"] == 1

# --------------------------------------------------------------------------------------
# the plan-level reproduction check
# --------------------------------------------------------------------------------------

def _plan(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame([{"stand_id": sid, "fvs_run_id": run,
                          "prescription": run.split("::")[1], "timing_offset_years": 0}
                         for sid, run in rows])


def test_plan_reproduction_reports_an_identical_plan(drv, tmp_path, monkeypatch):
    rows = [("s1", "100::a"), ("s2", "200::b")]
    published = tmp_path / "annealed_plan.csv"
    _plan(rows).assign(PLT_CN=["100", "200"]).to_csv(published, index=False)
    monkeypatch.setattr(drv, "PREV_PLAN", published)
    out = drv.check_plan_reproduces_published(_plan(rows))
    assert out["identical"] is True
    assert out["stands_choosing_the_same_trajectory"] == 2
    assert out["stands_choosing_differently"] == 0


def test_plan_reproduction_catches_a_stand_that_chose_differently(drv, tmp_path,
                                                                  monkeypatch):
    """Two selections can score the same, so matching objectives are not a matching plan."""
    published = tmp_path / "annealed_plan.csv"
    _plan([("s1", "100::a"), ("s2", "200::b")]).assign(
        PLT_CN=["100", "200"]).to_csv(published, index=False)
    monkeypatch.setattr(drv, "PREV_PLAN", published)
    out = drv.check_plan_reproduces_published(_plan([("s1", "100::a"), ("s2", "200::c")]))
    assert out["identical"] is False
    assert out["stands_choosing_differently"] == 1


def test_plan_reproduction_catches_a_missing_or_extra_stand(drv, tmp_path, monkeypatch):
    published = tmp_path / "annealed_plan.csv"
    _plan([("s1", "100::a"), ("s2", "200::b")]).assign(
        PLT_CN=["100", "200"]).to_csv(published, index=False)
    monkeypatch.setattr(drv, "PREV_PLAN", published)
    out = drv.check_plan_reproduces_published(_plan([("s1", "100::a")]))
    assert out["identical"] is False
    assert out["stands_only_published"] == 1
    assert out["stands_only_here"] == 0


def test_plan_reproduction_keeps_a_19_digit_control_number_intact(drv, tmp_path,
                                                                  monkeypatch):
    """The run id carries a PLT_CN; through a float64 it would truncate and match nothing."""
    plt = "4738039174899981234"
    rows = [("s1", f"{plt}::a")]
    published = tmp_path / "annealed_plan.csv"
    _plan(rows).assign(PLT_CN=[plt]).to_csv(published, index=False)
    monkeypatch.setattr(drv, "PREV_PLAN", published)
    out = drv.check_plan_reproduces_published(_plan(rows))
    assert out["identical"] is True


def test_plan_reproduction_says_so_when_there_is_nothing_to_compare(drv, tmp_path,
                                                                    monkeypatch):
    monkeypatch.setattr(drv, "PREV_PLAN", tmp_path / "absent.csv")
    out = drv.check_plan_reproduces_published(_plan([("s1", "100::a")]))
    assert out["compared"] is False
