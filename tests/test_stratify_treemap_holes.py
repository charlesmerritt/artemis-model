"""Unit tests for the TreeMap hole stratification logic (no /mnt/d access needed)."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_MOD_PATH = Path(__file__).resolve().parents[1] / "pipeline/s1_initial_state/stratify_treemap_holes.py"
_spec = importlib.util.spec_from_file_location("stratify_treemap_holes", _MOD_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
STRATA, fia_tree, summarize = _mod.STRATA, _mod.fia_tree, _mod.summarize


def test_fia_tree_excludes_urban_and_developed_tree_classes():
    names = np.array([
        "Southeastern North American Temperate Forest Plantation",
        "Eastern Warm Temperate Urban Evergreen Forest",
        "Eastern Warm Temperate Developed Evergreen Forest",
        "Eastern Warm Temperate Orchard",
        "Southeastern Ruderal Grassland",
    ], dtype=object)
    lifeforms = np.array(["Tree", "Tree", "Tree", "Tree", "Herb"], dtype=object)
    assert fia_tree(names, lifeforms).tolist() == [True, False, False, False, False]


def test_fia_tree_requires_tree_lifeform():
    names = np.array(["Recently Logged-Herb and Grass Cover"], dtype=object)
    assert fia_tree(names, np.array(["Herb"], dtype=object)).tolist() == [False]


@pytest.mark.parametrize("code,label", sorted(STRATA.items()))
def test_summary_covers_every_stratum(code, label):
    strata = np.array([[1, 2, 3], [4, 5, 0]], dtype=np.uint8)
    table = summarize(strata)
    row = table[table.code == code].iloc[0]
    assert row.stratum == label
    assert row.pixels == 1


def test_summary_fractions_exclude_nodata_and_sum_to_one():
    strata = np.array([[1, 1, 2], [3, 4, 5], [0, 0, 0]], dtype=np.uint8)
    table = summarize(strata)
    assert table.pixels.sum() == 6  # the three zeros are outside the hole universe
    assert table.frac_of_holes.sum() == pytest.approx(1.0)
    assert table.loc[table.code == 1, "acres"].item() == pytest.approx(2 * 0.2224)
