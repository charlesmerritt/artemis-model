"""Age-5 stand profiles by forest type group, measured from FIA young conditions."""

import math

import pandas as pd
import pytest

from pipeline.s1_initial_state.young_stand_profiles import (
    ForestTypeGroup as G,
    YoungStandProfile,
    forest_type_group,
    load_young_stand_profiles,
    young_stand_profiles,
)


@pytest.mark.parametrize("fortypcd, group", [
    (141, G.LONGLEAF_SLASH), (142, G.LONGLEAF_SLASH),
    (161, G.LOBLOLLY_SHORTLEAF), (167, G.LOBLOLLY_SHORTLEAF),
    (403, G.OAK_PINE), (409, G.OAK_PINE),
    (602, G.OAK_GUM_CYPRESS), (608, G.OAK_GUM_CYPRESS),
    (503, G.OTHER_HARDWOOD), (520, G.OTHER_HARDWOOD), (704, G.OTHER_HARDWOOD), (998, G.OTHER_HARDWOOD),
    (171, G.OTHER), (999, G.OTHER), (121, G.OTHER),
])
def test_forest_type_group_follows_the_fia_type_ranges(fortypcd, group):
    assert forest_type_group(fortypcd) is group


def test_a_missing_forest_type_is_other():
    assert forest_type_group(None) is G.OTHER
    assert forest_type_group(float("nan")) is G.OTHER


def _plots(group_codes: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame({"PLT_CN": list(group_codes), "FORTYPCD": list(group_codes.values()),
                         "STDORGCD": [1] * len(group_codes)})


def test_profile_is_median_plot_tpa_with_tpa_weighted_size():
    conditions = _plots({"100000000000001": 142, "100000000000002": 142, "100000000000003": 142})
    trees = pd.DataFrame({
        "PLT_CN": ["100000000000001", "100000000000001", "100000000000002", "100000000000003"],
        "TPA_UNADJ": [300.0, 100.0, 600.0, 800.0],
        "DIA": [2.0, 4.0, 2.0, 3.0],
        "HT": [10.0, 20.0, 12.0, 14.0],
    })
    seedlings = pd.DataFrame({"PLT_CN": ["100000000000002"], "TPA_UNADJ": [75.0]})
    out = young_stand_profiles(conditions, trees, seedlings, min_plots=1)
    row = out.loc[G.LONGLEAF_SLASH]
    assert row["n_plots"] == 3
    assert row["tpa"] == pytest.approx(600.0)            # plot TPAs 400, 600, 800
    # Quadratic mean diameter over every live tree, weighted by TPA: TPA x DIA reproduces BA.
    qmd = math.sqrt((300 * 4 + 100 * 16 + 600 * 4 + 800 * 9) / 1800)
    assert row["dbh_in"] == pytest.approx(qmd)
    assert row["ht_ft"] == pytest.approx((300 * 10 + 100 * 20 + 600 * 12 + 800 * 14) / 1800)
    assert row["seedling_tpa"] == pytest.approx(0.0)     # plots 0, 75, 0 -> median 0
    assert row["profile_from"] == G.LONGLEAF_SLASH


def test_a_plot_with_no_live_trees_counts_as_zero_tpa_not_missing():
    conditions = _plots({"1": 142, "2": 142, "3": 142})
    trees = pd.DataFrame({"PLT_CN": ["1"], "TPA_UNADJ": [500.0], "DIA": [2.0], "HT": [10.0]})
    out = young_stand_profiles(conditions, trees, pd.DataFrame(columns=["PLT_CN", "TPA_UNADJ"]),
                               min_plots=1)
    assert out.loc[G.LONGLEAF_SLASH, "n_plots"] == 3
    assert out.loc[G.LONGLEAF_SLASH, "tpa"] == pytest.approx(0.0)   # 500, 0, 0


def test_a_thin_group_falls_back_to_its_parent_then_to_all_plots():
    # Ten slash pine plots, two loblolly: loblolly borrows the softwood pool. One redcedar
    # (other) plot borrows the all-plot pool.
    codes = {f"{i}": 142 for i in range(10)} | {"20": 161, "21": 161, "30": 171}
    conditions = _plots(codes)
    trees = pd.DataFrame({"PLT_CN": list(codes), "TPA_UNADJ": [400.0] * 10 + [900.0, 900.0, 50.0],
                          "DIA": [2.0] * 13, "HT": [10.0] * 13})
    out = young_stand_profiles(conditions, trees, pd.DataFrame(columns=["PLT_CN", "TPA_UNADJ"]),
                               min_plots=10)
    assert out.loc[G.LOBLOLLY_SHORTLEAF, "n_plots"] == 2              # its own count, for the record
    assert out.loc[G.LOBLOLLY_SHORTLEAF, "profile_from"] == G.SOFTWOOD
    assert out.loc[G.LOBLOLLY_SHORTLEAF, "tpa"] == out.loc[G.SOFTWOOD, "tpa"]
    assert out.loc[G.SOFTWOOD, "n_plots"] == 12
    assert out.loc[G.OTHER, "profile_from"] == G.ALL
    assert out.loc[G.ALL, "n_plots"] == 13
    assert out.loc[G.LONGLEAF_SLASH, "profile_from"] == G.LONGLEAF_SLASH


def test_trees_join_to_their_plot_by_exact_plt_cn_string():
    # Two 19-digit control numbers that collide as floats must stay two plots.
    a, b = "1234567890123456789", "1234567890123456788"
    conditions = _plots({a: 142, b: 142})
    trees = pd.DataFrame({"PLT_CN": [a, b], "TPA_UNADJ": [100.0, 300.0],
                          "DIA": [2.0, 2.0], "HT": [10.0, 10.0]})
    out = young_stand_profiles(conditions, trees, pd.DataFrame(columns=["PLT_CN", "TPA_UNADJ"]),
                               min_plots=1)
    assert out.loc[G.LONGLEAF_SLASH, "tpa"] == pytest.approx(200.0)


def test_committed_profiles_cover_every_forest_type_group():
    profiles = load_young_stand_profiles()
    leaves = {g for g in G if g not in (G.SOFTWOOD, G.HARDWOOD, G.ALL)}
    assert leaves <= set(profiles)
    for p in profiles.values():
        assert isinstance(p, YoungStandProfile)
        assert p.tpa > 0 and p.dbh_in > 0 and p.ht_ft > 0
