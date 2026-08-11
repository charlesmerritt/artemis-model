"""Tests for the scenario factorial (config/scenarios.yaml).

Modelled on Diaz et al. 2018. The scenarios only mean something as a *comparison*, so
these tests check the factorial is complete and that each arm actually isolates the axis
it claims to isolate.
"""

from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s4_fvs.regime_library import get_regime, load_library, regime_names  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def scenarios():
    with open(ROOT / "config" / "scenarios.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def library():
    return load_library()


def test_factorial_is_complete(scenarios):
    """Two axes, two levels each — every combination must be present exactly once."""
    axes = scenarios["axes"]
    objectives = set(axes["objective"])
    constraints = set(axes["practice_constraint"])
    expected = {(o, c) for o in objectives for c in constraints}

    seen = {
        (s["objective"], s["practice_constraint"])
        for s in scenarios["scenarios"].values()
    }
    assert seen == expected, f"factorial incomplete or duplicated: {seen ^ expected}"
    assert len(scenarios["scenarios"]) == len(expected) == 4


def test_axis_levels_referenced_by_scenarios_are_defined(scenarios):
    axes = scenarios["axes"]
    for name, s in scenarios["scenarios"].items():
        assert s["objective"] in axes["objective"], f"{name}: unknown objective"
        assert s["practice_constraint"] in axes["practice_constraint"], (
            f"{name}: unknown practice_constraint"
        )


def test_exactly_one_reference_scenario(scenarios):
    """BAU is the case that claims to describe reality; the others are counterfactuals."""
    refs = [n for n, s in scenarios["scenarios"].items() if s.get("is_reference")]
    assert refs == ["bau"]
    # BAU names its regimes rather than inheriting them: config/management_regimes.yaml
    # carries a second, independently-keyed prescription library, and a factorial that
    # resolved BAU from one library and its counterfactuals from the other would not be
    # comparing like with like. See the note on `bau` in config/scenarios.yaml.
    bau = scenarios["scenarios"]["bau"]["regimes"]["private_industrial"]["by_forest_type"]
    assert {v["regime"] for v in bau.values()} == {
        "pine_plantation_industrial", "hardwood_industrial",
    }, "BAU must name the current-practice regimes, which are the shortest rotations"


def test_every_scenario_regime_exists_in_the_library(scenarios, library):
    known = set(regime_names(library))
    for name, s in scenarios["scenarios"].items():
        for owner, override in s["regimes"].items():
            entries = (
                list(override["by_forest_type"].values())
                if "by_forest_type" in override else [override]
            )
            for entry in entries:
                assert entry["regime"] in known, (
                    f"{name}/{owner}: regime {entry['regime']!r} not in the library"
                )


def test_overridden_owner_classes_exist(scenarios, management_regimes):
    owners = set(management_regimes["owner_classes"])
    for name, s in scenarios["scenarios"].items():
        for owner in s["regimes"]:
            assert owner in owners, f"{name}: overrides unknown owner class {owner!r}"


def _corporate_regimes(scenario, management_regimes=None):
    """Resolve the corporate pine/other regimes a scenario declares.

    Every scenario names them, BAU included, so this never falls back to the prescription
    library in management_regimes.yaml — see that file's note in config/scenarios.yaml.
    """
    override = scenario["regimes"]["private_industrial"]
    return {k: v["regime"] for k, v in override["by_forest_type"].items()}


def test_long_arms_extend_the_rotation(scenarios, management_regimes, library):
    """The `long` objective must actually push the regeneration harvest later."""
    def final_harvest_offset(regime):
        ops = get_regime(regime, library)["operations"]
        terminal = [o for o in ops if o["kind"] in ("regeneration_harvest", "retention_harvest")]
        assert terminal, f"{regime}: corporate regimes must end in a regeneration entry"
        return terminal[-1]["year_offset"]

    by_objective = {}
    for name, s in scenarios["scenarios"].items():
        regimes = _corporate_regimes(s, management_regimes)
        by_objective.setdefault(s["objective"], []).append(
            (name, {k: final_harvest_offset(v) for k, v in regimes.items()})
        )

    for forest_type in ("pine", "other"):
        short = {off[forest_type] for _, off in by_objective["short"]}
        long_ = {off[forest_type] for _, off in by_objective["long"]}
        assert max(short) < min(long_), (
            f"{forest_type}: long-objective rotations ({long_}) do not extend past "
            f"short ({short})"
        )


def test_certified_arms_retain_trees_and_bmp_arms_do_not(scenarios, management_regimes, library):
    """The practice-constraint axis must show up as retention in the regimes themselves."""
    for name, s in scenarios["scenarios"].items():
        regimes = _corporate_regimes(s, management_regimes)
        certified = s["practice_constraint"] == "certified"
        for forest_type, regime in regimes.items():
            block = get_regime(regime, library)
            kinds = {op["kind"] for op in block["operations"]}
            if certified:
                assert "retention_harvest" in kinds, (
                    f"{name}/{forest_type}: certified arm but {regime} clearcuts outright"
                )
                assert block.get("retention_pct", 0) > 0
            else:
                assert "retention_harvest" not in kinds, (
                    f"{name}/{forest_type}: BMP-minimum arm but {regime} retains trees"
                )


def test_each_arm_isolates_one_axis_against_bau(scenarios, management_regimes):
    """short_certified and long_bmp must each differ from BAU on exactly one axis."""
    bau = scenarios["scenarios"]["bau"]
    for name in ("short_certified", "long_bmp"):
        s = scenarios["scenarios"][name]
        differing = sum(
            s[axis] != bau[axis] for axis in ("objective", "practice_constraint")
        )
        assert differing == 1, f"{name} differs from BAU on {differing} axes, expected 1"

    both = scenarios["scenarios"]["long_certified"]
    assert both["objective"] != bau["objective"]
    assert both["practice_constraint"] != bau["practice_constraint"]


def test_certified_buffers_are_wider_than_bmp_minimum(scenarios, bmp_rules):
    """The certified arm must widen every SMZ class, and BMP-minimum must match the
    Florida rules already in config/bmp_rules.yaml rather than restating them wrong."""
    axes = scenarios["axes"]["practice_constraint"]
    minimum = axes["bmp_minimum"]["buffer_widths_ft"]
    certified = axes["certified"]["buffer_widths_ft"]
    florida = bmp_rules["states"]["12"]["buffers"]

    assert set(minimum) == set(certified) == set(florida)
    for cls, width in minimum.items():
        assert width == florida[cls]["width_ft"], (
            f"{cls}: scenario BMP minimum {width} disagrees with config/bmp_rules.yaml "
            f"{florida[cls]['width_ft']}"
        )
        assert certified[cls] > width, f"{cls}: certified buffer is not wider"


def test_buffer_axis_declares_that_it_needs_re_delineation(scenarios):
    """Widening SMZs changes stand polygons, not just prescriptions.

    This is the one axis that cannot be answered from the existing trajectory library,
    and forgetting it would produce a carbon comparison that silently omits the largest
    driver. Keep the caveat attached to the config.
    """
    certified = scenarios["axes"]["practice_constraint"]["certified"]
    assert "buffer_caveat" in certified
    assert "re-run" in certified["buffer_caveat"] or "re-segment" in certified["buffer_caveat"]


def test_retention_pct_is_consistent_between_axis_and_regimes(scenarios, library):
    """The axis declares 15% retention; the certified regimes must actually deliver it."""
    declared = scenarios["axes"]["practice_constraint"]["certified"]["green_tree_retention_pct"]
    assert scenarios["axes"]["practice_constraint"]["bmp_minimum"]["green_tree_retention_pct"] == 0

    for name, s in scenarios["scenarios"].items():
        if s["practice_constraint"] != "certified":
            continue
        for override in s["regimes"].values():
            entries = (
                list(override["by_forest_type"].values())
                if "by_forest_type" in override else [override]
            )
            for entry in entries:
                block = get_regime(entry["regime"], library)
                assert block["retention_pct"] == declared, (
                    f"{name}: {entry['regime']} retains {block.get('retention_pct')}%, "
                    f"axis declares {declared}%"
                )


# --- KPIs -----------------------------------------------------------------------------

def test_all_three_diaz_kpis_are_present(scenarios):
    """Timber, carbon, cash flow — the paper's three. Absence should be explicit."""
    assert set(scenarios["kpis"]) == {
        "cumulative_timber_output", "average_carbon_storage", "discounted_cash_flow",
    }


def test_blocked_kpis_name_their_blockers(scenarios):
    for name, kpi in scenarios["kpis"].items():
        assert kpi["status"] in ("available", "blocked"), f"{name}: bad status"
        if kpi["status"] == "blocked":
            assert kpi["blocked_by"], f"{name}: blocked without naming a blocker"


def test_carbon_kpi_stays_blocked_while_the_fvs_flag_is_off(scenarios, projection_config):
    """The carbon KPI and the carbon tripwire must not disagree.

    If someone re-enables carbon_extension, this fails and forces a decision about the
    HWP pool rather than letting a half-built carbon KPI look finished.
    """
    carbon = scenarios["kpis"]["average_carbon_storage"]
    if not projection_config["fvs"]["carbon_extension"]:
        assert carbon["status"] == "blocked", (
            "carbon_extension is false but the carbon KPI claims to be available"
        )
        assert any("carbon_extension" in b for b in carbon["blocked_by"])
    assert any("HARVESTED WOOD PRODUCTS" in b.upper() for b in carbon["blocked_by"]), (
        "the HWP gap must stay documented — in-forest carbon alone biases every "
        "harvesting scenario downward"
    )


def test_run_cost_matches_the_regimes_actually_referenced(scenarios, library):
    """The stated upper bound must follow from the library, not be a stale number."""
    cost = scenarios["run_cost"]
    assert cost["upper_bound_runs"] == cost["donor_plots"] * cost["distinct_regimes_referenced"]
    assert cost["distinct_regimes_referenced"] == len(regime_names(library))
