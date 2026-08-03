"""
Consistency tests for the three policy configs: ownership, management regimes, and
fallback tree lists.

These are scaffold tests in the same spirit as `tests/test_config.py` — they run anywhere,
touch no data drive, and catch the failure mode that policy configs are prone to: the
files drift apart from each other and from the code that reads them, and nothing notices
until a run produces a wrong number.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.owner_classes import MASKED, load_ownership_policy
from pipeline.s3_management.regime_assignment import (
    _TEMPLATE_PARAMS,
    RIPARIAN_SMZ_PCT,
    eligible_prescriptions,
    load_regimes_config,
)
from pipeline.s4_fvs.fallback_treelists import load_fallback_policy
from pipeline.s4_fvs.regime_templates import REGIMES


# ---- ownership policy ------------------------------------------------------------------

def test_every_harris_forest_class_maps_to_an_owner_class(ownership_policy, projection_config):
    """No forest pixel value may fall through: every unmasked Harris value needs a class."""
    claimed = {v for spec in ownership_policy["classes"].values() for v in spec["harris_values"]}
    masked = set(ownership_policy["masked_harris_values"])
    all_values = set(projection_config["ownership"]["classes"])
    assert claimed | masked == all_values


def test_masked_values_match_projection_config(ownership_policy, projection_config):
    assert set(ownership_policy["masked_harris_values"]) == set(
        projection_config["ownership"]["mask_values"]
    )


def test_every_owner_class_has_a_tpo_group_that_exists(ownership_policy, config_dir):
    """TPO groups are the only owner dimension the harvest scheduler budgets against."""
    import yaml
    with open(config_dir / "tpo_targets.yaml") as f:
        tpo = yaml.safe_load(f)
    valid = set(tpo["by_owner_group"]) - {"All owners"}
    for name, spec in ownership_policy["classes"].items():
        assert spec["tpo_group"] in valid, f"{name} maps to unknown TPO group {spec['tpo_group']!r}"


def test_corporate_default_class_is_a_real_class(ownership_policy):
    default = ownership_policy["corporate_default_class"]
    assert default in ownership_policy["classes"]
    assert 4 in ownership_policy["classes"][default]["harris_values"]


def test_industrial_and_non_industrial_doruc_sets_are_disjoint(ownership_policy):
    """A code that fires both signals at once would make the refinement order-dependent."""
    rules = ownership_policy["private_refinement"]
    assert not set(rules["timberland_doruc"]) & set(rules["non_industrial_doruc"])


def test_owner_name_refinement_stays_disabled_until_the_field_is_confirmed(ownership_policy):
    """No owner-name column is confirmed present in FL_5_Co_Parcels.

    Enabling this without a verified field name would silently match nothing and quietly
    change every corporate unit's refinement. Flip it in the same commit that records the
    verified column name.
    """
    cfg = ownership_policy["private_refinement"]["owner_name"]
    if cfg["enabled"]:
        assert cfg["field"], "owner_name refinement enabled without naming the parcel field"


def test_masked_ownership_returns_the_masked_sentinel():
    from pipeline.s3_management.owner_classes import harris_value_to_class
    assert harris_value_to_class(1) == MASKED     # non_forest
    assert harris_value_to_class(2) == MASKED     # water


# ---- management regimes ----------------------------------------------------------------

def test_regime_config_agrees_with_projection_config(management_regimes, projection_config):
    proj = projection_config["projection"]
    assert management_regimes["inventory_year"] == proj["base_year"]
    assert management_regimes["cycle_years"] == proj["cycle_years"]
    assert management_regimes["horizon_years"] == proj["horizon_years"]


def test_every_prescription_names_a_registered_template(management_regimes):
    for name, spec in management_regimes["prescriptions"].items():
        assert spec["template"] in REGIMES, f"{name} names unknown template {spec['template']!r}"


def test_every_prescription_param_is_one_the_template_consumes(management_regimes):
    """A `params:` key the builder never reads is a typo that would vanish silently."""
    for name, spec in management_regimes["prescriptions"].items():
        allowed = _TEMPLATE_PARAMS[spec["template"]]
        for key in spec.get("params", {}):
            assert key in allowed, f"{name}: template {spec['template']!r} ignores param {key!r}"


def test_every_prescription_has_offsets_as_the_age_based_fallback(management_regimes):
    """Age-based schedules need a fallback for units with no stand age."""
    for name, spec in management_regimes["prescriptions"].items():
        schedule = spec["schedule"]
        if schedule["mode"] == "none":
            continue
        assert schedule.get("offsets"), f"{name} has no offsets to fall back on"


def test_stand_replacing_prescriptions_declare_a_regeneration_slot(management_regimes,
                                                                   fallback_treelists):
    """A clearcut with no regeneration grows an empty stand for the rest of the horizon."""
    for name, spec in management_regimes["prescriptions"].items():
        if spec["template"] not in {"clearcut", "plantation_rotation"}:
            continue
        regen = spec.get("regen")
        assert regen, f"{name} removes the whole stand but declares no regen slot"
        slot = regen["treelist_slot"]
        assert slot in fallback_treelists["slots"], f"{name} names unknown slot {slot!r}"
        assert fallback_treelists["slots"][slot]["use"] == "regeneration"


def test_every_owner_class_declares_defaults_for_all_three_forest_branches(management_regimes):
    """`other` is not a rounding case — it is what an unknown forest type resolves to."""
    for owner, spec in management_regimes["owner_classes"].items():
        for branch in ("pine", "hardwood", "other"):
            assert branch in spec["default"], f"{owner} has no {branch} default"
            assert spec["default"][branch] in management_regimes["prescriptions"]


def test_every_owner_class_offers_two_or_three_prescriptions(management_regimes):
    """The stated policy: 2-3 real choices per owner, plus the universal no-entry option.

    `menu_policy: minimal` classes are exempt by declaration — `unknown` is missing
    information rather than an owner type, and a wide menu there would let the scheduler
    invent behaviour to hit a volume target.
    """
    for owner, spec in management_regimes["owner_classes"].items():
        menu = [p for p in spec["eligible"] if p != "no_management"]
        if spec.get("menu_policy") == "minimal":
            assert len(menu) == 1, f"{owner} declares a minimal menu but offers {len(menu)}"
            continue
        assert 2 <= len(menu) <= 3, f"{owner} offers {len(menu)} prescriptions, expected 2-3"


def test_every_default_is_also_eligible(management_regimes):
    """A default the scheduler is not allowed to pick would flip on the first schedule run."""
    for owner in management_regimes["owner_classes"]:
        eligible = set(eligible_prescriptions(owner))
        defaults = set(management_regimes["owner_classes"][owner]["default"].values())
        assert defaults <= eligible, f"{owner}: defaults {defaults - eligible} not in the menu"


def test_owner_classes_match_the_ownership_policy(management_regimes, ownership_policy):
    assert set(management_regimes["owner_classes"]) == set(ownership_policy["classes"])


def test_riparian_override_is_absolute_and_matches_the_module_constant(management_regimes):
    override = management_regimes["overrides"]["riparian"]
    assert override["absolute"] is True
    assert override["prescription"] == "no_management"
    assert str(RIPARIAN_SMZ_PCT) in override["when"]


def test_trajectory_library_cost_is_stated_correctly(management_regimes):
    """Library size is the cost driver; the stated number must track the actual count."""
    n_prescriptions = len(management_regimes["prescriptions"])
    expected = 693 * n_prescriptions * management_regimes["si_bins"]
    assert management_regimes["estimated_max_runs_pilot"] == expected, (
        f"{n_prescriptions} prescriptions x {management_regimes['si_bins']} SI bins x 693 "
        f"pilot plots = {expected}; update estimated_max_runs_pilot"
    )


# ---- fallback tree lists ---------------------------------------------------------------

def test_every_slot_declares_a_use_and_a_filter(fallback_treelists):
    for name, spec in fallback_treelists["slots"].items():
        assert spec["use"] in {"regeneration", "establishment"}, name
        assert spec["filter"], f"{name} has an empty filter and would match every plot"


def test_exactly_one_default_slot(fallback_treelists):
    defaults = [n for n, s in fallback_treelists["slots"].items() if s.get("default")]
    assert len(defaults) == 1, f"expected one default slot, found {defaults}"


def test_ladder_terminates_unconditionally(fallback_treelists):
    """The last rung must resolve for any input; otherwise a unit can get nothing at all."""
    last = fallback_treelists["initialization_ladder"][-1]
    assert last["method"] == "fallback_slot"
    assert last.get("slot") in fallback_treelists["slots"]


def test_ladder_slot_mapping_names_real_establishment_slots(fallback_treelists):
    for rung in fallback_treelists["initialization_ladder"]:
        for slot in rung.get("mapping", {}).values():
            assert slot in fallback_treelists["slots"]
            assert fallback_treelists["slots"][slot]["use"] == "establishment"


def test_donor_distance_bounds_tighten_down_the_ladder(fallback_treelists):
    """A same-type donor may come from further away than an any-type one."""
    distances = [
        rung["constraints"]["max_distance_m"]
        for rung in fallback_treelists["initialization_ladder"]
        if rung["method"] == "nearest_runnable_unit"
    ]
    assert distances == sorted(distances, reverse=True)


def test_unresolved_slots_refuse_to_produce_a_plt_cn(fallback_treelists):
    """Until the lock file exists, asking for a fixed list must fail loudly.

    Substituting an arbitrary tree list for a missing pin would be invisible in every
    downstream summary, which is exactly the class of error this whole file exists to
    prevent.
    """
    from pipeline.s4_fvs.fallback_treelists import plt_cn_for_slot
    if fallback_treelists["status"] == "resolved":
        pytest.skip("slots are resolved; the lock file supplies the pins")
    with pytest.raises(RuntimeError, match="--resolve"):
        plt_cn_for_slot("planted_pine_regen")


def test_forest_type_group_ranges_partition_fortypcd(fallback_treelists):
    spans = fallback_treelists["forest_type_groups"]
    ordered = sorted(spans.values(), key=lambda s: s["min"])
    for lower, upper in zip(ordered, ordered[1:]):
        assert lower["max"] < upper["min"], "forest type group ranges overlap"


def test_configs_load_through_their_modules():
    """The loaders and the fixtures must be reading the same files."""
    assert load_ownership_policy()["version"] == 1
    assert load_regimes_config()["version"] == 1
    assert load_fallback_policy()["version"] == 1
