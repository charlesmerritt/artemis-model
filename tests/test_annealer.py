"""The simulated-annealing trajectory selector, on landscapes small enough to solve by hand."""

import pandas as pd
import pytest

from pipeline.s3_management import annealer as A
from pipeline.s3_management import harvest_scheduler as hs

PERIOD = "2013_2024"


def _toy(county="Baker"):
    """Two corporate stands in one county, each choosing between growing and one cut.

    Stand `a` cuts 100 cuft/ac in cycle 1; stand `b` cuts 100 cuft/ac in cycle 2. With one acre
    each and a 100 cuft target per cycle, the only zero-deviation plan cuts both.
    """
    stands = pd.DataFrame({
        "unit_id": ["a", "b"], "PLT_CN": ["1234567890123456789", "2"], "county": [county] * 2,
        "owner_class": ["corporate"] * 2, "acres": [1.0, 1.0], "unit_class": ["upland"] * 2,
        "OWN_CODE": [4, 4], "FORTYPCD": [161, 161], "stand_age": [30, 30],
    })
    library = pd.DataFrame({"unit_id": ["a", "a", "b", "b"],
                            "prescription": ["no_management", "cut", "no_management", "cut"]})
    rows = []
    for plt, cut_cycle in [("1234567890123456789", 1), ("2", 2)]:
        for presc in ["no_management", "cut"]:
            for cycle in (1, 2):
                removed = 100.0 if presc == "cut" and cycle == cut_cycle else 0.0
                rows.append({"PLT_CN": plt, "prescription": presc, "cycle": cycle,
                             "removed_merch_cuft_per_ac": removed, "MCuFt": 1000.0})
    return stands, library, pd.DataFrame(rows)


def _caps(target=100.0, county="Baker"):
    return {hs.TOTAL: {"": target}, hs.COUNTY: {county: target}, hs.OWNER: {"Private": target}}


def _cfg():
    cfg = A.load_config()
    cfg["objectives"] = [{"metric": "harvest_volume", "weight": 1.0}]
    cfg["dimensions"] = [hs.COUNTY, hs.OWNER]
    return cfg


def test_tpo_caps_reads_every_county_and_the_all_counties_total_per_cycle():
    caps = A.tpo_caps(PERIOD)

    assert set(caps[hs.COUNTY]) == {"Baker", "Columbia", "Hamilton", "Suwanee", "Union"}
    assert caps[hs.TOTAL] == {"": pytest.approx(77505800.0 * A.CYCLE_YEARS)}
    assert caps[hs.OWNER]["Private"] > 0 and "All owners" not in caps[hs.OWNER]


def test_tpo_county_applies_the_workbook_spelling():
    assert A.tpo_county("Suwannee") == "Suwanee"
    assert A.tpo_county("Baker") == "Baker"


def test_landscape_scales_per_acre_volumes_and_keeps_ids_exact():
    stands, library, cycles = _toy()
    land = A.Landscape(stands, library, cycles, n_cycles=2)

    assert land.stand_ids == ["a", "b"]
    assert land.options == [["no_management", "cut"], ["no_management", "cut"]]
    assert land.volumes[0] == [[0.0, 0.0], [100.0, 0.0]]
    assert land.decision_stands == [0, 1]


def test_landscape_drops_a_stand_whose_county_has_no_target():
    stands, library, cycles = _toy(county="Alachua")
    land = A.Landscape(stands, library, cycles, n_cycles=2, counties={"Baker"})

    assert land.n == 0 and land.dropped_no_trajectory == 2


def test_anneal_finds_the_zero_deviation_plan_and_is_deterministic_by_seed():
    stands, library, cycles = _toy()
    land = A.Landscape(stands, library, cycles, n_cycles=2)
    cfg = _cfg()
    obj = A.Objective(land, _caps(), cfg)

    first = A.anneal(land, obj, cfg, seed=7, initial=[0, 0])
    again = A.anneal(land, A.Objective(land, _caps(), cfg), cfg, seed=7, initial=[0, 0])

    assert first["choice"] == [1, 1]
    assert first["objective"] == pytest.approx(0.0)
    assert again["choice"] == first["choice"] and again["proposed"] == first["proposed"]


def test_plan_frame_reports_the_selected_trajectory_per_stand():
    stands, library, cycles = _toy()
    land = A.Landscape(stands, library, cycles, n_cycles=2)

    plan = A.plan_frame(land, [1, 0], stands)

    assert plan["prescription"].tolist() == ["cut", "no_management"]
    assert plan["PLT_CN"].tolist() == ["1234567890123456789", "2"]
    assert plan["owner_group"].tolist() == ["Private", "Private"]
    assert plan["cuft_cycle_1"].tolist() == [100.0, 0.0]
