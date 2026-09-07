"""The 2026-09-07 timing-offset grid: what a delay may and may not change.

The driver lives in `weekly-artifact/2026-09-07/make_offset_library.py`. Its runtime
gates cover the real batch — offset-0 keyfiles are compared against 2026-08-31's
params-path render for all 3,782 unshifted runs, and offset-0 trajectories against that
week's committed `trajectory_index.csv` — but those only fire with a compiled FVS and a
1 GiB FIA database behind them. The rules worth protecting on their own are the ones a
future edit could quietly break without any of that:

* a delay shifts *every* year-valued parameter and nothing else, so a delayed variant is
  the same silviculture started later rather than a shorter or different one;
* operations past the horizon are dropped rather than simulated, and regeneration is
  dropped with the stand-replacing harvest that would have triggered it;
* a variant with nothing left inside the horizon is not published at all, because it is
  `no_management` — already in every non-riparian menu — and republishing it would
  inflate the library and bias the mix;
* offset 0 is untouched, which is the whole basis of the week-on-week comparison.

The last is checked here the same way the driver checks it: by rendering the keyfile both
ways and comparing bytes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
LIB_DRIVER = REPO / "weekly-artifact/2026-09-07/make_offset_library.py"
PLAN_DRIVER = REPO / "weekly-artifact/2026-09-07/make_annealed_plan.py"

pytestmark = pytest.mark.skipif(not LIB_DRIVER.exists(),
                                reason="2026-09-07 artifact not present")


def _load(path: Path, name: str):
    """Load a driver by path — `weekly-artifact/2026-09-07` is not an importable name."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mol():
    return _load(LIB_DRIVER, "wa_20260907_offsets")


@pytest.fixture(scope="module")
def plan():
    return _load(PLAN_DRIVER, "wa_20260907_plan")


# Every (template, params) shape the committed 2026-08-17 enumeration actually produces,
# one representative each. Hard-coded rather than read from the CSV so the expected years
# below can be checked by inspection.
CASES = {
    "thin_from_below": {"max_dbh": 8.0, "proportion": 0.35, "year": 2032},
    "clearcut": {"year": 2027},
    "selection_harvest": {"end_year": 2062, "interval": 10, "proportion": 0.2,
                          "start_year": 2032},
    "thin_from_below_repeated": {"end_year": 2067, "interval": 15, "max_dbh": 10.0,
                                 "proportion": 0.3, "start_year": 2027},
    "plantation_rotation": {"clearcut_year": 2037, "thin_max_dbh": 9.0,
                            "thin_proportion": 0.35, "thin_year": 2027},
}


# ---- naming -------------------------------------------------------------------------

def test_offset_zero_keeps_the_base_name(mol, plan):
    """Offset 0 must not be renamed: `assign_prescription` returns base ids, so the
    greedy baseline resolves against the library without a translation table."""
    assert mol.variant_name("family_light_thin", 0) == "family_light_thin"
    assert mol.variant_name("family_light_thin", 5) == "family_light_thin@+5y"
    assert plan.split_variant("family_light_thin") == ("family_light_thin", 0)


@pytest.mark.parametrize("base", sorted(CASES) + ["no_management"])
@pytest.mark.parametrize("offset", [0, 5, 10, 15])
def test_variant_name_round_trips(mol, plan, base, offset):
    assert plan.split_variant(mol.variant_name(base, offset)) == (base, offset)


def test_split_variant_rejects_a_malformed_delay(plan):
    with pytest.raises(ValueError):
        plan.split_variant("family_light_thin@+fivey")


def test_params_round_trip(mol):
    for params in CASES.values():
        assert mol.parse_params(mol.format_params(params)) == params


# ---- what a delay may change --------------------------------------------------------

@pytest.mark.parametrize("template", sorted(CASES))
@pytest.mark.parametrize("offset", [5, 10, 15])
def test_shift_moves_years_and_only_years(mol, template, offset):
    """Proportions, DBH limits and intervals describe the treatment, not its timing."""
    params = CASES[template]
    shifted = mol.shift_params(template, params, offset)
    assert set(shifted) == set(params)
    for key, value in params.items():
        if key in mol.YEAR_PARAMS[template]:
            assert shifted[key] == value + offset, key
        else:
            assert shifted[key] == value, key


@pytest.mark.parametrize("template", sorted(CASES))
def test_zero_offset_is_the_identity(mol, template):
    assert mol.shift_params(template, CASES[template], 0) == CASES[template]


def test_every_template_in_the_library_has_a_year_mapping(mol):
    """A template with no entry in `YEAR_PARAMS` would silently never be delayed."""
    lib = pd.read_csv(REPO / "weekly-artifact/2026-08-17/trajectory_library.csv",
                      usecols=["template"])
    assert set(lib["template"].unique()) <= set(mol.YEAR_PARAMS)


def test_unknown_template_raises_rather_than_passing_through(mol):
    with pytest.raises(ValueError):
        mol.shift_params("shelterwood", {"year": 2030}, 5)


# ---- the horizon rules ---------------------------------------------------------------

def test_entries_past_the_horizon_are_dropped(mol):
    """`selection_harvest` at +15 loses its 2077 entry and keeps the other three."""
    thins, _ = mol.resolve_variant("selection_harvest", CASES["selection_harvest"], 15)
    assert [t.year for t in thins] == [2047, 2057, 2067]


def test_the_cutoff_is_the_last_executable_year_not_the_last_reported_one(mol):
    """The correction that cost a batch: 2072 is reported but never executed.

    `selection_harvest` at +10 lands an entry in 2072. FVS accepts it and runs nothing —
    no cycle begins in the projection's terminal year — so keeping it would publish a
    trajectory claiming an entry that removes no volume.
    """
    assert mol.LAST_SIMULATED_ENTRY_YEAR == mol.LAST_CYCLE_YEAR - mol.CYCLE_YEARS
    thins, _ = mol.resolve_variant("selection_harvest", CASES["selection_harvest"], 10)
    assert [t.year for t in thins] == [2042, 2052, 2062]


def test_a_delayed_variant_that_only_reaches_2072_is_not_published(mol):
    """A 2067 clearcut delayed by five years cuts nothing, so it is not an option."""
    assert mol.resolve_variant("clearcut", {"year": 2067}, 5) is None


def test_offset_zero_keeps_an_inert_2072_entry(mol):
    """Offset 0 is reproduced verbatim, defects included.

    Twelve clearcuts in the enumerated library are scheduled in 2072 and never fire.
    Filtering them here would silently change the control this week's numbers are
    measured against, so they are kept and reported instead.
    """
    thins, _ = mol.resolve_variant("clearcut", {"year": 2072}, 0)
    assert [t.year for t in thins] == [2072]


@pytest.mark.parametrize("offset", [5, 10, 15])
def test_a_variant_with_nothing_left_in_the_horizon_is_not_published(mol, offset):
    """A 2072 clearcut delayed at all is `no_management`, which the menu already has."""
    assert mol.resolve_variant("clearcut", {"year": 2072}, offset) is None


def test_regeneration_follows_its_harvest_out_of_the_horizon(mol):
    """A plantation whose final clearcut is pushed past 2072 keeps its thin and loses
    both the clearcut and the planting that clearcut would have triggered."""
    params = {"clearcut_year": 2067, "thin_max_dbh": 9.0, "thin_proportion": 0.35,
              "thin_year": 2047}
    thins, regen = mol.resolve_variant("plantation_rotation", params, 10)
    assert [t.year for t in thins] == [2057]
    assert regen == []


def test_regeneration_is_kept_when_its_harvest_survives(mol):
    thins, regen = mol.resolve_variant("plantation_rotation",
                                       CASES["plantation_rotation"], 10)
    assert [t.year for t in thins] == [2037, 2047]
    assert [r.year for r in regen] == [2048]


@pytest.mark.parametrize("template", sorted(CASES))
@pytest.mark.parametrize("offset", [5, 10, 15])
def test_a_delay_never_adds_an_entry(mol, template, offset):
    """Delaying can only lose entries off the end of the horizon, never gain them —
    otherwise a "later" variant would be doing more work, not the same work later."""
    base, _ = mol.resolve_variant(template, CASES[template], 0)
    delayed = mol.resolve_variant(template, CASES[template], offset)
    later = [] if delayed is None else [t.year for t in delayed[0]]
    assert len(later) <= len(base)
    # And what survives is exactly the delayed prefix of the original schedule.
    assert later == [y + offset for y in [t.year for t in base]][:len(later)]


# ---- offset 0 is untouched -----------------------------------------------------------

@pytest.mark.parametrize("template", sorted(CASES) + ["no_management"])
def test_offset_zero_renders_byte_identically_to_the_params_path(mol, template):
    """The hinge of the week-on-week comparison.

    This week's driver builds operations itself and hands them to `render_keyfile` via
    `thins=`/`regen=`, so it can drop out-of-horizon entries before rendering.
    2026-08-31 used the `params=` path. If those two disagree at offset 0 then last
    week's plan is not a control and any improvement measured against it is partly a
    rendering change.
    """
    from pipeline.s4_fvs.regime_templates import render_keyfile

    params = dict(CASES.get(template, {}))
    params["stand_sdi"] = {"LP": 40.0, "SA": 10.0}
    kwargs = dict(stand_id="S1", stand_cn="1", regime=template, params=params,
                  inv_year=mol.INV_YEAR, cycle_years=mol.CYCLE_YEARS,
                  num_cycle=mol.NUM_CYCLE)
    resolved = mol.resolve_schedule(template, params, filter_horizon=False)
    thins, regen = ([], []) if resolved is None else resolved
    assert render_keyfile(**kwargs) == render_keyfile(thins=thins, regen=regen, **kwargs)


# ---- the expansion over the carved library -------------------------------------------

def _tiny_library() -> pd.DataFrame:
    """Two upland stands sharing a donor plot, and one riparian stand."""
    return pd.DataFrame([
        {"unit_id": "U1", "PLT_CN": "P1", "prescription": "family_light_thin",
         "template": "thin_from_below", "params": "max_dbh=8.0;proportion=0.35;year=2032",
         "unit_class": "managed"},
        {"unit_id": "U1", "PLT_CN": "P1", "prescription": "no_management",
         "template": "no_management", "params": "", "unit_class": "managed"},
        {"unit_id": "U2", "PLT_CN": "P1", "prescription": "hardwood_clearcut_regen",
         "template": "clearcut", "params": "year=2072", "unit_class": "managed"},
        {"unit_id": "U2", "PLT_CN": "P1", "prescription": "no_management",
         "template": "no_management", "params": "", "unit_class": "managed"},
        {"unit_id": "R1", "PLT_CN": "P2", "prescription": "no_management",
         "template": "no_management", "params": "", "unit_class": "riparian"},
    ])


def test_expansion_keeps_no_management_once_per_stand(mol):
    out = mol.expand_offset_grid(_tiny_library())
    per_stand = out[out["base_prescription"] == "no_management"].groupby("unit_id").size()
    assert set(per_stand) == {1}


def test_expansion_leaves_riparian_libraries_structural(mol):
    """§3 rule 2: a riparian stand's library stays exactly {no_management}, enforced by
    the absence of an alternative rather than by a constraint the search could violate."""
    out = mol.expand_offset_grid(_tiny_library())
    assert list(out[out["unit_id"] == "R1"]["prescription"]) == ["no_management"]


def test_expansion_grows_the_delayable_stand_and_not_the_other(mol):
    out = mol.expand_offset_grid(_tiny_library())
    # U1's 2032 thin delays cleanly to 2037/2042/2047: four variants plus no_management.
    assert sorted(out[out["unit_id"] == "U1"]["prescription"]) == [
        "family_light_thin", "family_light_thin@+10y", "family_light_thin@+15y",
        "family_light_thin@+5y", "no_management"]
    # U2's clearcut is already in the final cycle, so no delay of it survives.
    assert sorted(out[out["unit_id"] == "U2"]["prescription"]) == [
        "hardwood_clearcut_regen", "no_management"]


def test_expansion_preserves_every_offset_zero_row(mol):
    """The expansion is additive: nothing that existed at offset 0 may be dropped or
    renamed, or the library is not a superset of the one last week planned over."""
    lib = _tiny_library()
    out = mol.expand_offset_grid(lib)
    before = set(zip(lib["unit_id"], lib["prescription"]))
    after = set(zip(out[out["offset_years"] == 0]["unit_id"],
                    out[out["offset_years"] == 0]["prescription"]))
    assert before == after


def test_expansion_records_the_entry_years_it_resolved(mol):
    out = mol.expand_offset_grid(_tiny_library())
    row = out[out["prescription"] == "family_light_thin@+10y"].iloc[0]
    assert row["entry_years"] == "2042"
    assert row["n_entries"] == 1
    assert row["params"] == "max_dbh=8.0;proportion=0.35;year=2042"


# ---- the week-on-week difference -----------------------------------------------------

def test_envelope_delta_classifies_each_target(plan, tmp_path, monkeypatch):
    """`recovered` means "no longer provably unreachable", and the four categories must
    partition the targets — a mislabelled row would misstate the headline result."""
    keys = {"dimension": ["county"] * 4, "key": ["Baker"] * 4, "cycle": [1, 2, 3, 4],
            "calendar_year": [2027, 2032, 2037, 2042], "target_cuft": [100.0] * 4}
    prev = pd.DataFrame({**keys, "min_attainable_cuft": [0.0] * 4,
                         "max_attainable_cuft": [50.0, 200.0, 50.0, 200.0],
                         "target_within_envelope": [False, True, False, True],
                         "max_as_pct_of_target": [50.0, 200.0, 50.0, 200.0]})
    now = pd.DataFrame({**keys, "min_attainable_cuft": [0.0] * 4,
                        "max_attainable_cuft": [150.0, 300.0, 60.0, 80.0],
                        "target_within_envelope": [True, True, False, False],
                        "max_as_pct_of_target": [150.0, 300.0, 60.0, 80.0]})
    path = tmp_path / "prev_envelope.csv"
    prev.to_csv(path, index=False)
    monkeypatch.setattr(plan, "PREV_ENVELOPE", path)

    delta, prev_unreachable = plan.envelope_delta(now)
    assert prev_unreachable == 2
    assert list(delta["change"]) == ["recovered", "unchanged", "still unreachable", "lost"]
    assert delta["ceiling_ratio"].iloc[0] == pytest.approx(3.0)


def test_planning_refuses_without_a_batch_marker(plan, tmp_path, monkeypatch):
    """The other half of the smoke-mode fix.

    `make_offset_library.py --limit` now unlinks the marker and refuses to rewrite it, so
    the library left on disk is a partial one that nothing vouches for. That is only safe
    because planning stops dead without the marker rather than reading whatever tables
    happen to be there.
    """
    monkeypatch.setattr(plan, "MANIFEST", tmp_path / "batch_manifest.json")
    with pytest.raises(SystemExit, match="has not completed successfully"):
        plan.require_fresh_batch()


def test_library_driver_takes_its_grid_from_config(mol):
    """Hard-coded horizons were the bug; the constants must come from the same file the
    planner reads, and stay self-consistent once they do."""
    import yaml

    cfg = yaml.safe_load((REPO / "config/projection.yaml").read_text())["projection"]
    assert (mol.INV_YEAR, mol.CYCLE_YEARS, mol.NUM_CYCLE) == (
        cfg["base_year"], cfg["cycle_years"], cfg["n_cycles"])
    assert mol.LAST_CYCLE_YEAR == mol.INV_YEAR + mol.CYCLE_YEARS * mol.NUM_CYCLE
    assert mol.LAST_SIMULATED_ENTRY_YEAR == mol.LAST_CYCLE_YEAR - mol.CYCLE_YEARS


def test_planning_refuses_a_library_from_a_different_horizon(plan):
    """The 🔴 case: `n_cycles` raised to 11 against a library simulated to ten.

    Nothing else catches this. Row counts still match, the marker is still valid, and
    `Landscape` would zero-fill the eleventh cycle for every option — publishing a target
    that "nothing can reach" when in truth nothing simulated it.
    """
    manifest = {"num_cycle": 10, "cycle_years": 5, "inv_year": 2022}
    cfg = {"n_cycles": 11, "cycle_years": 5, "base_year": 2022}
    with pytest.raises(SystemExit, match="different projection grid"):
        plan.check_projection_grid(manifest, cfg)


def test_planning_accepts_a_matching_horizon(plan):
    plan.check_projection_grid({"num_cycle": 10, "cycle_years": 5, "inv_year": 2022},
                               {"n_cycles": 10, "cycle_years": 5, "base_year": 2022})


def test_planning_refuses_a_manifest_that_cannot_state_its_grid(plan):
    """A manifest written before this check existed says nothing about its horizon, and
    silence is not agreement."""
    with pytest.raises(SystemExit, match="does not record"):
        plan.check_projection_grid({"num_cycle": 10},
                                   {"n_cycles": 10, "cycle_years": 5, "base_year": 2022})


def test_the_two_drivers_agree_on_the_grid_today(mol, plan):
    """End to end: the grid the batch stamps is the grid the planner demands."""
    cfg = plan.load_config()
    plan.check_projection_grid(
        {"num_cycle": mol.NUM_CYCLE, "cycle_years": mol.CYCLE_YEARS,
         "inv_year": mol.INV_YEAR}, cfg)


def test_planning_refuses_a_marker_that_does_not_match_the_tables(plan):
    """And a marker from a different batch is rejected on row counts."""
    manifest = {"carved_stands_rows": 11831, "carved_library_rows": 53458,
                "trajectory_cycles_rows": 142791}
    frames = {"stands": pd.DataFrame({"a": range(11831)}),
              "library": pd.DataFrame({"a": range(53458)}),
              "cycles": pd.DataFrame({"a": range(40)})}      # truncated, as a smoke run
    with pytest.raises(SystemExit, match="library has changed since the batch completed"):
        plan.check_batch_matches(manifest, frames["stands"], frames["library"],
                                 frames["cycles"])


def test_envelope_delta_refuses_a_changed_target_amount(plan, tmp_path, monkeypatch):
    """Matching keys are not enough — the *amounts* must match too.

    `config/tpo_targets.yaml` can change a target's value without touching any
    (dimension, key, cycle). A ceiling that never moved would then cross a target that
    did, and be reported here as a timing-grid recovery: a fabricated result, and the one
    this artifact's headline rests on.
    """
    base = {"dimension": ["county"], "key": ["Baker"], "cycle": [1],
            "calendar_year": [2027], "min_attainable_cuft": [0.0],
            "max_attainable_cuft": [120.0], "max_as_pct_of_target": [120.0]}
    prev = pd.DataFrame({**base, "target_cuft": [100.0],
                         "target_within_envelope": [False]})
    # Same ceiling, same key, a target that moved down: "recovered" without the guard.
    now = pd.DataFrame({**base, "target_cuft": [90.0],
                        "target_within_envelope": [True]})
    path = tmp_path / "prev_envelope.csv"
    prev.to_csv(path, index=False)
    monkeypatch.setattr(plan, "PREV_ENVELOPE", path)
    with pytest.raises(AssertionError, match="targets changed target_cuft"):
        plan.envelope_delta(now)


def test_envelope_delta_refuses_a_changed_calendar_year(plan, tmp_path, monkeypatch):
    """A cycle that maps to a different year is a different projection grid."""
    base = {"dimension": ["county"], "key": ["Baker"], "cycle": [1],
            "target_cuft": [100.0], "min_attainable_cuft": [0.0],
            "max_attainable_cuft": [120.0], "max_as_pct_of_target": [120.0],
            "target_within_envelope": [True]}
    prev = pd.DataFrame({**base, "calendar_year": [2027]})
    now = pd.DataFrame({**base, "calendar_year": [2028]})
    path = tmp_path / "prev_envelope.csv"
    prev.to_csv(path, index=False)
    monkeypatch.setattr(plan, "PREV_ENVELOPE", path)
    with pytest.raises(AssertionError, match="targets changed calendar_year"):
        plan.envelope_delta(now)


def test_attribute_lookup_survives_a_reused_frame_id(plan):
    """`id()` is unique only among *live* objects.

    The cache is keyed on `id(stands)`, so once a frame is collected CPython may hand its
    address to the next allocation and a stale entry would answer for a different
    landscape — the greedy baseline would then read another frame's county, owner or age.
    Constructed here by deliberately reusing the key an expired frame left behind, which
    is the same state an `id` collision produces without depending on the allocator
    actually colliding.
    """
    first = pd.DataFrame({"unit_id": ["U1"], "county": ["Baker"]})
    assert plan._attr_lookup(first, "county") == {"U1": "Baker"}

    second = pd.DataFrame({"unit_id": ["U1"], "county": ["Union"]})
    # Force the collision: give `second` the entry `first` would have left at a shared id.
    plan._LOOKUPS[(id(second), "county")] = plan._LOOKUPS[(id(first), "county")]
    assert plan._attr_lookup(second, "county") == {"U1": "Union"}


def test_attribute_lookup_still_caches(plan):
    """The weak reference must not defeat the cache it guards."""
    stands = pd.DataFrame({"unit_id": ["U1"], "county": ["Baker"]})
    assert plan._attr_lookup(stands, "county") is plan._attr_lookup(stands, "county")


def test_envelope_delta_refuses_a_changed_target_set(plan, tmp_path, monkeypatch):
    """Comparing envelopes over different targets would be a meaningless headline."""
    prev = pd.DataFrame({"dimension": ["county"], "key": ["Baker"], "cycle": [1],
                         "calendar_year": [2027], "target_cuft": [100.0],
                         "min_attainable_cuft": [0.0], "max_attainable_cuft": [50.0],
                         "target_within_envelope": [False], "max_as_pct_of_target": [50.0]})
    now = prev.assign(key="Union")
    path = tmp_path / "prev_envelope.csv"
    prev.to_csv(path, index=False)
    monkeypatch.setattr(plan, "PREV_ENVELOPE", path)
    with pytest.raises(AssertionError, match="target set changed"):
        plan.envelope_delta(now)
