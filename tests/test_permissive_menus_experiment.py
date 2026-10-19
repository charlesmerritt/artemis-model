"""Menu variants and repeated rotations for experiments/2026-10-08_permissive-menus.

`repeats: true` in config/management_regimes.yaml has never been consumed by a keyfile
(research/regeneration_audit/README.md). The experiment expresses a repeated rotation as
further operations in the same keyfile, each clearcut carrying its own `Estab` packet;
`probe_second_rotation.py` shows FVS replants and regrows after the second one.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from pipeline.s3_management.regime_assignment import load_regimes_config
from pipeline.s4_fvs.regime_templates import Regeneration, ThinDBH, render_keyfile

EXP = Path(__file__).resolve().parents[1] / "experiments/2026-10-08_permissive-menus"
spec = importlib.util.spec_from_file_location("permissive_menus", EXP / "menus.py")
menus = importlib.util.module_from_spec(spec)
sys.modules["permissive_menus"] = menus  # dataclasses resolve their module by name
spec.loader.exec_module(menus)

LIVE = load_regimes_config()
BASELINE = load_regimes_config(str(EXP / "baseline_management_regimes.yaml"))


def _years(entries):
    return [(e.thin.year, e.thin.proportion) for e in entries]


def test_single_rotation_is_the_repository_resolution():
    entries = menus.build_entries(LIVE, "pine_plantation_short_rotation", 20.0,
                                  menus.RotationMode.SINGLE, stand_sdi=None)
    # Age 20 on a 25-year rotation: the thin at 15 is past, so only the clearcut, in 5 years.
    assert _years(entries) == [(2027, 1.0)]
    # The repository downgrades an overmature rotation to the `clearcut` template, which
    # regenerates naturally. Kept as is: the first rotation is the repository's resolution.
    assert entries[0].regen and entries[0].regen[0].natural


def test_a_repeated_plantation_rotation_replants():
    entries = menus.build_entries(LIVE, "pine_plantation_short_rotation", 20.0,
                                  menus.RotationMode.REPEAT, stand_sdi=None)
    second_cut = next(e for e in entries if e.thin.year == 2052)
    assert not second_cut.regen[0].natural


def test_repeated_pine_rotation_thins_and_cuts_again_inside_the_horizon():
    entries = menus.build_entries(LIVE, "pine_plantation_short_rotation", 20.0,
                                  menus.RotationMode.REPEAT, stand_sdi=None)
    # Replanted 2027, thinned at age 15, cut at 25, again: 2042/2052, then 2067 thin and a
    # 2077 clearcut that falls outside the horizon and is dropped.
    assert _years(entries) == [(2027, 1.0), (2042, 0.4), (2052, 1.0), (2067, 0.4)]
    assert [bool(e.regen) for e in entries] == [True, False, True, False]
    assert entries[2].regen[0].year == 2053


def test_repeat_is_ignored_for_a_prescription_that_does_not_repeat():
    single = menus.build_entries(LIVE, "hardwood_clearcut_regen", 20.0,
                                 menus.RotationMode.SINGLE, stand_sdi={"WO": 1.0})
    repeat = menus.build_entries(LIVE, "hardwood_clearcut_regen", 20.0,
                                 menus.RotationMode.REPEAT, stand_sdi={"WO": 1.0})
    assert _years(single) == _years(repeat)


def test_natural_short_rotation_regenerates_from_the_stand_and_repeats():
    entries = menus.build_entries(LIVE, "natural_short_rotation", 10.0,
                                  menus.RotationMode.REPEAT, stand_sdi={"WO": 3.0, "SU": 1.0})
    assert _years(entries) == [(2042, 1.0), (2072, 1.0)]
    assert all(r.natural for e in entries for r in e.regen)
    assert {r.species for r in entries[0].regen} == {"WO", "SU"}


def test_shift_drops_entries_and_their_regeneration_past_the_horizon():
    entries = menus.build_entries(LIVE, "pine_plantation_short_rotation", 20.0,
                                  menus.RotationMode.REPEAT, stand_sdi=None)
    shifted = menus.shift_entries(entries, 25)
    assert _years(shifted) == [(2052, 1.0), (2067, 0.4)]
    assert shifted[0].regen[0].year == 2053
    assert menus.shift_entries(entries, 50) == []


def test_one_rotation_renders_exactly_as_the_repository_renderer():
    thin = ThinDBH(2027, 1.0)
    regen = (Regeneration(2028),)
    ours = menus.render(stand_cn="374861300489998", template="clearcut",
                        entries=[menus.Entry(thin, regen)])
    theirs = render_keyfile(stand_id="S374861300489998", stand_cn="374861300489998",
                            regime="clearcut", thins=[thin], regen=list(regen),
                            num_cycle=menus.NUM_CYCLE)
    assert ours == theirs


def test_two_rotations_render_two_estab_packets():
    key = menus.render(stand_cn="374861300489998", template="clearcut",
                       entries=[menus.Entry(ThinDBH(2027, 1.0), (Regeneration(2028),)),
                                menus.Entry(ThinDBH(2052, 1.0), (Regeneration(2053),))])
    assert key.count("Estab") == 2


@pytest.mark.parametrize("variant", menus.VARIANTS, ids=lambda v: v.name)
def test_riparian_and_no_management_are_untouched_by_every_variant(variant):
    cfg = menus.load_variant_config(variant)
    for owner in cfg["owner_classes"]:
        assert "no_management" in menus.menu_for(variant, cfg, owner, "pine")


def test_the_baseline_variant_keeps_the_old_narrow_menus():
    base = next(v for v in menus.VARIANTS if v.name == "baseline")
    cfg = menus.load_variant_config(base)
    assert menus.menu_for(base, cfg, "local", "pine") == [
        "public_selection_light", "public_thin_restore", "no_management"]


def test_ignoring_forest_type_offers_planted_pine_on_hardwood():
    v = next(v for v in menus.VARIANTS if v.forest_types is menus.ForestTypeRule.IGNORE)
    cfg = menus.load_variant_config(v)
    assert "pine_plantation_short_rotation" in menus.menu_for(v, cfg, "family", "hardwood")


def test_legacy_owner_names_resolve():
    assert menus.policy_owner("private_family") == "family"
    assert menus.policy_owner("private_industrial") == "corporate"
    assert menus.policy_owner("state") == "state"
