"""Nearest-neighbour establishment imputation for recovered TreeMap patches."""

from pathlib import Path

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
