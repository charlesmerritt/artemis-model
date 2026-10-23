"""The simulated-annealing trajectory selector, on landscapes small enough to solve by hand."""

import pandas as pd
import pytest

from pipeline.s3_management import annealer as A
from pipeline.s3_management import harvest_scheduler as hs

PERIOD = "2013_2024"
# The corporate pine default, so the greedy seed takes it where the caps allow.
CUT = "pine_plantation_short_rotation"


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
                            "prescription": ["no_management", CUT, "no_management", CUT]})
    rows = []
    for plt, cut_cycle in [("1234567890123456789", 1), ("2", 2)]:
        for presc in ["no_management", CUT]:
            for cycle in (1, 2):
                removed = 100.0 if presc == CUT and cycle == cut_cycle else 0.0
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
    assert land.options == [["no_management", CUT], ["no_management", CUT]]
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

    assert plan["prescription"].tolist() == [CUT, "no_management"]
    assert plan["PLT_CN"].tolist() == ["1234567890123456789", "2"]
    assert plan["owner_group"].tolist() == ["Private", "Private"]
    assert plan["cuft_cycle_1"].tolist() == [100.0, 0.0]


def _library_and_cycles():
    """`_toy` in the trajectory library's shape: options carry a run hash, cycles are per run."""
    stands, library, cycles = _toy()
    run_of = {(p, presc): f"{p[-1]}-{presc}" for p, presc in
              cycles[["PLT_CN", "prescription"]].drop_duplicates().itertuples(index=False)}
    plt_of_unit = dict(zip(stands["unit_id"], stands["PLT_CN"]))
    library = library.assign(PLT_CN=library["unit_id"].map(plt_of_unit))
    library["run_hash"] = [run_of[(p, presc)] for p, presc in
                           library[["PLT_CN", "prescription"]].itertuples(index=False)]
    cycles = cycles.assign(run_hash=[run_of[(p, presc)] for p, presc in
                                     cycles[["PLT_CN", "prescription"]].itertuples(index=False)])
    return stands, library, cycles.drop(columns="prescription")


def test_build_landscape_joins_options_to_their_runs():
    stands, library, cycles = _library_and_cycles()
    cfg = _cfg()
    cfg["n_cycles"] = 2

    land, obj, greedy = A.build_landscape(stands, library, cycles, cfg, _caps())

    assert land.options == [["no_management", CUT], ["no_management", CUT]]
    assert land.volumes[1] == [[0.0, 0.0], [0.0, 100.0]]
    assert len(greedy) == 2


def test_build_landscape_refuses_a_stand_that_lost_every_option():
    stands, library, cycles = _library_and_cycles()
    cfg = _cfg()
    cfg["n_cycles"] = 2

    with pytest.raises(AssertionError, match="lost every option"):
        A.build_landscape(stands, library, cycles[cycles["PLT_CN"] != "2"], cfg, _caps())


def test_search_keeps_the_best_restart():
    stands, library, cycles = _library_and_cycles()
    cfg = _cfg()
    cfg["n_cycles"] = 2
    land, obj, greedy = A.build_landscape(stands, library, cycles, cfg, _caps())

    best, results = A.search(land, obj, cfg, greedy, seeds=[1, 2, 3], workers=2)

    assert [r["seed"] for r in results] == [1, 2, 3]
    assert best["objective"] == min(r["objective"] for r in results) == pytest.approx(0.0)
