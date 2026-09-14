"""Age eligibility is applied to actual schedules, including rendered FVS operations."""

from copy import deepcopy

import pytest

from pipeline.harvest_eligibility import HarvestEligibilityPolicy, enforce_schedule
from pipeline.s3_management.regime_assignment import (
    assign_prescription, load_regimes_config, resolve_schedule,
)
from pipeline.s4_fvs import regime_library, regime_templates


def policy(minimum=15):
    config = deepcopy(load_regimes_config())
    config["harvest_eligibility"]["minimum_age_years"] = minimum
    return HarvestEligibilityPolicy.from_config(config)


def years(name, age, *, minimum=15, horizon=50):
    config = load_regimes_config()
    resolved, notes = resolve_schedule(
        config["prescriptions"][name], inv_year=2022, cycle_years=5,
        horizon_years=horizon, stand_age=age, harvest_eligibility=policy(minimum),
    )
    return {key: year - 2022 for key, year in resolved.items()}, notes


@pytest.mark.parametrize("age,expected", [(0, 15), (4.9, 15), (5, 10), (50, 10)])
def test_family_thin_age_at_entry(age, expected):
    resolved, _ = years("family_light_thin", age)
    assert resolved == {"year": expected}


@pytest.mark.parametrize("minimum,expected", [(15, [15, 30, 45]), (40, [40]), (55, [])])
def test_restoration_shifts_whole_sequence_and_clips_horizon(minimum, expected):
    config = deepcopy(load_regimes_config())
    config["harvest_eligibility"]["minimum_age_years"] = minimum
    p = assign_prescription({"OWN_CODE": 7, "FORTYPCD": 161, "stand_age": 0}, config=config)
    assert [t.year - 2022 for t in regime_templates.build_thins(p.template, p.params)] == expected


def test_repeated_end_bound_is_clamped_instead_of_lost():
    resolved, _ = years("public_selection_light", 0, horizon=20)
    assert resolved == {"start_year": 15, "end_year": 20}


@pytest.mark.parametrize("name,age,expected", [
    ("pine_plantation_short_rotation", 0, {"thin_year": 15, "clearcut_year": 25}),
    ("pine_plantation_short_rotation", 22, {"clearcut_year": 5}),
    ("pine_plantation_long_rotation", 0, {"thin_year": 20, "clearcut_year": 35}),
    ("hardwood_clearcut_regen", 0, {"year": 50}),
])
def test_existing_higher_age_targets_remain(name, age, expected):
    assert years(name, age)[0] == expected


@pytest.mark.parametrize("age", [None, float("nan"), float("inf"), -1, "bad", True])
def test_unknown_age_excludes_managed_assignment_with_reason(age):
    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "stand_age": age})
    assert p.template == "no_management"
    assert p.params == {}
    assert p.regen_slot is None
    assert "unknown stand age" in " ".join(p.notes)
    key = regime_templates.render_keyfile("MU", "1234567890123456789", p.template, p.params)
    assert "ThinDBH" not in key and "Estab" not in key


def test_leto_age_alias_and_riparian_override():
    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "STDAGE_MEAN": 22})
    assert p.params == {"year": 2027}
    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "STDAGE_MEAN": 22, "SMZ_Pct": 100})
    assert p.template == "no_management" and p.regen_slot is None


@pytest.mark.parametrize("field,value", [
    ("mode", "disabled"), ("underage_action", "ignore"),
    ("unknown_age_action", "assume_mature"), ("minimum_age_years", -1),
    ("minimum_age_years", float("nan")), ("minimum_age_years", float("inf")),
    ("minimum_age_years", True), ("minimum_age_years", "15"),
])
def test_invalid_policy_rejected(field, value):
    config = deepcopy(load_regimes_config())
    config["harvest_eligibility"][field] = value
    with pytest.raises(ValueError, match="harvest_eligibility"):
        HarvestEligibilityPolicy.from_config(config)


@pytest.mark.parametrize("field", ["mode", "minimum_age_years", "underage_action", "unknown_age_action"])
def test_missing_policy_field_rejected(field):
    config = deepcopy(load_regimes_config())
    del config["harvest_eligibility"][field]
    with pytest.raises(ValueError, match="harvest_eligibility"):
        HarvestEligibilityPolicy.from_config(config)


def test_missing_or_misspelled_policy_is_not_ignored():
    with pytest.raises(ValueError, match="harvest_eligibility"):
        HarvestEligibilityPolicy.from_config({})
    config = deepcopy(load_regimes_config())
    config["harvest_eligibility"]["minimum_age"] = 20
    with pytest.raises(ValueError, match="harvest_eligibility"):
        HarvestEligibilityPolicy.from_config(config)


def test_library_uses_same_deferral_and_preserves_operations():
    with pytest.warns(UserWarning, match="deferred"):
        thins = regime_library.build_thins("pine_plantation_industrial", stand_age=0)
    assert [(t.year - 2022, t.proportion) for t in thins] == [(15, .4), (30, 1.)]
    with pytest.warns(UserWarning, match="unknown stand age"):
        key = regime_library.render_keyfile("MU", "1234567890123456789", "pine_plantation_industrial")
    assert "ThinDBH" not in key and "Estab" not in key


def test_library_render_obeys_actual_projection_horizon():
    with pytest.warns(UserWarning):
        key = regime_library.render_keyfile(
            "MU", "1234567890123456789", "pine_plantation_industrial", stand_age=0,
            cycle_years=5, num_cycle=4,
        )
    lines = [line for line in key.splitlines() if line.startswith("ThinDBH")]
    assert len(lines) == 1 and "2037" in lines[0]


def test_no_management_is_valid_without_age():
    assert years("no_management", None) == ({}, ())
    assert regime_library.build_thins("no_management") == []


def test_grid_deferral_uses_actual_entry_year_and_fractional_age():
    resolved, _ = enforce_schedule(
        {"year": 2032}, stand_age=4.9, inv_year=2022, cycle_years=5,
        horizon_years=50, policy=policy(),
    )
    assert resolved == {"year": 2037}


def test_params_cannot_override_age_checked_entry_year():
    config = deepcopy(load_regimes_config())
    config["prescriptions"]["family_light_thin"]["params"]["year"] = 2027
    with pytest.raises(ValueError, match="schedule"):
        assign_prescription({"OWN_CODE": 3, "stand_age": 0}, config=config)


@pytest.mark.parametrize("cycle,horizon", [(0, 50), (-5, 50), (5, -1), (2.5, 50)])
def test_invalid_projection_grid_fails_clearly(cycle, horizon):
    with pytest.raises(ValueError, match="cycle|horizon"):
        enforce_schedule({"year": 2032}, stand_age=0, inv_year=2022,
                         cycle_years=cycle, horizon_years=horizon, policy=policy())


def test_library_rejects_incompatible_cycle_before_fvs_silently_reschedules():
    with pytest.raises(ValueError, match="cycle"):
        regime_library.render_keyfile("MU", "MU", "public_active_thinning",
                                     stand_age=20, cycle_years=10)


def test_changed_harvest_cannot_reuse_original_regeneration_year():
    regen = [regime_templates.Regeneration(year=2048)]
    with pytest.raises(ValueError, match="regeneration"):
        regime_library.render_keyfile("MU", "MU", "pine_plantation_industrial",
                                     stand_age=0, regen=regen)
    key = regime_library.render_keyfile("MU", "MU", "pine_plantation_industrial",
                                       stand_age=15, regen=regen)
    assert "Estab" in key and "2048" in key
