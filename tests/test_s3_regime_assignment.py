"""
Tests for config-driven regime assignment (pipeline/s3_management/regime_assignment.py).

`tests/test_s4_regime_templates.py` already pins the keyfile-facing `assign_regime`
contract. This file covers the prescription layer: owner-class routing, the eligible menu,
age-based versus offset scheduling, and the regeneration handoff to the fallback tree lists.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.regime_assignment import (
    HARDWOOD,
    OTHER,
    PINE,
    RIPARIAN_SMZ_PCT,
    assign_prescription,
    assign_prescriptions,
    assign_regime,
    eligible_prescriptions,
    forest_type_branch,
    is_hardwood,
    is_pine,
    resolve_schedule,
)
from pipeline.s4_fvs.regime_templates import build_thins, render_keyfile

INV_YEAR = 2022


# ---- forest-type branch ----------------------------------------------------------------

def test_forest_type_branches():
    assert forest_type_branch({"FORTYPCD": 161}) == PINE
    assert forest_type_branch({"FORTYPCD": 505}) == HARDWOOD
    assert forest_type_branch({"FORTYPCD": 406}) == OTHER      # oak/pine
    assert forest_type_branch({}) == OTHER                     # unknown type


def test_is_hardwood_does_not_claim_pine_types():
    assert is_hardwood({"FORTYPCD": 607})
    assert not is_hardwood({"FORTYPCD": 161})
    assert not is_hardwood({"ForTypName": "Slash pine"})
    assert is_pine({"ForTypName": "Loblolly pine"})


# ---- owner routing ---------------------------------------------------------------------

def test_industrial_pine_gets_the_short_rotation():
    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "DORUC": 54, "ACRES": 4000})
    assert p.owner_class == "private_industrial"
    assert p.prescription_id == "pine_plantation_short_rotation"
    assert p.template == "plantation_rotation"


def test_demoted_corporate_gets_the_other_corporate_menu_not_the_industrial_one():
    """The parcel refinement changes the prescription, not just a label."""
    industrial = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "DORUC": 54, "ACRES": 4000})
    demoted = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "DORUC": 97, "ACRES": 8})
    assert demoted.owner_class == "private_corporate_other"
    assert demoted.prescription_id != industrial.prescription_id
    assert demoted.prescription_id == "pine_plantation_long_rotation"


def test_state_pine_default_is_the_restoration_thin():
    p = assign_prescription({"OWN_CODE": 7, "FORTYPCD": 161})
    assert p.owner_class == "state"
    assert p.prescription_id == "public_thin_restore"
    assert p.template == "thin_from_below_repeated"


def test_local_government_defaults_to_no_entry():
    p = assign_prescription({"OWN_CODE": 8, "FORTYPCD": 161})
    assert p.prescription_id == "no_management"
    assert p.params == {}


def test_masked_ownership_is_rejected_rather_than_assigned_a_regime():
    with pytest.raises(ValueError, match="masked"):
        assign_prescription({"OWN_CODE": 2})       # water


# ---- riparian override -----------------------------------------------------------------

@pytest.mark.parametrize("owner", [3, 4, 6, 7, 8])
def test_riparian_overrides_every_owner_class(owner):
    """No entry of any kind, no buffer class exempted, no ownership rule above it."""
    p = assign_prescription({"OWN_CODE": owner, "FORTYPCD": 161, "SMZ_Pct": RIPARIAN_SMZ_PCT})
    assert p.prescription_id == "no_management"
    assert p.template == "no_management"
    assert p.regen_slot is None
    assert "riparian" in " ".join(p.notes)


def test_riparian_threshold_is_inclusive():
    just_under = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "SMZ_Pct": 49.9})
    assert just_under.template != "no_management"


# ---- scheduling ------------------------------------------------------------------------

def _spec(mode, **kwargs):
    return {"schedule": {"mode": mode, **kwargs}}


def test_age_based_schedule_cuts_a_mature_plantation_soon_not_in_thirty_years():
    """A 22-year-old stand on a 25-year rotation is 3 years out, snapped to the next cycle."""
    years, notes = resolve_schedule(
        _spec("age_based", first_thin_age=15, rotation_years=25, offsets={}),
        inv_year=INV_YEAR, cycle_years=5, horizon_years=50, stand_age=22,
    )
    assert years["clearcut_year"] == 2027
    assert "thin_year" not in years                 # already past thinning age
    assert any("past rotation age" in n for n in notes)


def test_age_based_schedule_places_both_entries_for_a_young_stand():
    years, _ = resolve_schedule(
        _spec("age_based", first_thin_age=15, rotation_years=25, offsets={}),
        inv_year=INV_YEAR, cycle_years=5, horizon_years=50, stand_age=5,
    )
    assert years == {"thin_year": 2032, "clearcut_year": 2042}


def test_entries_never_land_sooner_than_one_cycle_out():
    years, _ = resolve_schedule(
        _spec("age_based", rotation_years=25, offsets={}),
        inv_year=INV_YEAR, cycle_years=5, horizon_years=50, stand_age=90,
    )
    assert years["year"] == INV_YEAR + 5


def test_age_based_falls_back_to_offsets_without_a_stand_age():
    years, notes = resolve_schedule(
        _spec("age_based", first_thin_age=15, rotation_years=25,
              offsets={"thin_year": 15, "clearcut_year": 30}),
        inv_year=INV_YEAR, cycle_years=5, horizon_years=50, stand_age=None,
    )
    assert years == {"thin_year": 2037, "clearcut_year": 2052}
    assert any("no stand_age" in n for n in notes)


def test_an_entry_on_the_horizon_boundary_is_kept():
    """2072 is the year-50 headline raster year in config/projection.yaml, not past the end."""
    years, _ = resolve_schedule(
        _spec("age_based", rotation_years=50, offsets={}),
        inv_year=INV_YEAR, cycle_years=5, horizon_years=50, stand_age=0,
    )
    assert years == {"year": 2072}


def test_entries_past_the_horizon_are_dropped():
    years, notes = resolve_schedule(
        _spec("age_based", rotation_years=60, offsets={}),
        inv_year=INV_YEAR, cycle_years=5, horizon_years=50, stand_age=0,
    )
    assert years == {}
    assert any("horizon" in n for n in notes)


def test_a_prescription_with_no_entry_in_the_horizon_becomes_no_management():
    """Better an explicit grow-only stand than a keyfile with an entry FVS never reaches."""
    import copy

    from pipeline.s3_management.regime_assignment import load_regimes_config

    config = copy.deepcopy(load_regimes_config())
    config["prescriptions"]["hardwood_clearcut_regen"]["schedule"]["rotation_years"] = 70

    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 505, "DORUC": 54,
                             "ACRES": 4000, "stand_age": 0}, config=config)
    assert p.prescription_id == "hardwood_clearcut_regen"      # assignment is unchanged
    assert p.template == "no_management"                        # but nothing renders
    assert p.regen_slot is None
    assert any("no entry falls inside the horizon" in n for n in p.notes)


# ---- regeneration handoff --------------------------------------------------------------

def test_stand_replacing_prescriptions_name_a_regeneration_slot():
    from pipeline.s4_fvs.fallback_treelists import resolve_regeneration

    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "DORUC": 54,
                             "ACRES": 4000, "stand_age": 10})
    assert p.regen_slot == "planted_pine_regen"
    assert resolve_regeneration(p.regen_slot).tree_source == "REGEN_FIXED"


def test_partial_harvest_prescriptions_have_no_regeneration_slot():
    p = assign_prescription({"OWN_CODE": 3, "FORTYPCD": 161})
    assert p.regen_slot is None


# ---- eligible menus --------------------------------------------------------------------

def test_no_management_is_always_on_the_menu():
    for owner_class in ("private_industrial", "private_family", "federal", "state", "local"):
        assert "no_management" in eligible_prescriptions(owner_class)


def test_industrial_menu_offers_the_real_rotation_choice():
    menu = eligible_prescriptions("private_industrial")
    assert "pine_plantation_short_rotation" in menu
    assert "pine_plantation_long_rotation" in menu


def test_eligible_prescriptions_rejects_an_unknown_owner_class():
    with pytest.raises(ValueError, match="unknown owner class"):
        eligible_prescriptions("private_martian")


# ---- everything assigned must actually render ------------------------------------------

@pytest.mark.parametrize("owner", [0, 3, 4, 5, 6, 7, 8])
@pytest.mark.parametrize("fortypcd", [161, 505, 406, None])
@pytest.mark.parametrize("stand_age", [None, 3, 22, 60])
def test_every_assignment_renders_a_valid_keyfile(owner, fortypcd, stand_age):
    """The end-to-end contract: any attributed unit produces a keyfile FVS can read."""
    unit = {"OWN_CODE": owner, "FORTYPCD": fortypcd, "stand_age": stand_age, "SMZ_Pct": 0}
    template, params = assign_regime(unit)
    build_thins(template, params)                   # raises on a bad parameter set
    key = render_keyfile("MU_1", "MU_1", template, params)
    assert key.rstrip().endswith("Stop")


def test_every_scheduled_entry_falls_inside_the_projection_horizon():
    for owner in (0, 3, 4, 5, 6, 7, 8):
        for fortypcd in (161, 505, 406):
            template, params = assign_regime({"OWN_CODE": owner, "FORTYPCD": fortypcd})
            for thin in build_thins(template, params):
                assert INV_YEAR < thin.year <= INV_YEAR + 50, (
                    f"owner {owner} / type {fortypcd}: entry at {thin.year} is outside the horizon"
                )


# ---- frame API -------------------------------------------------------------------------

def test_assign_prescriptions_carries_the_trajectory_library_key():
    pd = pytest.importorskip("pandas")
    units = pd.DataFrame([
        {"unit_id": "a", "OWN_CODE": 4, "FORTYPCD": 161, "DORUC": 54, "ACRES": 3000, "stand_age": 8},
        {"unit_id": "b", "OWN_CODE": 6, "FORTYPCD": 505, "SMZ_Pct": 0},
        {"unit_id": "c", "OWN_CODE": 3, "FORTYPCD": 161, "SMZ_Pct": 80},
    ])
    out = assign_prescriptions(units)
    assert list(out["owner_class"]) == ["private_industrial", "federal", "private_family"]
    assert list(out["prescription"]) == [
        "pine_plantation_short_rotation", "public_selection_light", "no_management",
    ]
    assert out.loc[0, "regen_slot"] == "planted_pine_regen"
    assert out.loc[2, "regime"] == "no_management"      # riparian override


# ---- PR #14 review regressions ---------------------------------------------------------

def test_nan_stand_age_falls_back_to_offsets_instead_of_crashing():
    """A pandas row with no age carries NaN, not None, and float(NaN) succeeds.

    Without rejecting it the value reached math.ceil(NaN) and aborted the whole assignment
    rather than taking the documented offset fallback. (codex P1)
    """
    pd = pytest.importorskip("pandas")
    p = assign_prescription(pd.Series({"OWN_CODE": 4, "FORTYPCD": 161, "DORUC": 54,
                                       "ACRES": 4000, "stand_age": float("nan")}))
    assert p.template == "plantation_rotation"
    assert p.params["clearcut_year"] == 2052          # the offset schedule


@pytest.mark.parametrize("age", [float("nan"), float("inf"), -1, -26])
def test_unusable_stand_ages_are_treated_as_missing(age):
    from pipeline.s3_management.regime_assignment import _stand_age
    assert _stand_age({"stand_age": age}) is None


def test_a_negative_stand_age_does_not_crash_the_assignment():
    """Reported reproducer: a negative FIA sentinel dropped clearcut_year but kept
    thin_year, and the rotation template then asked for the missing key. (review, critical)
    """
    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "DORUC": 54,
                             "ACRES": 4000, "stand_age": -26})
    assert p.template == "plantation_rotation"


def test_a_rotation_that_keeps_only_its_thin_downgrades_to_a_thin():
    """The horizon filter can drop either half of the pair, not just the thin."""
    from pipeline.s3_management.regime_assignment import _template_for
    spec = {"template": "plantation_rotation"}
    assert _template_for(spec, {"thin_year": 2037, "clearcut_year": 2052}) == "plantation_rotation"
    assert _template_for(spec, {"clearcut_year": 2052}) == "clearcut"
    assert _template_for(spec, {"thin_year": 2037}) == "thin_from_below"
    assert _template_for(spec, {}) == "no_management"


def test_a_long_rotation_past_the_horizon_still_renders():
    """A prescription whose harvest falls outside the horizon but whose thin does not."""
    import copy

    from pipeline.s3_management.regime_assignment import load_regimes_config

    config = copy.deepcopy(load_regimes_config())
    config["prescriptions"]["pine_plantation_short_rotation"]["schedule"]["rotation_years"] = 90
    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "DORUC": 54,
                             "ACRES": 4000, "stand_age": 0}, config=config)
    assert p.template == "thin_from_below"        # only the thin survived
    assert p.params["year"] == 2037
    build_thins(p.template, p.params)              # renders


def test_masked_ownership_is_rejected_even_when_the_unit_is_riparian():
    """Masking must precede every override.

    The riparian branch used to run first, so a water pixel with SMZ_Pct >= 50 returned
    `no_management` silently — putting water into the growth outputs as an unharvested
    stand. (review, major)
    """
    with pytest.raises(ValueError, match="masked"):
        assign_prescription({"OWN_CODE": 2, "SMZ_Pct": 80})
    with pytest.raises(ValueError, match="masked"):
        assign_prescription({"OWN_CODE": 1, "SMZ_Pct": 95})


def test_the_eligible_menu_filters_by_forest_type():
    """An industrial hardwood stand must not be offered a pine-plantation prescription.

    Selecting one would apply pine thinning parameters and inject a planted-pine
    regeneration tree list into a hardwood trajectory. (codex P2)
    """
    hardwood = eligible_prescriptions("private_industrial", HARDWOOD)
    assert "pine_plantation_short_rotation" not in hardwood
    assert "pine_plantation_long_rotation" not in hardwood
    assert "hardwood_clearcut_regen" in hardwood

    pine = eligible_prescriptions("private_industrial", PINE)
    assert "pine_plantation_short_rotation" in pine
    assert "hardwood_clearcut_regen" not in pine


def test_state_hardwood_is_not_offered_a_pine_rotation():
    assert "pine_plantation_long_rotation" not in eligible_prescriptions("state", HARDWOOD)
    assert "pine_plantation_long_rotation" in eligible_prescriptions("state", PINE)


def test_an_unknown_forest_type_gets_only_type_agnostic_prescriptions():
    """A pine rotation on a stand that may not be pine is a guess, not a policy."""
    for name in eligible_prescriptions("private_industrial", OTHER):
        assert name in {"no_management", "hardwood_clearcut_regen"} or "pine" not in name


def test_no_management_survives_every_forest_type_filter():
    for branch in (PINE, HARDWOOD, OTHER):
        for owner in ("private_industrial", "private_family", "federal", "local"):
            assert "no_management" in eligible_prescriptions(owner, branch)


def test_the_unfiltered_menu_is_still_available_for_describing_policy():
    assert set(eligible_prescriptions("private_industrial")) >= set(
        eligible_prescriptions("private_industrial", PINE)
    )


def test_eligible_prescriptions_rejects_an_unknown_forest_branch():
    with pytest.raises(ValueError, match="unknown forest branch"):
        eligible_prescriptions("private_industrial", "swamp")


def test_every_default_is_eligible_under_its_own_forest_branch():
    """The filter must not exclude the very prescription the unit is assigned."""
    from pipeline.s3_management.regime_assignment import load_regimes_config

    config = load_regimes_config()
    for owner, spec in config["owner_classes"].items():
        for branch, default in spec["default"].items():
            assert default in eligible_prescriptions(owner, branch), (
                f"{owner}/{branch} defaults to {default}, which its own filter excludes"
            )
