"""Tests for the constrained harvest scheduler (pipeline/s3_management/harvest_scheduler.py)."""

from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.harvest_scheduler import (
    COUNTY,
    OWNER,
    TOTAL,
    allocate_cycle,
    schedule_harvests,
    summarize_schedule,
    to_cycle_budget,
)


def _units():
    # Four candidate units of equal removable volume (100 each), different ages/counties.
    return pd.DataFrame({
        "unit_id": ["a", "b", "c", "d"],
        "county": ["Baker", "Baker", "Union", "Union"],
        "owner_group": ["Private", "Private", "Public", "Private"],
        "stand_age": [80, 60, 90, 40],
        "removable_volume": [100.0, 100.0, 100.0, 100.0],
    })


def test_to_cycle_budget_multiplies_by_cycle_years():
    assert to_cycle_budget({"": 10.0}, cycle_years=5) == {"": 50.0}


def test_total_cap_fills_in_priority_order_oldest_first():
    # Total cycle budget = 250 cuft (annual 50 x 5). Only the 2 oldest+ (by age) fit... 250/100=2.5 -> 2 units.
    caps = {TOTAL: {"": 50.0}}
    out = allocate_cycle(_units(), caps, dims=[TOTAL], cycle_years=5)
    harvested = set(out[out["harvested"]]["unit_id"])
    # Oldest first: c(90), a(80) chosen; b(60), d(40) left (budget exhausted at 200<250 but 3rd would be 300>250).
    assert harvested == {"c", "a"}
    assert out["volume_removed"].sum() == pytest.approx(200.0)


def test_under_cap_harvests_everything():
    caps = {TOTAL: {"": 1000.0}}  # 5000 cuft/cycle, plenty
    out = allocate_cycle(_units(), caps, dims=[TOTAL], cycle_years=5)
    assert out["harvested"].all()


def test_county_cap_binds_independently():
    # Total is generous; Baker county capped to 100/cycle -> only 1 Baker unit; Union capped to 300 -> both.
    caps = {
        TOTAL: {"": 100000.0},
        COUNTY: {"Baker": 20.0, "Union": 60.0},  # x5 -> Baker 100, Union 300
    }
    out = allocate_cycle(_units(), caps, dims=[TOTAL, COUNTY], cycle_years=5)
    baker = out[(out["county"] == "Baker") & (out["harvested"])]
    union = out[(out["county"] == "Union") & (out["harvested"])]
    assert len(baker) == 1          # only the older Baker unit (a, age 80)
    assert set(baker["unit_id"]) == {"a"}
    assert len(union) == 2          # both Union units fit
    # The blocked Baker unit records the binding dimension.
    blocked = out[~out["harvested"]]
    assert (blocked["blocked_by"] == COUNTY).all()


def test_owner_group_dimension():
    caps = {OWNER: {"Private": 20.0, "Public": 0.0}}  # x5 -> Private 100, Public 0
    out = allocate_cycle(_units(), caps, dims=[OWNER], cycle_years=5)
    harvested = out[out["harvested"]]
    # Public unit (c) can't harvest; among Private, oldest (a, 80) fits 100, then b/d blocked.
    assert set(harvested["unit_id"]) == {"a"}
    assert (out[out["unit_id"] == "c"]["blocked_by"] == OWNER).all()


def test_missing_active_dimension_column_raises():
    units = _units().drop(columns=["county"])
    with pytest.raises(ValueError, match="county"):
        allocate_cycle(units, {TOTAL: {"": 1.0}, COUNTY: {"Baker": 1.0}}, dims=[TOTAL, COUNTY])


def test_missing_caps_for_active_dimension_raises():
    with pytest.raises(ValueError, match="no caps"):
        allocate_cycle(_units(), {TOTAL: {"": 1.0}}, dims=[TOTAL, COUNTY])


def test_schedule_harvests_runs_each_cycle():
    u = _units()
    a = u.assign(cycle=1)
    b = u.assign(cycle=2, removable_volume=50.0)
    units_by_cycle = pd.concat([a, b], ignore_index=True)
    caps = {TOTAL: {"": 50.0}}  # 250/cycle
    sched = schedule_harvests(units_by_cycle, caps, dims=[TOTAL], cycle_years=5)
    summ = summarize_schedule(sched)
    # Cycle 1: 2 units x100; Cycle 2: 250/50 = 5 -> all 4 units x50.
    assert summ[summ["cycle"] == 1]["units_harvested"].iloc[0] == 2
    assert summ[summ["cycle"] == 2]["units_harvested"].iloc[0] == 4
    assert summ[summ["cycle"] == 2]["volume_removed"].iloc[0] == pytest.approx(200.0)


def test_summarize_schedule_totals_only_harvested():
    out = allocate_cycle(_units(), {TOTAL: {"": 50.0}}, dims=[TOTAL]).assign(cycle=1)
    summ = summarize_schedule(out)
    assert summ["units_harvested"].iloc[0] == 2
    assert summ["volume_removed"].iloc[0] == pytest.approx(200.0)
