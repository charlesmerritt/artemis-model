"""Numerics of the 2026-08-31 simulated-annealing scheduler, on a synthetic landscape.

The driver lives in `weekly-artifact/2026-08-31/make_annealed_plan.py` and carries runtime
assertions that cover the real run, but those only fire when the whole pipeline is
executed with an FVS batch behind it. The piece worth protecting independently is
`Objective.delta_and_apply`: it maintains county and owner aggregates incrementally across
~10^5 proposals per temperature level, and the period-swap move applies, rejects and
reverses a delta in place against aggregates two stands share. That is exactly the kind of
arithmetic that stays silently wrong after an edit, and the README notes the driver is
headed for promotion into `pipeline/`.

Each test builds a tiny landscape by hand so the expected values are checkable by
inspection, and the central property is checked the only way that really settles it:
against a from-scratch `reset()` + `total()` recompute.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "weekly-artifact/2026-08-31/make_annealed_plan.py"

pytestmark = pytest.mark.skipif(not DRIVER.exists(), reason="2026-08-31 artifact not present")


def _load_driver():
    """Load the driver by path — `weekly-artifact/2026-08-31` is not an importable name."""
    spec = importlib.util.spec_from_file_location("wa_20260831_anneal", DRIVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load_driver()


# --------------------------------------------------------------------------------------
# A three-stand landscape: two counties, two owner groups, three cycles.
# --------------------------------------------------------------------------------------

N_CYCLES = 3


def _frames():
    stands = pd.DataFrame([
        # Baker / private_family, two options
        {"unit_id": "A", "tm_id": "1", "PLT_CN": "100", "county": "Baker",
         "owner_class": "private_family", "forest_branch": "pine", "acres": 10.0,
         "stand_age": 40.0, "unit_class": "managed", "OWN_CODE": 3, "FORTYPCD": 142},
        # Union / federal, two options
        {"unit_id": "B", "tm_id": "2", "PLT_CN": "200", "county": "Union",
         "owner_class": "federal", "forest_branch": "pine", "acres": 20.0,
         "stand_age": 60.0, "unit_class": "managed", "OWN_CODE": 6, "FORTYPCD": 142},
        # Baker / private_family, riparian: exactly one option
        {"unit_id": "C", "tm_id": "3", "PLT_CN": "300", "county": "Baker",
         "owner_class": "private_family", "forest_branch": "pine", "acres": 5.0,
         "stand_age": 30.0, "unit_class": "riparian", "OWN_CODE": 3, "FORTYPCD": 142},
    ])
    library = pd.DataFrame([
        {"unit_id": "A", "PLT_CN": "100", "prescription": "no_management"},
        {"unit_id": "A", "PLT_CN": "100", "prescription": "family_light_thin"},
        {"unit_id": "B", "PLT_CN": "200", "prescription": "no_management"},
        {"unit_id": "B", "PLT_CN": "200", "prescription": "family_light_thin"},
        {"unit_id": "C", "PLT_CN": "300", "prescription": "no_management"},
    ])
    rows = []
    # Per-acre removed volume and standing volume, by (plot, prescription, cycle).
    spec = {
        ("100", "no_management"): ([0.0, 0.0, 0.0], [10.0, 20.0, 30.0]),
        ("100", "family_light_thin"): ([5.0, 0.0, 7.0], [8.0, 14.0, 18.0]),
        ("200", "no_management"): ([0.0, 0.0, 0.0], [12.0, 24.0, 36.0]),
        ("200", "family_light_thin"): ([3.0, 4.0, 0.0], [9.0, 15.0, 22.0]),
        ("300", "no_management"): ([0.0, 0.0, 0.0], [11.0, 22.0, 33.0]),
    }
    for (plt, presc), (removed, standing) in spec.items():
        for c in range(1, N_CYCLES + 1):
            rows.append({"PLT_CN": plt, "prescription": presc, "cycle": c,
                         "calendar_year": 2022 + 5 * c,
                         "removed_merch_cuft_per_ac": removed[c - 1],
                         "MCuFt": standing[c - 1]})
    return stands, library, pd.DataFrame(rows)


def _caps():
    per_cycle = 1000.0
    return {
        m.hs.TOTAL: {"": per_cycle},
        m.hs.COUNTY: {"Baker": per_cycle, "Union": per_cycle},
        m.hs.OWNER: {"Private": per_cycle, "Federal (NF)": per_cycle,
                     "Other public": per_cycle},
    }


def _cfg(dimensions=("county", "owner_group")):
    return {
        "objectives": [{"metric": "harvest_volume", "weight": 6.0},
                       {"metric": "standing_volume", "weight": 1.0}],
        "dimensions": list(dimensions),
        "n_cycles": N_CYCLES,
    }


def _build(dimensions=("county", "owner_group")):
    stands, library, cycles = _frames()
    land = m.Landscape(stands, library, cycles, N_CYCLES)
    obj = m.Objective(land, _caps(), _cfg(dimensions))
    return land, obj


# --------------------------------------------------------------------------------------
# Landscape construction
# --------------------------------------------------------------------------------------

def test_landscape_shapes_and_volume_scaling():
    land, _ = _build()
    assert land.n == 3
    ix = {sid: i for i, sid in enumerate(land.stand_ids)}
    # Volumes are per-acre in the library and absolute on the landscape.
    a = ix["A"]
    k = land.options[a].index("family_light_thin")
    assert land.volumes[a][k] == [50.0, 0.0, 70.0]        # 5/0/7 per acre x 10 acres
    assert land.standing[a][k] == pytest.approx(180.0)     # ending 18 x 10
    # Only stands with more than one trajectory are proposable.
    assert sorted(land.stand_ids[i] for i in land.decision_stands) == ["A", "B"]


def test_riparian_no_entry_is_structural():
    land, _ = _build()
    report = land.verify_riparian_structural()
    assert report == {"riparian_stands": 1, "with_a_cutting_option": 0,
                      "structurally_enforced": True}


def test_option_without_a_trajectory_is_dropped_not_zero_filled():
    """A missing (plot, prescription) must shrink the menu, never read as zero harvest."""
    stands, library, cycles = _frames()
    library = pd.concat([library, pd.DataFrame([
        {"unit_id": "A", "PLT_CN": "100", "prescription": "hardwood_clearcut_regen"},
    ])], ignore_index=True)
    land = m.Landscape(stands, library, cycles, N_CYCLES)
    assert land.dropped_options == 1
    assert ("A", "hardwood_clearcut_regen") in land.dropped_option_keys
    a = land.stand_ids.index("A")
    assert "hardwood_clearcut_regen" not in land.options[a]


# --------------------------------------------------------------------------------------
# The incremental objective — the property that matters
# --------------------------------------------------------------------------------------

def _flat(rows):
    """pytest.approx does not accept nested sequences."""
    return [v for row in rows for v in row]


def _recompute(obj, land, choice):
    obj.reset(choice)
    return obj.total()


@pytest.mark.parametrize("dimensions", [("county", "owner_group"), ("county",),
                                        ("owner_group",)])
def test_delta_matches_full_recompute_for_every_single_stand_move(dimensions):
    """`delta_and_apply` must agree with reset()+total() for every reachable move."""
    land, obj = _build(dimensions)
    base = [0] * land.n
    for i in land.decision_stands:
        for new_k in range(len(land.options[i])):
            old_k = base[i]
            if new_k == old_k:
                continue
            before = _recompute(obj, land, base)
            predicted = obj.delta_and_apply(i, old_k, new_k, apply=False)

            after_choice = list(base)
            after_choice[i] = new_k
            actual = _recompute(obj, land, after_choice) - before
            assert predicted == pytest.approx(actual, rel=1e-12, abs=1e-12)


def test_apply_true_leaves_aggregates_consistent_with_a_fresh_reset():
    land, obj = _build()
    choice = [0] * land.n
    obj.reset(choice)
    i = land.decision_stands[0]
    obj.delta_and_apply(i, 0, 1, apply=True)
    choice[i] = 1
    running_county = _flat(obj.county_v)
    running_owner = _flat(obj.owner_v)
    running_standing = obj.standing_total

    obj.reset(choice)
    assert running_county == pytest.approx(_flat(obj.county_v))
    assert running_owner == pytest.approx(_flat(obj.owner_v))
    assert running_standing == pytest.approx(obj.standing_total)


def test_period_swap_apply_then_reverse_restores_state():
    """The swap move's apply -> reject -> reverse path must be exactly reversible."""
    land, obj = _build()
    choice = [0] * land.n
    obj.reset(choice)
    before = obj.total()
    county_before = _flat(obj.county_v)

    i = land.decision_stands[0]
    obj.delta_and_apply(i, 0, 1, apply=True)
    obj.delta_and_apply(i, 1, 0, apply=True)      # reverse, as anneal() does on reject

    assert obj.total() == pytest.approx(before, rel=1e-12, abs=1e-12)
    assert _flat(obj.county_v) == pytest.approx(county_before)


def test_disabled_dimension_is_not_scored():
    """§6: the objective must be exactly the one the scenario declares."""
    land_both, obj_both = _build(("county", "owner_group"))
    land_c, obj_c = _build(("county",))
    choice = [0] * land_both.n
    both = _recompute(obj_both, land_both, choice)
    county_only = _recompute(obj_c, land_c, choice)
    # Dropping the owner dimension drops its squared-deviation terms, so the volume part
    # of the objective must strictly decrease (targets are not met at this choice).
    assert county_only < both

    with pytest.raises(AssertionError):
        m.Objective(land_both, _caps(), _cfg(()))
    with pytest.raises(AssertionError):
        m.Objective(land_both, _caps(), _cfg(("not_a_dimension",)))


def test_relaxation_bound_is_a_lower_bound_on_attainable_objectives():
    """The declared bound must not exceed any objective the landscape can actually reach."""
    land, obj = _build()
    bound, strategy = obj.relaxation_bound()
    assert "interval relaxation" in strategy
    # Enumerate the whole (tiny) decision space.
    from itertools import product
    reachable = [
        _recompute(obj, land, list(combo))
        for combo in product(*[range(len(o)) for o in land.options])
    ]
    assert bound <= min(reachable) + 1e-9


def test_attainable_envelope_brackets_every_reachable_volume():
    land, obj = _build()
    env = obj.attainable_envelope()
    from itertools import product
    for combo in product(*[range(len(o)) for o in land.options]):
        obj.reset(list(combo))
        for keys, agg, name in ((land.counties, obj.county_v, "county"),
                                (land.owners, obj.owner_v, "owner_group")):
            for key, row in zip(keys, agg):
                for c, v in enumerate(row, start=1):
                    cell = env[(env.dimension == name) & (env.key == key)
                               & (env.cycle == c)].iloc[0]
                    assert cell.min_attainable_cuft - 1e-9 <= v
                    assert v <= cell.max_attainable_cuft + 1e-9


# --------------------------------------------------------------------------------------
# Reporting contracts
# --------------------------------------------------------------------------------------

def test_effective_move_probabilities_renormalise_over_the_available_moves():
    probs = m.effective_move_probabilities(
        {"single_stand": 0.70, "block": 0.20, "period_swap": 0.10})
    assert probs == {"single_stand": pytest.approx(0.875),
                     "period_swap": pytest.approx(0.125)}
    assert sum(probs.values()) == pytest.approx(1.0)
    with pytest.raises(AssertionError):
        m.effective_move_probabilities({"block": 1.0})


def test_violation_vector_covers_every_declared_dimension_and_cycle():
    land, obj = _build()
    v = obj.violation_vector([0] * land.n)
    assert len(v) == (len(land.counties) + len(land.owners)) * N_CYCLES
    assert set(v.dimension) == {"county", "owner_group"}
    # No harvest at all under the all-no_management choice: every target undershoots 100%.
    assert (v.deviation_pct == -100.0).all()


def test_spatial_penalties_report_unavailable_with_a_reason():
    land, _ = _build()
    available, reason = m.spatial_penalties_available(land)
    assert available is False
    assert "adjacency" in reason
