"""Nearest-neighbour establishment imputation for recovered TreeMap patches."""


import numpy as np
import pandas as pd
import pytest

from pipeline.s1_initial_state.impute_establishment import (
    donor_assignments,
    establishment_tree_lists,
    label_patches,
)


def test_label_patches_is_8_connected():
    mask = np.zeros((5, 5), dtype=bool)
    mask[0, 0] = True
    mask[1, 1] = True  # diagonal neighbour: same patch
    mask[3, 3] = True  # separate patch
    labels, count = label_patches(mask)
    assert count == 2
    assert labels[0, 0] == labels[1, 1]
    assert labels[3, 3] != labels[0, 0]


def test_donor_is_the_modal_neighbour_tm_id():
    # A 2x2 patch; the ring around it holds three pixels of plot 12 and one of plot 7.
    donors = np.zeros((7, 7), dtype=np.int32)
    donors[0, 0:3] = 12
    donors[0, 3] = 7
    donors[1, 0] = 12
    patch = np.zeros((7, 7), dtype=bool)
    patch[2:4, 2:4] = True
    labels, count = label_patches(patch)
    assignments, unresolved = donor_assignments(donors, labels)
    assert count == 1
    assert unresolved == []
    assert assignments.loc[1, "donor_tm_id"] == "12"


def test_tie_break_prefers_the_lowest_tm_id():
    donors = np.zeros((7, 7), dtype=np.int32)
    donors[0, 0:2] = 20
    donors[0, 2:4] = 9
    patch = np.zeros((7, 7), dtype=bool)
    patch[2:4, 2:4] = True
    labels, _ = label_patches(patch)
    assignments, _ = donor_assignments(donors, labels)
    assert assignments.loc[1, "donor_tm_id"] == "9"


def test_donor_ring_never_takes_a_hole_pixel_or_another_patch():
    # Pixels adjacent to the patch are TreeMap holes (0); the only donors sit
    # one pixel further out. The ring must expand past the holes.
    donors = np.zeros((9, 9), dtype=np.int32)
    donors[0, 3:6] = 33
    patch = np.zeros((9, 9), dtype=bool)
    patch[3:6, 3:6] = True
    patch[3, 2] = True  # second patch pixel directly beside a donor column
    patch[2, 2] = True  # diagonal arm, so the hole band is thicker
    labels, count = label_patches(patch)
    assignments, unresolved = donor_assignments(donors, labels)
    assert count == 1
    assert unresolved == []
    assert assignments.loc[1, "donor_tm_id"] == "33"


def test_unresolvable_patch_is_reported_not_assigned():
    donors = np.zeros((9, 9), dtype=np.int32)  # no donors anywhere
    patch = np.zeros((9, 9), dtype=bool)
    patch[3:6, 3:6] = True
    labels, _ = label_patches(patch)
    assignments, unresolved = donor_assignments(donors, labels, ring_radii=(3,))
    assert unresolved == [1]
    assert pd.isna(assignments.loc[1, "donor_tm_id"])


def test_assignment_carries_patch_geometry_and_stratum():
    donors = np.zeros((9, 9), dtype=np.int32)
    donors[0, 3:6] = 55
    patch = np.zeros((9, 9), dtype=bool)
    patch[3:6, 3:6] = True
    strata = np.zeros((9, 9), dtype=np.uint8)
    strata[patch] = 1  # S1
    labels, _ = label_patches(patch)
    assignments, unresolved = donor_assignments(donors, labels, strata=strata)
    row = assignments.loc[1]
    assert row["pixels"] == 9
    assert row["acres"] == pytest.approx(9 * 0.2224)
    assert row["stratum"] == 1
    # S1 was cut before the 2016 vintage: establishment window closes at 2016.
    assert row["est_year_low"] is None and row["est_year_high"] == 2016


def test_establishment_tree_lists_use_the_donor_plot_exactly():
    tree_table = pd.DataFrame(
        {
            "TM_ID": ["55", "55", "12"],
            "PLT_CN": ["17498047010478", "17498047010478", "236048879010661"],
            "SP": ["131", "111", "131"],
            "TPA": [50.0, 30.0, 120.0],
        }
    )
    assignments = pd.DataFrame(
        {"patch_id": [1], "donor_tm_id": ["55"], "pixels": [9], "acres": [2.0016], "stratum": [1]}
    )
    lists = establishment_tree_lists(assignments, tree_table)
    # The donor's own rows, unmodified — the establishment list *is* the donor's list.
    assert lists[lists.patch_id == 1]["SP"].tolist() == ["131", "111"]
    assert lists[lists.patch_id == 1]["PLT_CN"].tolist() == ["17498047010478", "17498047010478"]
    # IDs stayed strings end to end: no 1.7e+13 renderings anywhere.
    assert lists["PLT_CN"].dtype == object or pd.api.types.is_string_dtype(lists["PLT_CN"])


def test_large_plt_cn_survives_intact():
    tree_table = pd.DataFrame(
        {"TM_ID": ["55"], "PLT_CN": ["123456789012345678"], "SP": ["131"], "TPA": [50.0]}
    )
    assignments = pd.DataFrame(
        {"patch_id": [1], "donor_tm_id": ["55"], "pixels": [4], "acres": [0.8896], "stratum": [2]}
    )
    lists = establishment_tree_lists(assignments, tree_table)
    assert lists.loc[0, "PLT_CN"] == "123456789012345678"


# ── scaled nearest-neighbour establishment ──────────────────────────────────────────────

from pipeline.s1_initial_state.impute_establishment import (  # noqa: E402
    EstablishmentDensity as D,
    EstablishmentMode as M,
    EstablishmentPolicy,
    establishment_effect,
    live_stand_metrics,
    scaled_establishment_lists,
)
from pipeline.s1_initial_state.young_stand_profiles import (  # noqa: E402
    BA_FACTOR,
    ForestTypeGroup as G,
    YoungStandProfile,
)

BIG_CN = "1234567890123456789"   # a 19-digit PLT_CN that a float would round


def _policy(density=D.FOREST_TYPE_PROFILE, modes=None) -> EstablishmentPolicy:
    slash = YoungStandProfile(tpa=600.0, dbh_in=2.5, ht_ft=12.0, n_plots=400,
                              profile_from=G.LONGLEAF_SLASH)
    return EstablishmentPolicy(
        target_age=5, density=density,
        stratum_modes=modes or {1: M.SCALED_YOUNG, 2: M.DONOR_AS_IS, 3: M.SCALED_YOUNG, 4: M.SCALED_YOUNG},
        profiles={G.LONGLEAF_SLASH: slash})


def _donor_table() -> pd.DataFrame:
    # Mature slash pine donor 55: 90 TPA slash (111) and 30 TPA sweetgum (611) alive, one dead tree.
    return pd.DataFrame({
        "TM_ID": ["55", "55", "55", "55"],
        "PLT_CN": [BIG_CN] * 4,
        "STATUSCD": [1, 1, 1, 2],
        "TPA_UNADJ": [60.0, 30.0, 30.0, 6.0],
        "SPCD": [111, 111, 611, 111],
        "DIA": [10.0, 12.0, 8.0, 9.0],
        "HT": [70.0, 75.0, 50.0, 60.0],
        "CR": [40, 35, 30, 0],
    })


FORTYPES = pd.Series({"55": 142})


def test_stratum_modes_follow_config_and_unlisted_strata_are_scaled():
    policy = _policy()
    assert policy.mode_for(1) is M.SCALED_YOUNG
    assert policy.mode_for(2) is M.DONOR_AS_IS
    assert policy.mode_for(0) is M.SCALED_YOUNG   # no stratum: no evidence of standing forest


def test_committed_policy_scales_s1_s3_s4_and_keeps_s2_as_is():
    from pipeline.s1_initial_state.impute_establishment import load_establishment_policy

    policy = load_establishment_policy()
    assert [policy.mode_for(s) for s in (1, 2, 3, 4)] == [
        M.SCALED_YOUNG, M.DONOR_AS_IS, M.SCALED_YOUNG, M.SCALED_YOUNG]
    assert policy.target_age == 5
    assert policy.density is D.FOREST_TYPE_PROFILE


def test_scaled_list_takes_species_shares_from_the_donor_and_density_and_size_from_the_profile():
    keys = pd.DataFrame({"TM_ID": ["55"], "establishment_mode": [M.SCALED_YOUNG]})
    lists = scaled_establishment_lists(keys, _donor_table(), FORTYPES, _policy())
    assert lists["SPCD"].tolist() == [111, 611]                       # one row per live species
    assert lists["TPA_UNADJ"].tolist() == pytest.approx([600 * 90 / 120, 600 * 30 / 120])
    assert lists["DIA"].tolist() == [2.5, 2.5]
    assert lists["HT"].tolist() == [12.0, 12.0]
    assert (lists["STATUSCD"] == 1).all()
    assert (lists["establishment_mode"] == "scaled_young").all()
    assert (lists["establishment_age"] == 5).all()
    assert (lists["forest_type_group"] == "longleaf_slash").all()
    assert lists["PLT_CN"].tolist() == [BIG_CN, BIG_CN]                # the donor's, digit for digit


def test_donor_density_keeps_the_donor_tpa_but_still_resets_size():
    keys = pd.DataFrame({"TM_ID": ["55"], "establishment_mode": [M.SCALED_YOUNG]})
    lists = scaled_establishment_lists(keys, _donor_table(), FORTYPES, _policy(density=D.DONOR))
    assert lists["TPA_UNADJ"].tolist() == pytest.approx([90.0, 30.0])
    assert lists["DIA"].tolist() == [2.5, 2.5]


def test_donor_as_is_rows_are_the_verbatim_tree_rows():
    keys = pd.DataFrame({"TM_ID": ["55"], "establishment_mode": [M.DONOR_AS_IS]})
    lists = scaled_establishment_lists(keys, _donor_table(), FORTYPES, _policy())
    assert len(lists) == 4                                             # dead tree included, as published
    assert lists["DIA"].tolist() == [10.0, 12.0, 8.0, 9.0]
    assert (lists["establishment_mode"] == "donor_as_is").all()
    assert lists["establishment_age"].isna().all()


def test_one_donor_can_seed_both_modes_and_each_is_keyed_separately():
    keys = pd.DataFrame({"TM_ID": ["55", "55"], "establishment_mode": [M.SCALED_YOUNG, M.DONOR_AS_IS]})
    lists = scaled_establishment_lists(keys, _donor_table(), FORTYPES, _policy())
    assert lists.groupby("establishment_mode").size().to_dict() == {"donor_as_is": 4, "scaled_young": 2}


def test_the_donor_forest_type_group_picks_the_profile():
    policy = _policy()
    policy.profiles[G.OAK_GUM_CYPRESS] = YoungStandProfile(900.0, 1.8, 10.0, 5, G.HARDWOOD)
    keys = pd.DataFrame({"TM_ID": ["55"], "establishment_mode": [M.SCALED_YOUNG]})
    lists = scaled_establishment_lists(keys, _donor_table(), pd.Series({"55": 608}), policy)
    assert lists["TPA_UNADJ"].sum() == pytest.approx(900.0)
    assert (lists["forest_type_group"] == "oak_gum_cypress").all()


def test_live_stand_metrics_count_only_live_trees():
    m = live_stand_metrics(_donor_table(), keys=["TM_ID"])
    assert m.loc["55", "live_tpa"] == pytest.approx(120.0)
    ba = BA_FACTOR * (60 * 100 + 30 * 144 + 30 * 64)
    assert m.loc["55", "live_ba_ft2_per_ac"] == pytest.approx(ba)


def test_establishment_effect_compares_mature_donors_with_what_is_established():
    keys = pd.DataFrame({"TM_ID": ["55", "55"], "establishment_mode": [M.SCALED_YOUNG, M.DONOR_AS_IS]})
    lists = scaled_establishment_lists(keys, _donor_table(), FORTYPES, _policy())
    acres = pd.DataFrame({"TM_ID": ["55", "55", "55"],
                          "establishment_mode": ["scaled_young", "scaled_young", "donor_as_is"],
                          "acres": [10.0, 30.0, 5.0]})
    effect = establishment_effect(acres, _donor_table(), lists)
    mature_ba = BA_FACTOR * (60 * 100 + 30 * 144 + 30 * 64)
    young_ba = BA_FACTOR * 600 * 2.5**2
    young = effect["by_mode"]["scaled_young"]
    assert young["acres"] == pytest.approx(40.0)
    assert young["mature_donor"]["live_ba_ft2"] == pytest.approx(40 * mature_ba, rel=1e-3)
    assert young["established"]["live_ba_ft2"] == pytest.approx(40 * young_ba, rel=1e-3)
    assert young["established"]["live_tpa"] == pytest.approx(600.0)
    as_is = effect["by_mode"]["donor_as_is"]
    assert as_is["established"]["live_ba_ft2"] == pytest.approx(as_is["mature_donor"]["live_ba_ft2"])
    total = effect["total"]
    assert total["acres"] == pytest.approx(45.0)
    assert total["established"]["live_ba_ft2"] == pytest.approx(40 * young_ba + 5 * mature_ba, rel=1e-3)
