"""LETO's own pure-Python tests, run against our ports.

Upstream is aauslander480/Leto `LETO_v1_project/tests/` at the pin in
`research/leto_parity/README.md`. Each section below is one upstream test file, with the
test names and bodies copied verbatim; only the imports point at our modules. When
`scripts/leto_upstream_changes.py` reports a changed upstream test, re-copy it here. A test that
fails here is a divergence: fix our module or record it in the parity record.
"""

import pandas as pd

from pipeline.leto_donors import filter_weights
from pipeline.leto_imputation_qa import (
    build_imputation_diagnostics,
    build_imputation_summary,
    summarize_fia_donors,
)


# ---- LETO_v1_project/tests/test_weight_math.py (leto.donor_weights -> pipeline.leto_donors) ---


def test_filtered_weights_sum_to_one():
    frame = pd.DataFrame({
        "SEG_ID": [1, 1, 1, 2, 2],
        "STAND_ID": ["MU_1", "MU_1", "MU_1", "MU_2", "MU_2"],
        "PLT_CN": ["A", "B", "C", "D", "E"],
        "WEIGHT": [0.90, 0.06, 0.04, 0.03, 0.02],
    })
    result = filter_weights(frame, 0.05)
    sums = result.groupby("SEG_ID")["WEIGHT"].sum()
    assert all(abs(value - 1.0) < 1e-12 for value in sums)
    assert set(result.loc[result["SEG_ID"] == 2, "PLT_CN"]) == {"D"}


# ---- LETO_v1_project/tests/test_imputation_qa.py (leto.imputation_qa -> pipeline.leto_imputation_qa)


def test_fia_donor_audit_distinguishes_missing_from_no_live():
    trees = pd.DataFrame({
        "CN": ["1", "2", "3"],
        "PLT_CN": ["present_dead", "mapped", "unmapped"],
        "STATUSCD": [2, 1, 1],
        "SPCD": [101, 101, 999],
        "DIA": [10, 12, 8],
        "TPA_UNADJ": [1, 2, 3],
    })
    audit = summarize_fia_donors(trees, {"101": "AB"}, 1).set_index("PLT_CN")
    assert audit.loc["present_dead", "FIA_TREE_RECORDS_TOTAL"] == 1
    assert audit.loc["present_dead", "LIVE_TREE_RECORDS"] == 0
    assert audit.loc["mapped", "USABLE_TREE_RECORDS"] == 1
    assert audit.loc["unmapped", "UNMAPPED_SPECIES_RECORDS"] == 1
    assert "absent" not in audit.index


def test_diagnostics_report_missing_fia_and_removed_usable_donor():
    stands = pd.DataFrame({
        "MU_ID": [1], "SEG_ID": [1], "STAND_ID": ["MU_1"], "Acres": [10.0],
    })
    original = pd.DataFrame({
        "STAND_ID": ["MU_1", "MU_1", "MU_1"],
        "PLT_CN": ["missing", "dead", "dropped_live"],
        "WEIGHT": [0.50, 0.47, 0.03],
    })
    retained = pd.DataFrame({
        "STAND_ID": ["MU_1", "MU_1"],
        "PLT_CN": ["missing", "dead"],
        "WEIGHT": [0.5154639, 0.4845361],
        "WEIGHT_ORIGINAL": [0.50, 0.47],
    })
    audit = pd.DataFrame({
        "PLT_CN": ["dead", "dropped_live"],
        "FIA_TREE_RECORDS_TOTAL": [2, 1],
        "LIVE_TREE_RECORDS": [0, 1],
        "MAPPED_LIVE_TREE_RECORDS": [0, 1],
        "USABLE_TREE_RECORDS": [0, 1],
        "UNMAPPED_SPECIES_RECORDS": [0, 0],
        "INVALID_TREE_RECORDS": [0, 0],
    })
    final_trees = pd.DataFrame({
        "STAND_ID": ["MU_1"], "TREE_SOURCE": ["IMPUTED_NEAREST"],
        "DONOR_STAND_ID": ["MU_9"], "NEAR_DIST": [125.0],
    })
    diagnostics = build_imputation_diagnostics(
        stands, original, retained, audit, final_trees
    )
    row = diagnostics.iloc[0]
    assert row["FAILURE_REASON"] == "MISSING_FIA_DONOR"
    assert row["MISSING_PLT_CN"] == "missing"
    assert row["LIVE_TREE_RECORDS"] == 0
    assert row["WOULD_HAVE_USABLE_TREES_WITHOUT_5PCT_FILTER"]
    assert row["DROPPED_DONORS_WITH_FIA_LIVE_TREES"] == 1


def test_summary_uses_total_mu_acreage_denominator():
    stands = pd.DataFrame({
        "STAND_ID": ["MU_1", "MU_2"], "Acres": [10.0, 30.0],
    })
    diagnostics = pd.DataFrame({
        "MU_ID": [1], "STAND_ID": ["MU_1"], "Acres": [10.0],
        "FAILURE_REASON": ["NO_LIVE_TREES"], "NEAR_DIST": [250.0],
    })
    summary = build_imputation_summary(stands, diagnostics, "Meters")
    overall = summary.loc[summary["SECTION"] == "OVERALL"].iloc[0]
    assert overall["DIRECT_MUS"] == 1
    assert overall["IMPUTED_MUS"] == 1
    assert overall["PCT_ACRES_IMPUTED"] == 25.0
