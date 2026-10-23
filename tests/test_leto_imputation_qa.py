"""Imputation QA pinned to LETO v1 `leto/imputation_qa.py` (aauslander480/Leto @ d2b92ee)."""

import pandas as pd
import pytest

from pipeline import leto_imputation_qa as qa
from pipeline.leto_inventory import TreeSource

LOOKUP = {"131": "LP"}


def _fia():
    return pd.DataFrame({
        "PLT_CN": ["P1", "P1", "P2", "P3"],
        "STATUSCD": ["1", "2", "2", "1"],
        "SPCD": ["131", "131", "131", "999"],
        "DIA": ["8", "8", "8", "8"],
        "TPA_UNADJ": ["6", "6", "6", "6"],
    })


def test_donor_audit_counts_raw_live_mapped_and_usable_records():
    audit = qa.summarize_fia_donors(_fia(), LOOKUP, 1).set_index("PLT_CN")
    assert audit.loc["P1", ["FIA_TREE_RECORDS_TOTAL", "LIVE_TREE_RECORDS",
                            "USABLE_TREE_RECORDS"]].tolist() == [2, 1, 1]
    assert audit.loc["P2", "LIVE_TREE_RECORDS"] == 0
    assert audit.loc["P3", "UNMAPPED_SPECIES_RECORDS"] == 1


def _case():
    stands = pd.DataFrame({"STAND_ID": ["MU_1", "MU_2", "MU_3", "MU_4"], "SEG_ID": [1, 2, 3, 4],
                           "MU_ID": ["1", "2", "3", "4"], "Acres": [10.0, 2.0, 3.0, 5.0]})
    original = pd.DataFrame({"STAND_ID": ["MU_1", "MU_2", "MU_2", "MU_3", "MU_4"],
                             "PLT_CN": ["P1", "P2", "P1", "P3", "P9"],
                             "WEIGHT": [1.0, 0.97, 0.03, 1.0, 1.0]})
    retained = original[original["WEIGHT"] >= 0.05].copy()
    retained["WEIGHT_ORIGINAL"] = retained["WEIGHT"]
    trees = pd.DataFrame({
        "STAND_ID": ["MU_1", "MU_2", "MU_3", "MU_4"],
        "TREE_SOURCE": [TreeSource.FIA_WEIGHTED_DIRECT] + [TreeSource.IMPUTED_NEAREST] * 3,
        "DONOR_STAND_ID": ["", "MU_1", "MU_1", "MU_1"],
        "NEAR_DIST": [float("nan"), 0.0, 150.0, 900.0]})
    return stands, original, retained, trees


def test_diagnostics_give_each_imputed_stand_its_failure_reason():
    stands, original, retained, trees = _case()
    audit = qa.summarize_fia_donors(_fia(), LOOKUP, 1)
    diag = qa.build_imputation_diagnostics(stands, original, retained, audit, trees)
    diag = diag.set_index("STAND_ID")
    assert diag.loc["MU_2", "FAILURE_REASON"] == "NO_LIVE_TREES"
    assert bool(diag.loc["MU_2", "WOULD_HAVE_USABLE_TREES_WITHOUT_5PCT_FILTER"])
    assert diag.loc["MU_3", "FAILURE_REASON"] == "SPECIES_MAPPING_FAILURE"
    assert diag.loc["MU_4", "FAILURE_REASON"] == "MISSING_FIA_DONOR"
    assert diag.loc["MU_4", "DONOR_STAND_ID"] == "MU_1"


def test_summary_reconciles_direct_and_imputed_counts_and_acres():
    stands, original, retained, trees = _case()
    audit = qa.summarize_fia_donors(_fia(), LOOKUP, 1)
    diag = qa.build_imputation_diagnostics(stands, original, retained, audit, trees)
    summary = qa.build_imputation_summary(stands, diag, "Meters")
    overall = summary[summary["SECTION"] == "OVERALL"].iloc[0]
    assert (overall["DIRECT_MUS"], overall["IMPUTED_MUS"]) == (1, 3)
    assert overall["PCT_ACRES_IMPUTED"] == pytest.approx(50.0)
    bins = summary[summary["SECTION"] == "DISTANCE_BIN"].set_index("DISTANCE_BIN")["MU_COUNT"]
    assert (bins["0-100"], bins["100-250"], bins["500-1000"]) == (1, 1, 1)
