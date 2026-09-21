"""Tests for the five-county FIA volume estimates (PER-21 gate).

The network-dependent query is injected, so these run offline; the real
EVALIDator call happens in the module's CLI, mirroring
``verify_fia_evalidator.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state import fia_volume_estimates as mod


def _canned_output(numerator: str, value: float, se_pct: float, plots: int) -> dict:
    return {
        "numeratorName": numerator,
        "row": [{"column": [{
            "content": "Total", "cellValueNumerator": value, "cellSE": se_pct,
            "cellPlotNumerator": plots,
        }]}],
    }


def _fake_query_factory(calls: list):
    def fake_query(params: dict) -> dict:
        calls.append(params)
        return _canned_output("volume", 1_000_000.0, 5.0, 120)
    return fake_query


def test_query_uses_county_domain_filter():
    calls: list = []
    mod.five_county_estimates(query=_fake_query_factory(calls), fetch_plan=False)
    wf = calls[0]["wf"]
    assert "PLOT.COUNTYCD IN (3, 23, 47, 121, 125)" == wf


def test_query_hits_the_florida_2022_evaluation_group():
    calls: list = []
    mod.five_county_estimates(query=_fake_query_factory(calls), fetch_plan=False)
    assert calls[0]["wc"] == mod.EVAL_GRP


def test_estimates_carry_all_reported_attributes():
    calls: list = []
    result = mod.five_county_estimates(query=_fake_query_factory(calls), fetch_plan=False)
    assert set(result) == set(mod.ATTRIBUTE_SNUMS.values()) | {"fetch_params_note"}
    row = result["growing_stock_inventory_forestland_cuft"]
    assert row["snum"] == 15
    assert row["estimate"] == 1_000_000.0
    assert row["se_pct"] == 5.0
    assert row["se"] == 50_000.0
    assert row["plots"] == 120


def test_every_snum_requested_with_the_same_stocking_dimensions():
    calls: list = []
    mod.five_county_estimates(query=_fake_query_factory(calls), fetch_plan=False)
    for call in calls:
        assert call["rselected"] == "All live stocking"
        assert call["cselected"] == "All live stocking"
        assert call["outputFormat"] == "JSON"


def test_plan_harvest_annualizes_each_cycle_over_five_years():
    plan_csv = (
        "cycle,cuft,calendar_year,target_cuft,deviation_pct\n"
        "1,100.0,2027,300.0,-66.7\n"
        "2,200.0,2032,300.0,-33.3\n"
    )
    rows = mod.plan_harvest_by_cycle(_csv=plan_csv)
    assert rows == [
        {"cycle": 1, "calendar_year": 2027, "cycle_cuft": 100.0, "annual_cuft": 20.0},
        {"cycle": 2, "calendar_year": 2032, "cycle_cuft": 200.0, "annual_cuft": 40.0},
    ]


def test_removal_comparison_frames_the_even_flow_gate():
    result = {
        "removals_timberland_cuft_per_year": {"estimate": 50_000_000.0, "se_pct": 8.0,
                                              "se": 4_000_000.0, "plots": 99},
        "net_growth_timberland_cuft_per_year": {"estimate": 80_000_000.0, "se_pct": 6.0,
                                                "se": 4_800_000.0, "plots": 99},
    }
    plan = [{"cycle": 1, "calendar_year": 2027, "cycle_cuft": 225_000_000.0,
             "annual_cuft": 45_000_000.0}]
    verdict = mod.removal_comparison(result, plan)
    assert verdict["plan_annual_cuft"] == 45_000_000.0
    assert verdict["fia_removals_cuft_per_year"] == 50_000_000.0
    assert verdict["plan_over_fia_removals"] == 0.9
    assert verdict["plan_within_fia_removals_ci"] is True
    assert verdict["fia_growth_over_plan"] == pytest.approx(80_000_000.0 / 45_000_000.0)


def test_removal_comparison_flags_out_of_band_plan():
    result = {
        "removals_timberland_cuft_per_year": {"estimate": 50_000_000.0, "se_pct": 2.0,
                                              "se": 1_000_000.0, "plots": 99},
        "net_growth_timberland_cuft_per_year": {"estimate": 60_000_000.0, "se_pct": 2.0,
                                                "se": 1_200_000.0, "plots": 99},
    }
    plan = [{"cycle": 1, "calendar_year": 2027, "cycle_cuft": 500_000_000.0,
             "annual_cuft": 100_000_000.0}]
    verdict = mod.removal_comparison(result, plan)
    assert verdict["plan_within_fia_removals_ci"] is False
    assert verdict["plan_exceeds_growth"] is True
