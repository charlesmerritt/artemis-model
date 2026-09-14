"""Boundary regressions identified by mutation testing of age eligibility."""

import pytest

from pipeline.harvest_eligibility import HarvestEligibilityPolicy, enforce_schedule


def _resolve(years, *, minimum=15, cycle=5, horizon=50, age=0):
    policy = HarvestEligibilityPolicy.from_config({"harvest_eligibility": {
        "mode": "minimum_stand_age", "minimum_age_years": minimum,
        "underage_action": "defer", "unknown_age_action": "exclude_managed_candidate",
    }})
    return enforce_schedule(years, stand_age=age, inv_year=2022,
                            cycle_years=cycle, horizon_years=horizon, policy=policy)


@pytest.mark.parametrize("minimum", [0, 0.5])
def test_nonnegative_policy_floor_allows_subyear_values(minimum):
    assert _resolve({"year": 2023}, minimum=minimum, cycle=1) == ({"year": 2023}, ())


def test_zero_horizon_can_keep_an_eligible_inventory_entry():
    assert _resolve({"year": 2022}, horizon=0, age=15) == ({"year": 2022}, ())


def test_window_bound_without_entry_is_not_a_managed_schedule():
    assert _resolve({"end_year": 2052}, age=None) == ({}, ())


def test_partial_cycle_horizon_clamps_repeated_window_to_last_cycle():
    resolved, notes = _resolve({"start_year": 2037, "end_year": 2072}, horizon=22)
    assert resolved == {"start_year": 2037, "end_year": 2042}
    assert any("clipped" in note and "2044" in note for note in notes)


def test_single_entry_repeated_window_is_inclusive():
    resolved, _ = _resolve({"start_year": 2037, "end_year": 2072}, horizon=15)
    assert resolved == {"start_year": 2037, "end_year": 2037}


def test_repeated_window_entirely_beyond_horizon_returns_empty_mapping():
    resolved, notes = _resolve({"start_year": 2037, "end_year": 2072}, horizon=10)
    assert resolved == {}
    assert any("clipped" in note for note in notes)
