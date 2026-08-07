"""Tests for FVS regime templates + assignment."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s4_fvs.regime_templates import (
    REGEN_DEFAULTS,
    REGIMES,
    Regeneration,
    ThinDBH,
    apportion_by_sdi,
    build_regeneration,
    build_thins,
    render_keyfile,
    render_schedule_block,
)
from pipeline.s3_management.regime_assignment import assign_regime, is_pine


def test_thindbh_renders_fixed_width_fields():
    line = ThinDBH(year=2032, proportion=0.4, max_dbh=8.0).render()
    # keyword (10) + 5 fields (10 each) = 60 chars
    assert len(line) == 60
    assert line.startswith("ThinDBH")
    assert line[10:20].strip() == "2032"     # year


def test_thindbh_field_order_year_mindbh_maxdbh_proportion_species():
    line = ThinDBH(year=2040, proportion=0.5, min_dbh=0, max_dbh=999, species=0).render()
    fields = [line[i:i + 10].strip() for i in range(0, 60, 10)]
    assert fields == ["ThinDBH", "2040", "0", "999", "0.50", "0"]


def test_thindbh_rejects_out_of_range_proportion():
    with pytest.raises(ValueError, match="proportion"):
        ThinDBH(year=2030, proportion=1.5).render()


def test_no_management_has_no_thins():
    assert build_thins("no_management", {}) == []


def test_clearcut_removes_everything_once():
    thins = build_thins("clearcut", {"year": 2052})
    assert len(thins) == 1
    assert thins[0].proportion == 1.0
    assert thins[0].min_dbh == 0.0 and thins[0].max_dbh == 999.0


def test_thin_from_below_targets_small_trees():
    thins = build_thins("thin_from_below", {"year": 2032, "max_dbh": 8, "proportion": 0.4})
    assert len(thins) == 1
    assert thins[0].max_dbh == 8.0
    assert thins[0].proportion == 0.4


def test_selection_harvest_repeats_on_interval():
    thins = build_thins("selection_harvest",
                        {"start_year": 2032, "end_year": 2062, "interval": 10, "proportion": 0.2})
    assert [t.year for t in thins] == [2032, 2042, 2052, 2062]
    assert all(t.proportion == 0.2 for t in thins)


def test_plantation_rotation_thins_then_clearcuts():
    thins = build_thins("plantation_rotation",
                        {"thin_year": 2037, "clearcut_year": 2052})
    assert len(thins) == 2
    assert thins[0].proportion < 1.0          # commercial thin
    assert thins[1].proportion == 1.0         # final clearcut
    assert thins[1].year == 2052


def test_build_thins_rejects_unknown_regime():
    with pytest.raises(ValueError, match="unknown regime"):
        build_thins("burn_it_all", {})


def test_render_keyfile_includes_scaffold_and_thins():
    key = render_keyfile("MU_123", "MU_123", "thin_from_below",
                         {"year": 2032, "max_dbh": 8, "proportion": 0.4})
    assert "StandCN" in key and "MU_123" in key
    assert "FVS_TreeInit_Plot" in key and "%Stand_CN%" in key
    assert "ThinDBH" in key
    assert key.rstrip().endswith("Stop")


def test_render_keyfile_no_management_has_no_thindbh():
    key = render_keyfile("MU_1", "MU_1", "no_management")
    assert "ThinDBH" not in key


# ---- keyword field placement ---------------------------------------------------------
#
# FVS reads keyword records in 10-column fields: cols 1-10 keyword, 11-20 field 1,
# 21-30 field 2, ... A value in the wrong field is accepted silently and applies a
# different parameter, so every field position gets pinned here.

def _field(line: str, n: int) -> str:
    """Field `n` of a keyword record (n=0 is the keyword itself)."""
    return line[n * 10:(n + 1) * 10].strip()


def _line(key: str, keyword: str) -> str:
    return next(ln for ln in key.splitlines() if ln.startswith(keyword))


def test_timeint_puts_the_interval_in_field_2_not_field_1():
    """Field 1 of TIMEINT is the cycle *number*; field 2 is the interval length.

    With the interval in field 1 the record is still valid — it sets a cycle index and
    leaves the interval at the FVS default — so a 10-cycle run silently projects 100
    years instead of 50. Matches the layout of the fixture verified against real FVS
    runs in research/restart_fidelity/make_keyfiles.py.
    """
    line = _line(render_keyfile("MU_1", "MU_1", "no_management"), "TimeInt")
    assert _field(line, 1) == "", "field 1 must stay blank so the interval applies to all cycles"
    assert _field(line, 2) == "5"


def test_invyear_and_numcycle_use_field_1():
    key = render_keyfile("MU_1", "MU_1", "no_management", inv_year=2022, num_cycle=10)
    assert _field(_line(key, "InvYear"), 1) == "2022"
    assert _field(_line(key, "NumCycle"), 1) == "10"


def test_schedule_block_matches_the_verified_fixture_fields():
    """Same field positions as the keyfile that produced 1999/2004/2009/2014/2019."""
    from research.restart_fidelity.make_keyfiles import CYCLE_YEARS, INV_YEAR

    fixture = {
        "InvYear": f"InvYear       {INV_YEAR}",
        "TimeInt": f"TimeInt                 {CYCLE_YEARS}",
        "NumCycle": "NumCycle      4",
    }
    ours = render_schedule_block(INV_YEAR, CYCLE_YEARS, 4)
    for keyword, fixture_line in fixture.items():
        mine = _line(ours, keyword)
        for n in (1, 2):
            assert _field(mine, n) == _field(fixture_line, n), f"{keyword} field {n}"


# ---- regeneration --------------------------------------------------------------------

def test_plant_field_order_matches_esin_f():
    """date, species, trees/acre, % survival, age, height — Open-FVS estb/esin.f opt. 2."""
    line = Regeneration(year=2053, species="LP", trees_per_acre=605, survival_pct=90,
                        age=1, height_ft=0.5).render()
    assert [_field(line, n) for n in range(7)] == [
        "Plant", "2053", "LP", "605", "90.00", "1.0", "0.5",
    ]


def test_natural_shares_the_plant_field_layout():
    """NATURAL (opt. 3) jumps into the PLANT handler before the date is read."""
    line = Regeneration(year=2053, species="LP", trees_per_acre=400, natural=True).render()
    assert _field(line, 0) == "Natural"
    assert [_field(line, n) for n in (1, 2, 3)] == ["2053", "LP", "400"]


@pytest.mark.parametrize("kwargs, match", [
    ({"trees_per_acre": 0}, "trees_per_acre"),      # esin.f rejects the record outright
    ({"survival_pct": 0.0}, "survival_pct"),        # FVS would silently substitute 100.0
    ({"survival_pct": 101.0}, "survival_pct"),
    ({"species": "LOBL"}, "species"),               # SPDECD truncates to 3 chars
])
def test_regeneration_rejects_values_fvs_would_mangle(kwargs, match):
    with pytest.raises(ValueError, match=match):
        Regeneration(year=2053, **{"species": "LP", "trees_per_acre": 605, **kwargs}).render()


def test_clearcut_regenerates_naturally_by_default():
    regen = build_regeneration("clearcut", {"year": 2052})
    assert len(regen) == 1
    assert regen[0].natural is True
    assert regen[0].year == 2053          # harvest year + the 1-year default delay


def test_plantation_rotation_replants_after_the_final_cut():
    regen = build_regeneration("plantation_rotation", {"thin_year": 2037, "clearcut_year": 2052})
    assert len(regen) == 1
    assert regen[0].natural is False      # industrial replant, not natural regen
    assert regen[0].year == 2053


@pytest.mark.parametrize("regime, params", [
    ("no_management", {}),
    ("thin_from_below", {"year": 2032}),
    ("selection_harvest", {"start_year": 2032}),
])
def test_partial_cuts_do_not_regenerate(regime, params):
    """The residual overstory is the seed source; a Plant record would double-count."""
    assert build_regeneration(regime, params) == []


# ---- Diaz et al. natural-regeneration species rule -----------------------------------
#
# Natural regeneration is limited to the species already present in the stand, with density
# set by each species' share of stand SDI (Diaz et al. 2015 p. 27, docs/references/).
# Planting is separate: planted species is a management choice, not a property of the
# stand that was cut.

def test_apportion_splits_tpa_by_sdi_share():
    pairs = apportion_by_sdi({"LP": 75.0, "SA": 25.0}, total_tpa=400)
    assert pairs == [("LP", 300.0), ("SA", 100.0)]


def test_apportion_drops_trace_species_and_renormalizes():
    """A 2% species would otherwise get a near-zero record; the rest must still sum to total."""
    pairs = apportion_by_sdi({"LP": 60.0, "SA": 38.0, "WO": 2.0}, total_tpa=400, min_share=0.05)
    assert [sp for sp, _ in pairs] == ["LP", "SA"]
    assert sum(tpa for _, tpa in pairs) == pytest.approx(400.0)


def test_apportion_keeps_the_largest_when_everything_is_below_the_floor():
    """A very mixed stand is not an empty one — returning nothing would drop the harvest."""
    even = {code: 1.0 for code in ("LP", "SA", "WO", "RM", "SU", "YP", "BG", "WA", "HI", "CO",
                                   "SP", "LL", "TM", "PP", "PD", "WP", "VP", "BY", "PC", "HM",
                                   "FM", "BE", "SV", "SM", "BU")}
    pairs = apportion_by_sdi(even, total_tpa=400, min_share=0.05)
    assert len(pairs) == 1
    assert pairs[0][1] == pytest.approx(400.0)


def test_apportion_ignores_absent_species():
    pairs = apportion_by_sdi({"LP": 80.0, "SA": 20.0, "WO": 0.0}, total_tpa=100)
    assert [sp for sp, _ in pairs] == ["LP", "SA"]


@pytest.mark.parametrize("sdi, tpa, match", [
    ({"LP": 1.0}, 0, "total_tpa"),
    ({"LP": 0.0}, 400, "positive SDI"),
])
def test_apportion_rejects_degenerate_input(sdi, tpa, match):
    with pytest.raises(ValueError, match=match):
        apportion_by_sdi(sdi, total_tpa=tpa)


def test_natural_regen_follows_stand_composition():
    regen = build_regeneration("clearcut", {
        "year": 2052, "regen_tpa": 400, "stand_sdi": {"SA": 70.0, "LP": 30.0},
    })
    assert [(r.species, r.trees_per_acre) for r in regen] == [("SA", 280.0), ("LP", 120.0)]
    assert all(r.natural for r in regen)
    assert {r.year for r in regen} == {2053}


def test_natural_regen_without_composition_warns_and_falls_back(caplog):
    """Falling back means the Diaz rule was skipped for that stand — it must not be silent."""
    with caplog.at_level("WARNING"):
        regen = build_regeneration("clearcut", {"year": 2052})
    assert len(regen) == 1
    assert "stand_sdi" in caplog.text


def test_planting_ignores_stand_composition():
    """What gets planted is a management decision, not a property of the stand that was cut."""
    regen = build_regeneration("plantation_rotation", {
        "thin_year": 2037, "clearcut_year": 2052, "stand_sdi": {"WO": 90.0, "RM": 10.0},
    })
    assert [r.species for r in regen] == [REGEN_DEFAULTS["plant_species"]]
    assert regen[0].natural is False


def test_planted_stands_suppress_automatic_regeneration():
    """NATURAL forces NOINGROW/NOAUTALY itself; planted stands state them so the two
    trajectory types stay comparable."""
    key = render_keyfile("MU_1", "MU_1", "plantation_rotation",
                         {"thin_year": 2037, "clearcut_year": 2052})
    estab = key.split("Estab")[1]
    assert "NoInGrow" in estab and "NoAutAly" in estab


def test_naturally_regenerated_stands_do_not_repeat_the_suppression():
    key = render_keyfile("MU_1", "MU_1", "clearcut", {"year": 2052})
    assert "NoInGrow" not in key


def test_regen_mode_and_species_are_overridable():
    regen = build_regeneration("clearcut", {
        "year": 2052, "regen": "plant", "regen_species": "SA", "regen_tpa": 700,
        "regen_delay_years": 0,
    })
    assert (regen[0].natural, regen[0].species, regen[0].year) == (False, "SA", 2052)


def test_regen_can_be_switched_off():
    assert build_regeneration("clearcut", {"year": 2052, "regen": "none"}) == []


def test_regen_rejects_an_unknown_mode():
    with pytest.raises(ValueError, match="regen must be one of"):
        build_regeneration("clearcut", {"year": 2052, "regen": "coppice"})


def test_estab_packet_is_wrapped_and_dated():
    key = render_keyfile("MU_1", "MU_1", "clearcut", {"year": 2052})
    lines = key.splitlines()
    start = lines.index(next(ln for ln in lines if ln.startswith("Estab")))
    assert _field(lines[start], 1) == "2053"          # Estab field 1 = date of disturbance
    assert lines[start + 1].startswith("Natural")
    assert lines[start + 2] == "End"


def test_no_empty_estab_packet_when_nothing_regenerates():
    key = render_keyfile("MU_1", "MU_1", "thin_from_below", {"year": 2032})
    assert "Estab" not in key


def test_all_registered_regimes_render():
    params = {
        "no_management": {},
        "clearcut": {"year": 2052},
        "thin_from_below": {"year": 2032},
        "selection_harvest": {"start_year": 2032},
        "plantation_rotation": {"thin_year": 2037, "clearcut_year": 2052},
    }
    for name in REGIMES:
        key = render_keyfile("MU_1", "MU_1", name, params[name])
        assert "Process" in key


# ---- assignment ----------------------------------------------------------------------

def test_is_pine_detects_code_and_name():
    assert is_pine({"FORTYPCD": 161})          # loblolly-shortleaf group
    assert is_pine({"ForTypName": "Loblolly pine"})
    assert not is_pine({"FORTYPCD": 500})      # oak-hickory
    assert not is_pine({"forest_type": "Oak-gum-cypress"})


def test_assign_regime_riparian_is_no_management():
    regime, _ = assign_regime({"OWN_CODE": 4, "SMZ_Pct": 80.0, "FORTYPCD": 161})
    assert regime == "no_management"


def test_assign_regime_public_owner_gets_selection():
    regime, params = assign_regime({"OWN_CODE": 6, "SMZ_Pct": 0})
    assert regime == "selection_harvest"
    assert params["interval"] == 10


def test_assign_regime_family_gets_light_thin():
    regime, _ = assign_regime({"OWN_CODE": 3, "SMZ_Pct": 5})
    assert regime == "thin_from_below"


def test_assign_regime_corporate_pine_vs_hardwood():
    pine, _ = assign_regime({"OWN_CODE": 4, "SMZ_Pct": 0, "FORTYPCD": 161})
    hardwood, _ = assign_regime({"OWN_CODE": 4, "SMZ_Pct": 0, "FORTYPCD": 500})
    assert pine == "plantation_rotation"
    assert hardwood == "clearcut"


def test_assign_regime_unknown_owner_defaults_to_thin():
    regime, _ = assign_regime({"SMZ_Pct": 0})
    assert regime == "thin_from_below"
