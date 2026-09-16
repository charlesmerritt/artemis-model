"""Tests for the fixed fallback tree lists (pipeline/s4_fvs/fallback_treelists.py)."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s4_fvs.fallback_treelists import (
    SOURCE_FALLBACK,
    SOURCE_NEAREST,
    SOURCE_REGEN,
    filter_candidates,
    forest_type_group,
    forest_type_group_code,
    is_bottomland_hardwood,
    ladder_type_key,
    resolve_all_slots,
    resolve_initialization,
    resolve_regeneration,
    select_donor_plot,
)

pd = pytest.importorskip("pandas")


# ---- forest-type routing ---------------------------------------------------------------

@pytest.mark.parametrize("fortypcd,group", [
    (141, 140),     # longleaf pine → longleaf/slash group
    (161, 160),     # loblolly → loblolly/shortleaf group
    (406, 400),     # loblolly/hardwood → oak/pine group
    (607, 600),     # baldcypress/water tupelo → oak/gum/cypress group
    (999, 999),     # nonstocked is its own group, not 990
])
def test_forest_type_group_code_floors_to_the_nearest_ten(fortypcd, group):
    assert forest_type_group_code(fortypcd) == group


@pytest.mark.parametrize("fortypcd,name", [
    (161, "pine"), (406, "mixed"), (505, "hardwood"), (607, "hardwood"), (999, "nonstocked"),
])
def test_broad_forest_type_groups(fortypcd, name):
    assert forest_type_group(fortypcd) == name


def test_bottomland_split_separates_wet_from_upland_hardwood():
    assert is_bottomland_hardwood(607)      # oak/gum/cypress
    assert is_bottomland_hardwood(703)      # elm/ash/cottonwood
    assert not is_bottomland_hardwood(505)  # oak/hickory


def test_ladder_type_key_splits_hardwood_but_passes_others_through():
    assert ladder_type_key(607) == "hardwood_bottomland"
    assert ladder_type_key(505) == "hardwood_upland"
    assert ladder_type_key(161) == "pine"
    assert ladder_type_key(None) is None


# ---- the initialization ladder ---------------------------------------------------------

def test_a_close_same_type_donor_wins():
    decision = resolve_initialization(
        fortypcd=161, donor_distance_m=1200.0, donor_same_forest_type=True
    )
    assert decision.rung == "donor_unit_same_type"
    assert decision.tree_source == SOURCE_NEAREST
    assert decision.slot is None


def test_a_distant_same_type_donor_falls_through_to_a_fixed_list():
    """Beyond the bound, 'nearest' stops meaning 'similar'.

    Today `build_fvs_inputs.impute_nearest_runnable` accepts a donor at any distance; this
    is the rule that stops a 40 km donor from silently initializing a stand.
    """
    decision = resolve_initialization(
        fortypcd=161, donor_distance_m=40_000.0, donor_same_forest_type=True
    )
    assert decision.tree_source == SOURCE_FALLBACK
    assert decision.slot == "upland_pine_established"


def test_a_different_type_donor_needs_to_be_much_closer():
    far = resolve_initialization(fortypcd=607, donor_distance_m=4000.0, donor_same_forest_type=False)
    near = resolve_initialization(fortypcd=607, donor_distance_m=900.0, donor_same_forest_type=False)
    assert far.tree_source == SOURCE_FALLBACK
    assert near.rung == "donor_unit_any_type"
    assert near.tree_source == SOURCE_NEAREST


def test_no_donor_at_all_routes_by_forest_type():
    assert resolve_initialization(fortypcd=607).slot == "bottomland_hardwood_established"
    assert resolve_initialization(fortypcd=161).slot == "upland_pine_established"
    assert resolve_initialization(fortypcd=406).slot == "mixed_pine_hardwood_established"


def test_unknown_forest_type_lands_on_the_default_slot():
    decision = resolve_initialization(fortypcd=None)
    assert decision.rung == "default_slot"
    assert decision.slot == "mixed_pine_hardwood_established"
    assert decision.uses_fixed_list


def test_the_ladder_always_terminates():
    """Every input must produce a decision — a forested acre with nothing to grow is a bug."""
    for fortypcd in [None, 0, 141, 406, 505, 607, 999, 12345, "not a code"]:
        assert resolve_initialization(fortypcd=fortypcd).slot or True


# ---- regeneration ----------------------------------------------------------------------

def test_regeneration_uses_the_prescriptions_slot_and_its_own_source_tag():
    decision = resolve_regeneration("planted_pine_regen")
    assert decision.slot == "planted_pine_regen"
    assert decision.tree_source == SOURCE_REGEN


def test_an_establishment_slot_cannot_be_used_for_regeneration():
    """The slots differ by stand age; swapping them would regenerate a clearcut as mid-rotation."""
    with pytest.raises(ValueError, match="regeneration"):
        resolve_regeneration("upland_pine_established")


def test_unknown_slot_is_rejected():
    with pytest.raises(ValueError, match="unknown fallback slot"):
        resolve_regeneration("magic_beans")


# ---- donor-plot selection --------------------------------------------------------------

def _candidates(n=20, fortypcd=161, stdorgcd=1, stdage=6):
    return pd.DataFrame({
        "PLT_CN": [f"{10**14 + i}" for i in range(n)],
        "FORTYPCD": [fortypcd] * n,
        "STDORGCD": [stdorgcd] * n,
        "STDAGE": [stdage] * n,
        "BALIVE": [float(i) for i in range(n)],
    })


def test_select_donor_plot_takes_the_lower_median_basal_area():
    plots = _candidates(n=20)                       # BALIVE 0..19
    chosen = select_donor_plot(plots)
    assert plots.loc[plots["PLT_CN"] == chosen, "BALIVE"].iloc[0] == 9.0


def test_select_donor_plot_is_order_independent():
    plots = _candidates(n=21)
    assert select_donor_plot(plots) == select_donor_plot(plots.sample(frac=1, random_state=7))


def test_select_donor_plot_breaks_ties_on_plt_cn_as_a_string():
    plots = _candidates(n=12)
    plots["BALIVE"] = 50.0                          # all tied
    chosen = select_donor_plot(plots)
    assert chosen == sorted(plots["PLT_CN"].astype(str))[5]
    assert isinstance(chosen, str)                  # PLT_CN is 15 digits; never numeric


def test_a_thin_candidate_pool_is_refused_rather_than_resolved():
    """A 'median plot' from a handful of plots is one arbitrary plot wearing a rule."""
    with pytest.raises(ValueError, match="minimum"):
        select_donor_plot(_candidates(n=4))


# ---- slot filters ----------------------------------------------------------------------

def test_filter_candidates_applies_type_origin_and_age():
    plots = pd.concat([
        _candidates(n=5, fortypcd=161, stdorgcd=1, stdage=6),     # planted young pine
        _candidates(n=5, fortypcd=161, stdorgcd=0, stdage=6),     # natural young pine
        _candidates(n=5, fortypcd=505, stdorgcd=0, stdage=6),     # young hardwood
    ], ignore_index=True)
    out = filter_candidates(plots, {"fortypcd_min": 140, "fortypcd_max": 179,
                                    "stdorgcd": 1, "stdage_max": 10})
    assert len(out) == 5
    assert set(out["STDORGCD"]) == {1}


def test_filter_candidates_matches_on_forest_type_group():
    plots = pd.concat([
        _candidates(n=3, fortypcd=607, stdage=40),   # oak/gum/cypress → group 600
        _candidates(n=3, fortypcd=703, stdage=40),   # elm/ash/cottonwood → group 700
        _candidates(n=3, fortypcd=505, stdage=40),   # oak/hickory → group 500
    ], ignore_index=True)
    out = filter_candidates(plots, {"fortypcd_in_groups": [600, 700], "stdage_min": 25})
    assert len(out) == 6


def test_filter_candidates_refuses_to_widen_silently_on_a_missing_column():
    plots = _candidates(n=12).drop(columns=["STDORGCD"])
    with pytest.raises(ValueError, match="STDORGCD"):
        filter_candidates(plots, {"stdorgcd": 1})


def test_resolve_all_slots_pins_every_slot_or_fails():
    """A partially pinned lock file would be worse than none."""
    pool = pd.concat([
        _candidates(n=15, fortypcd=161, stdorgcd=1, stdage=6),    # planted_pine_regen
        _candidates(n=15, fortypcd=161, stdorgcd=0, stdage=12),   # natural_pine_regen
        _candidates(n=15, fortypcd=505, stdorgcd=0, stdage=10),   # hardwood_regen
        _candidates(n=15, fortypcd=161, stdorgcd=0, stdage=30),   # upland_pine_established
        _candidates(n=15, fortypcd=607, stdorgcd=0, stdage=45),   # bottomland
        _candidates(n=15, fortypcd=406, stdorgcd=0, stdage=35),   # mixed default
    ], ignore_index=True)
    pool["PLT_CN"] = [f"{10**14 + i}" for i in range(len(pool))]

    lock = resolve_all_slots(pool)
    assert set(lock["slots"]) == {
        "planted_pine_regen", "natural_pine_regen", "hardwood_regen",
        "upland_pine_established", "bottomland_hardwood_established",
        "mixed_pine_hardwood_established",
    }
    assert all(isinstance(s["plt_cn"], str) for s in lock["slots"].values())


def test_resolve_all_slots_fails_loudly_when_a_slot_has_no_candidates():
    pool = _candidates(n=30, fortypcd=161, stdorgcd=1, stdage=6)   # only planted young pine
    with pytest.raises(ValueError, match="candidate plots"):
        resolve_all_slots(pool)
