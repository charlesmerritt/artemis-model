"""Young-plot matching for add-back pixels in the FL5 harvest-scheduling deck experiment.

ADR 0003 (branch add-back-stand-age): an add-back pixel takes a real young FIA plot of its
neighbour plot's forest type at the pixel's stand age, widening +-1, +-2, then the forest
type group. Until the raster correction dates each cut, the deck draws the age per patch
uniformly from 1-10.
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

MODULE = Path(__file__).resolve().parents[1] / "experiments/2026-10-05_fl5-harvest-deck/young_plots.py"
spec = importlib.util.spec_from_file_location("young_plots", MODULE)
yp = importlib.util.module_from_spec(spec)
sys.modules["young_plots"] = yp  # dataclasses resolve their module by name
spec.loader.exec_module(yp)


@pytest.fixture
def pool():
    return pd.DataFrame({
        "PLT_CN": ["900000000000000001", "900000000000000002", "900000000000000003",
                   "900000000000000004", "900000000000000005"],
        "FORTYPCD": [161, 161, 162, 406, 161],
        "TYPGRPCD": [160, 160, 160, 400, 160],
        "STDAGE": [5, 7, 4, 3, 5],
    })


def test_exact_type_and_age_wins_and_ties_break_on_plt_cn_string(pool):
    match = yp.match_young_plot(161, 5, pool)
    assert match.plt_cn == "900000000000000001"
    assert match.rung is yp.MatchRung.TYPE_EXACT_AGE


def test_widens_to_one_year_then_two(pool):
    assert yp.match_young_plot(161, 6, pool).rung is yp.MatchRung.TYPE_AGE_PM1
    two = yp.match_young_plot(161, 9, pool)
    assert (two.plt_cn, two.rung) == ("900000000000000002", yp.MatchRung.TYPE_AGE_PM2)


def test_falls_back_to_forest_type_group(pool):
    match = yp.match_young_plot(163, 4, pool, type_group=160)
    assert (match.plt_cn, match.rung) == ("900000000000000003", yp.MatchRung.GROUP_EXACT_AGE)


def test_last_rung_is_nearest_age_in_group_and_reports_the_gap(pool):
    match = yp.match_young_plot(406, 10, pool, type_group=400)
    assert (match.plt_cn, match.rung, match.age_gap) == (
        "900000000000000004", yp.MatchRung.GROUP_NEAREST_AGE, 7)


def test_no_plot_in_group_is_unmatched_not_guessed(pool):
    match = yp.match_young_plot(503, 5, pool, type_group=500)
    assert match.rung is yp.MatchRung.UNMATCHED and match.plt_cn is None


def test_patch_ages_are_one_per_connected_patch_within_one_to_ten():
    mask = np.zeros((5, 7), dtype=bool)
    mask[0:2, 0:2] = True   # patch A
    mask[3:5, 4:7] = True   # patch B
    ages = yp.patch_ages(mask, seed=1)
    assert (ages[~mask] == 0).all()
    assert len(np.unique(ages[0:2, 0:2])) == 1 and len(np.unique(ages[3:5, 4:7])) == 1
    assert ages[mask].min() >= 1 and ages[mask].max() <= 10
    assert np.array_equal(ages, yp.patch_ages(mask, seed=1))
