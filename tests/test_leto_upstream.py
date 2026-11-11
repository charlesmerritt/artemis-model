"""LETO's own pure-Python tests, run against our ports.

Upstream is aauslander480/Leto `LETO_v1_project/tests/` at the pin in
`research/leto_parity/README.md`. Each section below is one upstream test file, with the
test names and bodies copied verbatim; only the imports point at our modules. When
`scripts/leto_upstream_changes.py` reports a changed upstream test, re-copy it here. A test that
fails here is a divergence: fix our module or record it in the parity record.
"""

import pandas as pd
import pytest

from pipeline.leto_donors import filter_weights
from pipeline.leto_management_units import (
    assign_final_mu_ids,
    summarize_chain_run,
    summarize_mu_chain,
    validate_final_units,
)
from pipeline.leto_small_stands import (
    aggregate_management_unit_weights,
    plan_proximity_merges,
)
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


# ---- LETO_v1_project/tests/test_small_stands.py (leto.small_stands -> pipeline.leto_small_stands)


def test_proximity_merge_groups_without_changing_segment_ids():
    units = pd.DataFrame(
        {
            "SEG_ID": [10, 20, 30],
            "Acres": [3.0, 8.0, 20.0],
            "OWN_CODE": [4, 4, 4],
            "FORTYPCD_DOM": [101, 101, 101],
            "STDAGE_MEAN": [42.0, 43.0, 80.0],
        }
    )
    candidates = pd.DataFrame(
        {
            "SOURCE_SEG_ID": [10, 10],
            "TARGET_SEG_ID": [20, 30],
            "DISTANCE_METERS": [55.0, 10.0],
        }
    )
    mapping, merges, qa = plan_proximity_merges(
        units,
        candidates,
        minimum_acres=5.0,
        maximum_distance_meters=60.0,
        maximum_combined_acres=300.0,
        require_same_ownership=True,
        require_same_forest_type=True,
        retain_when_no_candidate=True,
    )

    assert set(mapping["SEG_ID"]) == {10, 20, 30}
    assert mapping.set_index("SEG_ID").loc[10, "MU_ID"] == 10
    assert mapping.set_index("SEG_ID").loc[20, "MU_ID"] == 10
    assert merges.iloc[0]["TARGET_SEG_ID"] == 20
    assert qa["proximity_merges"] == 1
    assert qa["total_acres_before"] == qa["total_acres_after"] == 31.0


def test_proximity_merge_respects_ownership_and_forest_type():
    units = pd.DataFrame(
        {
            "SEG_ID": [1, 2, 3],
            "Acres": [2.0, 10.0, 10.0],
            "OWN_CODE": [1, 2, 1],
            "FORTYPCD_DOM": [100, 100, 200],
            "STDAGE_MEAN": [20.0, 20.0, 20.0],
        }
    )
    candidates = pd.DataFrame(
        {
            "SOURCE_SEG_ID": [1, 1],
            "TARGET_SEG_ID": [2, 3],
            "DISTANCE_METERS": [5.0, 5.0],
        }
    )
    mapping, merges, qa = plan_proximity_merges(
        units,
        candidates,
        minimum_acres=5.0,
        maximum_distance_meters=60.0,
        maximum_combined_acres=300.0,
        require_same_ownership=True,
        require_same_forest_type=True,
        retain_when_no_candidate=True,
    )

    assert merges.empty
    assert mapping.set_index("SEG_ID").loc[1, "MU_ID"] == 1
    assert qa["small_stands_remaining"] == 1


def test_area_weighted_donor_aggregation_matches_example():
    parent_weights = pd.DataFrame(
        {
            "SEG_ID": [1, 1, 2],
            "PLT_CN": ["Plot1", "Plot2", "Plot3"],
            "WEIGHT": [0.70, 0.30, 1.00],
        }
    )
    components = pd.DataFrame(
        {
            "SEG_ID": [1, 2],
            "MU_ID": [1, 1],
            "Acres": [10.0, 5.0],
        }
    )

    result = aggregate_management_unit_weights(parent_weights, components)
    weights = result.set_index("PLT_CN")["WEIGHT"]

    assert abs(weights["Plot1"] - 0.4666666666666667) < 1e-12
    assert abs(weights["Plot2"] - 0.2) < 1e-12
    assert abs(weights["Plot3"] - 0.3333333333333333) < 1e-12
    assert abs(result["WEIGHT"].sum() - 1.0) < 1e-12
    assert result["DONOR_ACRES"].sum() == 15.0


def test_repeated_plot_donors_are_consolidated_across_segments():
    parent_weights = pd.DataFrame(
        {
            "SEG_ID": [1, 1, 2, 2],
            "PLT_CN": ["shared", "one", "shared", "two"],
            "WEIGHT": [0.5, 0.5, 0.25, 0.75],
        }
    )
    components = pd.DataFrame(
        {"SEG_ID": [1, 2], "MU_ID": [99, 99], "Acres": [4.0, 6.0]}
    )

    result = aggregate_management_unit_weights(parent_weights, components)
    weights = result.set_index("PLT_CN")["WEIGHT"]

    assert len(result) == 3
    assert abs(weights["shared"] - 0.35) < 1e-12
    assert abs(weights["one"] - 0.20) < 1e-12
    assert abs(weights["two"] - 0.45) < 1e-12
    assert abs(result["WEIGHT"].sum() - 1.0) < 1e-12


# ---- LETO_v1_project/tests/test_management_units.py (leto.management_units ->
# pipeline.leto_management_units)


COMPATIBILITY = ["MGMT_CLASS", "MGMT_TYPE", "DB_GROUP", "OWN_CODE", "RIPARIAN", "WETLAND"]


def _components():
    return pd.DataFrame({
        "SEG_ID": [10, 20, 30],
        "BIO_MU_ID": [10, 10, 30],
        "MU_ID": [10, 10, 30],
        "Acres": [2.0, 3.0, 8.0],
        "MGMT_CLASS": ["forest", "forest", "forest"],
        "MGMT_TYPE": ["upland", "upland", "upland"],
        "DB_GROUP": ["A", "A", "A"],
        "OWN_CODE": [1, 1, 1],
        "RIPARIAN": [0, 0, 0],
        "WETLAND": [0, 0, 0],
        "SOURCE_OID": [9, 3, 2],
    })


def test_final_mu_identity_preserves_bio_group_and_segment_ids():
    result = assign_final_mu_ids(
        _components(), compatibility_fields=COMPATIBILITY, proximity_enabled=True
    )
    assert result["SEG_ID"].tolist() == [10, 20, 30]
    assert result.loc[result["SEG_ID"].isin([10, 20]), "MU_ID"].nunique() == 1
    assert result.set_index("SEG_ID").loc[10, "MU_PART"] == 1
    assert result.set_index("SEG_ID").loc[20, "MU_PART"] == 2


def test_final_mu_splits_incompatible_management_classes():
    components = _components()
    components.loc[components["SEG_ID"] == 20, ["MGMT_CLASS", "RIPARIAN"]] = ["riparian", 1]
    result = assign_final_mu_ids(
        components, compatibility_fields=COMPATIBILITY, proximity_enabled=True
    )
    assert result.loc[result["BIO_MU_ID"] == 10, "MU_ID"].nunique() == 2
    assert result.groupby("MU_ID")["MGMT_CLASS"].nunique().max() == 1


def test_disabled_path_preserves_existing_mu_ids():
    components = _components()
    components["BIO_MU_ID"] = components["SEG_ID"]
    components["MU_ID"] = [101, 102, 103]
    result = assign_final_mu_ids(
        components, compatibility_fields=COMPATIBILITY, proximity_enabled=False
    )
    assert result["MU_ID"].tolist() == [101, 102, 103]


def test_chain_span_flags_transitive_group():
    components = _components().iloc[:3].copy()
    components["MU_ID"] = 10
    pairs = [
        {"boundary_distance_m": 30.0, "centroid_distance_m": 80.0},
        {"boundary_distance_m": 60.0, "centroid_distance_m": 100.0},
        {"boundary_distance_m": 120.0, "centroid_distance_m": 160.0},
    ]
    qa = summarize_mu_chain(
        components, pairs, maximum_distance_meters=60.0, warning_chain_ratio=1.5
    )
    assert qa["MU_SPAN_M"] == 120.0
    assert qa["CHAIN_FLAG"] is True
    assert qa["CHAIN_RATIO"] == 2.0
    assert qa["SOURCE_SEG_COUNT"] == 3
    run = summarize_chain_run(pd.DataFrame([qa]))
    assert run["chain_flagged_units"] == 1


def test_dissolved_unit_validation_accepts_one_row_for_many_components():
    components = _components().iloc[:2]
    units = pd.DataFrame({"MU_ID": [10], "Acres": [5.0]})
    validate_final_units(
        units, components, compatibility_fields=COMPATIBILITY
    )


def test_dissolved_unit_validation_rejects_duplicate_mu_rows():
    components = _components().iloc[:2]
    units = pd.DataFrame({"MU_ID": [10, 10], "Acres": [2.0, 3.0]})
    with pytest.raises(RuntimeError, match="one row per MU_ID"):
        validate_final_units(units, components, compatibility_fields=COMPATIBILITY)
