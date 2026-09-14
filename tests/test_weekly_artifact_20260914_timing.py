"""The timing-offset expansion of the 2026-09-14 trajectory library.

`weekly-artifact/2026-09-14/make_timing_library.py` adds the `when` axis §4 of
`notes/trajectory-library-and-annealing.md` specifies: every cutting prescription is
re-emitted at offsets of 0, 5, 10 and 15 years. The driver's own end-to-end check is
strong — all 3,000-odd offset-0 runs must reproduce 2026-08-31's published volumes — but it
only fires with a 13,000-run FVS batch behind it, and it cannot see the cases that matter
most at the edges of the horizon: an entry pushed past it, a variant that loses every entry,
and a regeneration record whose harvest is gone.

Those are what this file pins, on operations small enough to check by inspection.

The properties, stated as the driver states them:

* an offset moves entry **years** and nothing else — the same proportions, DBH windows and
  regeneration rule, so a variant is genuinely the same prescription started later;
* offset 0 is the identity, which is what makes the week-on-week comparison meaningful;
* an entry past the last schedulable year is dropped, and a variant that loses all of them
  collapses to `no_management` and is not published as a duplicate option;
* regeneration follows its harvest, so a stand is never re-initialized from a planting list
  for a clearcut that was dropped;
* `no_management` gains no variants, which is what keeps riparian libraries exactly
  `{no_management}` and §3 rule 2 structurally enforced after an expansion that multiplied
  every other menu by four.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
LIBRARY_DRIVER = REPO / "weekly-artifact/2026-09-14/make_timing_library.py"
PLAN_DRIVER = REPO / "weekly-artifact/2026-09-14/make_annealed_plan.py"

pytestmark = pytest.mark.skipif(not LIBRARY_DRIVER.exists(),
                                reason="2026-09-14 artifact not present")


def _load(path: Path, name: str):
    """Load a driver by path — `weekly-artifact/2026-09-14` is not an importable name."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: a module-level dataclass resolves its own __module__
    # through sys.modules, and fails outright when the entry is missing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load(LIBRARY_DRIVER, "wa_20260914_timing")


# --------------------------------------------------------------------------------------
# The grid itself
# --------------------------------------------------------------------------------------

def test_offsets_are_whole_cycles_starting_at_zero():
    """Every offset must be a cycle multiple, or entries stop landing on cycle boundaries.

    FVS resolves an activity year onto its cycle, so an offset of 7 years would silently
    snap two different variants onto the same cycle and the library would carry duplicate
    options that look distinct. Offset 0 must be present for the base library to survive the
    expansion at all.
    """
    assert m.OFFSETS[0] == 0
    assert all(o % m.CYCLE_YEARS == 0 for o in m.OFFSETS)
    assert len(set(m.OFFSETS)) == len(m.OFFSETS)
    assert m.LAST_ENTRY_YEAR == m.INV_YEAR + m.HORIZON_YEARS
    # The carrier cycle: one past the scored horizon, so a final-year entry executes.
    assert m.NUM_CYCLE == m.N_OBJECTIVE_CYCLES + 1


def test_variant_id_round_trips():
    assert m.variant_id("family_light_thin", 10) == "family_light_thin@+10"
    assert m.base_of("family_light_thin@+10") == ("family_light_thin", 10)
    assert m.base_of("family_light_thin@+0") == ("family_light_thin", 0)
    # An unoffset id is `no_management`, the one prescription with no timing to vary.
    assert m.base_of("no_management") == ("no_management", 0)


# --------------------------------------------------------------------------------------
# An offset moves years, and only years
# --------------------------------------------------------------------------------------

def test_offset_shifts_every_entry_and_changes_nothing_else():
    params = {"start_year": 2032, "end_year": 2062, "interval": 10, "proportion": 0.2}
    base = m.shift_variant("selection_harvest", params, "public_selection_light", 0)
    moved = m.shift_variant("selection_harvest", params, "public_selection_light", 10)

    assert base.entry_years == (2032, 2042, 2052, 2062)
    assert moved.entry_years == (2042, 2052, 2062, 2072)
    assert moved.dropped_entries == 0
    # Same operations, ten years later: proportion, DBH window and species untouched.
    for before, after in zip(base.thins, moved.thins):
        assert after.year == before.year + 10
        assert (after.proportion, after.min_dbh, after.max_dbh, after.species) == \
               (before.proportion, before.min_dbh, before.max_dbh, before.species)


def test_offset_zero_is_the_identity():
    """The claim the week-on-week comparison rests on, at the operation level."""
    for template, params in [
        ("thin_from_below", {"year": 2032, "max_dbh": 8.0, "proportion": 0.35}),
        ("clearcut", {"year": 2042}),
        ("plantation_rotation", {"thin_year": 2027, "clearcut_year": 2047,
                                 "thin_proportion": 0.35, "thin_max_dbh": 9.0}),
        ("thin_from_below_repeated", {"start_year": 2027, "end_year": 2057, "interval": 15,
                                      "proportion": 0.3, "max_dbh": 10.0}),
    ]:
        v = m.shift_variant(template, params, "p", 0)
        assert list(v.thins) == m.build_thins(template, params)
        assert list(v.regen) == m.build_regeneration(template, params)
        assert v.dropped_entries == 0


# --------------------------------------------------------------------------------------
# The horizon edge
# --------------------------------------------------------------------------------------

def test_entry_past_the_horizon_is_dropped_and_counted():
    """`config/management_regimes.yaml`: a later entry "is noise in the keyfile and a lie
    in the schedule". The rest of the prescription still runs."""
    params = {"start_year": 2037, "end_year": 2067, "interval": 15, "proportion": 0.15}
    v = m.shift_variant("selection_harvest", params, "family_uneven_aged_selection", 10)
    assert v.entry_years == (2047, 2062)          # 2077 dropped
    assert v.dropped_entries == 1
    assert all(t.year <= m.LAST_ENTRY_YEAR for t in v.thins)


def test_entry_in_the_final_year_is_kept():
    """2072 is schedulable — that is the whole point of the carrier cycle."""
    v = m.shift_variant("clearcut", {"year": 2067}, "hardwood_clearcut_regen", 5)
    assert v.entry_years == (m.LAST_ENTRY_YEAR,)
    assert v.dropped_entries == 0


def test_variant_with_no_surviving_entry_collapses():
    """Not published as an option: it would duplicate the `no_management` already in the
    menu, inflating the library with a trajectory it already carries."""
    for params, offset in (({"year": 2067}, 10), ({"year": 2072}, 5)):
        v = m.shift_variant("clearcut", params, "hardwood_clearcut_regen", offset)
        assert v.collapsed
        assert v.entry_years == ()
        assert v.thins == ()


def test_collapsed_variant_still_reports_what_it_dropped():
    """A collapsed row that claims it dropped nothing is a report contradicting itself:
    dropping every entry is *why* it collapsed, and `library_expansion.csv` says so."""
    v = m.shift_variant("clearcut", {"year": 2067}, "hardwood_clearcut_regen", 10)
    assert v.collapsed
    assert v.dropped_entries == 1

    params = {"start_year": 2062, "end_year": 2072, "interval": 5, "proportion": 0.2}
    multi = m.shift_variant("selection_harvest", params, "public_selection_light", 15)
    assert multi.collapsed
    assert multi.dropped_entries == 3       # 2077, 2082, 2087 — all three past the horizon

    expanded, acct = m.expand_library(_library_frame())
    collapsed = acct[acct["collapsed_to_no_management"]]
    assert len(collapsed) == 2
    assert (collapsed["entries_dropped"] > 0).all()
    assert (collapsed["entries_kept"] == 0).all()


def test_collapsed_variant_carries_no_regeneration():
    """No harvest inside the horizon means nothing to regenerate from."""
    v = m.shift_variant("clearcut", {"year": 2067, "regen": "plant"},
                        "hardwood_clearcut_regen", 10)
    assert v.collapsed
    assert v.regen == ()


def test_delayed_rotation_keeps_the_thin_when_the_clearcut_falls_out():
    """A plantation rotation delayed past its final harvest is a thin inside this horizon,
    and that is what the trajectory reports — not a rotation, and not nothing."""
    params = {"thin_year": 2042, "clearcut_year": 2062, "thin_proportion": 0.35,
              "thin_max_dbh": 9.0}
    v = m.shift_variant("plantation_rotation", params, "pine_plantation_long_rotation", 15)
    assert v.entry_years == (2057,)               # thin only; clearcut 2077 is gone
    assert v.dropped_entries == 1
    assert v.thins[0].proportion == 0.35          # still the thin, not a clearcut
    assert v.thins[0].max_dbh == 9.0


# --------------------------------------------------------------------------------------
# Regeneration follows its harvest
# --------------------------------------------------------------------------------------

def test_regeneration_moves_with_its_harvest():
    params = {"year": 2042, "regen": "plant"}
    base = m.shift_variant("clearcut", params, "hardwood_clearcut_regen", 0)
    moved = m.shift_variant("clearcut", params, "hardwood_clearcut_regen", 10)
    assert [r.year for r in moved.regen] == [r.year + 10 for r in base.regen]
    assert [r.species for r in moved.regen] == [r.species for r in base.regen]
    assert [r.trees_per_acre for r in moved.regen] == [r.trees_per_acre for r in base.regen]


def test_regeneration_is_dropped_with_the_harvest_that_created_it():
    """Otherwise a stand is re-initialized from a planting list for a clearcut that the
    horizon rule removed — a stand that regenerates without ever having been cut."""
    params = {"thin_year": 2042, "clearcut_year": 2062, "thin_proportion": 0.35,
              "thin_max_dbh": 9.0}
    v = m.shift_variant("plantation_rotation", params, "pine_plantation_long_rotation", 15)
    assert v.entry_years == (2057,)
    assert v.regen == ()          # the 2062 clearcut is gone, so its planting record is too

    # A surviving final-year harvest keeps its regeneration, which lands in the carrier cycle.
    kept = m.shift_variant("clearcut", {"year": 2067, "regen": "plant"},
                           "hardwood_clearcut_regen", 5)
    assert kept.entry_years == (2072,)
    assert [r.year for r in kept.regen] == [2073]
    assert all(r.year <= m.LAST_ENTRY_YEAR + m.CYCLE_YEARS for r in kept.regen)


def test_regeneration_parent_is_resolved_before_the_shift():
    """The record's parent is the entry that *created* it, not whichever survivor happens to
    precede it afterwards.

    Every template carrying regeneration today has a single stand-replacing entry, so
    "nearest preceding survivor" would agree on this week's data. It stops agreeing the
    moment a template has two and the later one — the real parent — is dropped: an earlier,
    unrelated entry would then adopt the record, and the stand would be re-initialized from
    a planting list for a harvest that never happened. The pairing is therefore done on the
    unshifted schedule, where the delta is exactly `delay_years`.
    """
    Regeneration, ThinDBH = m.Regeneration, m.ThinDBH
    thins = [ThinDBH(year=2032, proportion=0.3, max_dbh=9.0),   # a thin, not the parent
             ThinDBH(year=2062, proportion=1.0)]                # the clearcut that regenerates
    regen = [Regeneration(year=2063, species="LP", trees_per_acre=500.0, natural=False)]

    # +15 pushes the clearcut to 2077, outside the horizon; the 2032 thin survives at 2047.
    v = m._shift_operations(thins, regen, offset=15)
    assert [t.year for t in v[0]] == [2047]
    assert v[1] == [], "the surviving thin must not adopt the dropped clearcut's regeneration"

    # +5 keeps both, so the record moves with its parent.
    thins_kept, regen_kept = m._shift_operations(thins, regen, offset=5)
    assert [t.year for t in thins_kept] == [2037, 2067]
    assert [r.year for r in regen_kept] == [2068]


def test_with_regen_false_skips_the_records():
    """`expand_library` needs entry years only, and building regeneration without a
    `stand_sdi` table warns that the Diaz composition rule was skipped — a real warning that
    would be noise, and misleading, on records that are then discarded."""
    v = m.shift_variant("clearcut", {"year": 2042}, "hardwood_clearcut_regen", 5,
                        with_regen=False)
    assert v.regen == ()
    assert v.entry_years == (2047,)


# --------------------------------------------------------------------------------------
# The expansion over a library frame
# --------------------------------------------------------------------------------------

def _library_frame() -> pd.DataFrame:
    """Two stands: one upland with a two-prescription menu, one riparian with no entry."""
    common = {"tm_id": "1", "county": "Baker", "owner_class": "private_family",
              "forest_branch": "pine", "acres": 10.0, "stand_age": 40.0}
    return pd.DataFrame([
        {"unit_id": "U1", "PLT_CN": "100", "prescription": "no_management",
         "template": "no_management", "unit_class": "managed", "params": None, **common},
        {"unit_id": "U1", "PLT_CN": "100", "prescription": "family_light_thin",
         "template": "thin_from_below", "unit_class": "managed",
         "params": "max_dbh=8.0;proportion=0.35;year=2032", **common},
        {"unit_id": "U1", "PLT_CN": "100", "prescription": "hardwood_clearcut_regen",
         "template": "clearcut", "unit_class": "managed", "params": "year=2067", **common},
        {"unit_id": "R1", "PLT_CN": "200", "prescription": "no_management",
         "template": "no_management", "unit_class": "riparian", "params": None, **common},
    ])


def test_expansion_multiplies_cutting_menus_and_leaves_riparian_alone():
    expanded, acct = m.expand_library(_library_frame())

    rip = expanded[expanded["unit_class"] == "riparian"]
    assert list(rip["prescription"]) == ["no_management"], \
        "riparian libraries must stay exactly {no_management}: §3 rule 2 is enforced by the " \
        "absence of an alternative, not by a constraint the search could violate"

    u1 = expanded[expanded["unit_id"] == "U1"]
    # no_management once, the thin at all four offsets, and the 2067 clearcut at the two
    # offsets that keep it inside the horizon (2067, 2072).
    assert sorted(u1["prescription"]) == sorted([
        "no_management",
        "family_light_thin@+0", "family_light_thin@+5",
        "family_light_thin@+10", "family_light_thin@+15",
        "hardwood_clearcut_regen@+0", "hardwood_clearcut_regen@+5",
    ])
    assert (u1["base_prescription"].map(lambda b: b in {
        "no_management", "family_light_thin", "hardwood_clearcut_regen"})).all()

    collapsed = acct[acct["collapsed_to_no_management"]]
    assert set(zip(collapsed["base_prescription"], collapsed["offset_years"])) == {
        ("hardwood_clearcut_regen", 10), ("hardwood_clearcut_regen", 15)}


def test_expansion_preserves_stand_attributes():
    """The landscape does not change this week; only the decision space does."""
    lib = _library_frame()
    expanded, _ = m.expand_library(lib)
    for col in ("unit_id", "PLT_CN", "county", "owner_class", "acres", "unit_class"):
        assert set(expanded[col].dropna()) == set(lib[col].dropna())
    assert expanded.groupby("unit_id")["acres"].nunique().eq(1).all()


def test_run_ledger_reconciles_or_refuses():
    """A run that produced no rows and was never recorded as a failure is in neither frame,
    so nothing downstream can notice its option has left the decision space."""
    rendered = {"a::p@+0", "b::p@+0", "c::p@+0"}

    # The good case: a clean partition.
    m.reconcile_run_ledger(rendered, {"a::p@+0", "b::p@+0"}, {"c::p@+0"})
    m.reconcile_run_ledger(rendered, rendered, set())

    # Vanished without a trace — the failure this check exists for.
    with pytest.raises(SystemExit, match="does not reconcile"):
        m.reconcile_run_ledger(rendered, {"a::p@+0", "b::p@+0"}, set())

    # Counted on both sides, which would make the exclusion count meaningless.
    with pytest.raises(SystemExit, match="does not reconcile"):
        m.reconcile_run_ledger(rendered, rendered, {"c::p@+0"})


def test_parse_params_round_trips_the_20260817_form():
    assert m.parse_params("max_dbh=8.0;proportion=0.35;year=2032") == {
        "max_dbh": 8.0, "proportion": 0.35, "year": 2032}
    assert m.parse_params(None) == {}
    assert m.parse_params("") == {}
    assert isinstance(m.parse_params("year=2032")["year"], int)


# --------------------------------------------------------------------------------------
# The scheduler's side of the variant contract
# --------------------------------------------------------------------------------------

plan = _load(PLAN_DRIVER, "wa_20260914_anneal")


def test_greedy_baseline_takes_the_deterministic_timing():
    """`assign_prescription` resolves entry years from stand age and the config's fixed
    offsets, which is the `@+0` variant. The greedy baseline must plan over the same
    trajectories it planned over last week, or the comparison measures two algorithms."""
    options = ["no_management", "family_light_thin@+0", "family_light_thin@+10"]
    assert plan.default_variant("family_light_thin", options) == "family_light_thin@+0"
    assert plan.default_variant("no_management", options) == "no_management"
    # A default whose deterministic timing collapsed out of the horizon has no variant here.
    assert plan.default_variant("hardwood_clearcut_regen", options) is None


def test_plan_driver_decodes_variants_the_same_way():
    """Two files, one convention: a plan whose offsets decoded differently from the library
    that produced it would mislabel every row."""
    for presc, expected in [("family_light_thin@+15", ("family_light_thin", 15)),
                            ("no_management", ("no_management", 0))]:
        assert plan.base_of(presc) == expected == m.base_of(presc)
    assert plan.OFFSET_SEP == m.OFFSET_SEP
