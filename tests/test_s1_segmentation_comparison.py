from importlib import import_module
from pathlib import Path
import sys
from types import SimpleNamespace

import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _comparison_module():
    return import_module("pipeline.s1_initial_state.segmentation.comparison")


def test_compare_segmentations_reports_coverage_overlap_and_sizes():
    reference = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"], "Acres": [0.000247, 0.000247]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:5070",
    )
    candidate = gpd.GeoDataFrame(
        {
            "MU_ID": ["a", "b", "c"],
            "Acres": [0.000247, 0.0001235, 0.0001235],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 1.5, 1), box(1.5, 0, 2, 1)],
        crs="EPSG:5070",
    )

    metrics = _comparison_module().compare_segmentations(
        reference,
        candidate,
        reference_name="arcpy_leto",
        candidate_name="python_leto",
    )

    assert metrics["reference_name"] == "arcpy_leto"
    assert metrics["candidate_name"] == "python_leto"
    assert metrics["reference_unit_count"] == 2
    assert metrics["candidate_unit_count"] == 3
    assert metrics["coverage_jaccard"] == pytest.approx(1.0)
    assert metrics["coverage_symmetric_difference_acres"] == pytest.approx(0)
    assert metrics["candidate_overlap_acres"] == pytest.approx(0)
    square_meters_per_acre = 4_046.872609874251
    assert metrics["coverage_intersection_acres"] == pytest.approx(
        2 / square_meters_per_acre
    )
    assert metrics["coverage_union_acres"] == pytest.approx(2 / square_meters_per_acre)
    assert metrics["reference_median_acres"] == pytest.approx(
        1 / square_meters_per_acre
    )
    assert metrics["reference_p05_acres"] == pytest.approx(1 / square_meters_per_acre)
    assert metrics["candidate_p95_acres"] == pytest.approx(
        0.95 / square_meters_per_acre
    )
    assert metrics["reference_sliver_count"] == 2
    assert metrics["candidate_oversized_count"] == 0
    assert metrics["candidate_boundary_length_median_meters"] == pytest.approx(3.0)


def test_compare_segmentations_measures_within_method_overlap_without_unit_matching():
    reference = gpd.GeoDataFrame(
        {"MU_ID": ["same", "same-again"], "Acres": [0.000247, 0.000247]},
        geometry=[box(0, 0, 1, 1), box(0.5, 0, 1.5, 1)],
        crs="EPSG:5070",
    )
    candidate = gpd.GeoDataFrame(
        {"MU_ID": ["same"], "Acres": [0.0003705]},
        geometry=[box(0, 0, 1.5, 1)],
        crs="EPSG:5070",
    )

    metrics = _comparison_module().compare_segmentations(
        reference,
        candidate,
        reference_name="reference",
        candidate_name="candidate",
    )

    square_meters_per_acre = 4_046.872609874251
    assert metrics["reference_overlap_acres"] == pytest.approx(
        0.5 / square_meters_per_acre
    )
    assert metrics["candidate_overlap_acres"] == pytest.approx(0)
    assert "unit_id_agreement_rate" not in metrics.index


def test_compare_segmentations_reports_nonzero_coverage_differences():
    reference = gpd.GeoDataFrame(
        {"MU_ID": ["r"], "Acres": [2.0]},
        geometry=[box(0, 0, 2, 1)],
        crs="EPSG:5070",
    )
    candidate = gpd.GeoDataFrame(
        {"MU_ID": ["c"], "Acres": [2.0]},
        geometry=[box(1, 0, 3, 1)],
        crs="EPSG:5070",
    )

    metrics = _comparison_module().compare_segmentations(
        reference,
        candidate,
        reference_name="reference",
        candidate_name="candidate",
    )

    square_meters_per_acre = 4_046.872609874251
    assert metrics["coverage_intersection_acres"] == pytest.approx(
        1 / square_meters_per_acre
    )
    assert metrics["coverage_union_acres"] == pytest.approx(3 / square_meters_per_acre)
    assert metrics["coverage_symmetric_difference_acres"] == pytest.approx(
        2 / square_meters_per_acre
    )
    assert metrics["coverage_jaccard"] == pytest.approx(1 / 3)


def test_compare_segmentations_measures_unit_acres_from_geometry():
    geometry = box(0, 0, 100, 100)
    reference = gpd.GeoDataFrame(
        {"MU_ID": ["r"], "Acres": [999.0]},
        geometry=[geometry],
        crs="EPSG:5070",
    )
    candidate = gpd.GeoDataFrame(
        {"MU_ID": ["c"], "Acres": [0.001]},
        geometry=[geometry],
        crs="EPSG:5070",
    )

    metrics = _comparison_module().compare_segmentations(
        reference,
        candidate,
        reference_name="reference",
        candidate_name="candidate",
    )

    measured_acres = 10_000 / 4_046.872609874251
    assert metrics["coverage_jaccard"] == pytest.approx(1.0)
    assert metrics["reference_median_acres"] == pytest.approx(measured_acres)
    assert metrics["candidate_median_acres"] == pytest.approx(measured_acres)
    assert metrics["reference_oversized_count"] == 0
    assert metrics["candidate_sliver_count"] == 1


def test_compare_segmentations_uses_strict_sliver_and_oversized_thresholds():
    square_meters_per_acre = 4_046.872609874251
    acre_values = [4.99, 5.0, 200.0, 200.01]
    geometries = [
        box(0, index * 10, acres * square_meters_per_acre, index * 10 + 1)
        for index, acres in enumerate(acre_values)
    ]
    reference = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2", "3", "4"], "Acres": acre_values},
        geometry=geometries,
        crs="EPSG:5070",
    )
    candidate = reference.copy()
    candidate["MU_ID"] = ["a", "b", "c", "d"]

    metrics = _comparison_module().compare_segmentations(
        reference,
        candidate,
        reference_name="reference",
        candidate_name="candidate",
    )

    assert metrics["reference_sliver_count"] == 1
    assert metrics["reference_oversized_count"] == 1


def test_compare_segmentations_converts_us_survey_feet_to_metric_outputs():
    reference = gpd.GeoDataFrame(
        {"MU_ID": ["r"], "Acres": [1.0]},
        geometry=[box(0, 0, 1, 1)],
        crs="EPSG:2263",
    )
    candidate = reference.copy()
    candidate["MU_ID"] = ["c"]

    metrics = _comparison_module().compare_segmentations(
        reference,
        candidate,
        reference_name="reference",
        candidate_name="candidate",
    )

    meters_per_us_survey_foot = 1_200 / 3_937
    assert metrics["reference_coverage_acres"] == pytest.approx(
        meters_per_us_survey_foot**2 / 4_046.872609874251
    )
    assert metrics["reference_boundary_length_median_meters"] == pytest.approx(
        4 * meters_per_us_survey_foot
    )


@pytest.mark.parametrize("mu_ids", [[None, "2"], ["1", "1"]])
def test_compare_segmentations_requires_non_null_unique_mu_ids(mu_ids):
    reference = gpd.GeoDataFrame(
        {"MU_ID": mu_ids, "Acres": [1.0, 1.0]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:5070",
    )
    candidate = gpd.GeoDataFrame(
        {"MU_ID": ["c"], "Acres": [2.0]},
        geometry=[box(0, 0, 2, 1)],
        crs="EPSG:5070",
    )

    with pytest.raises(ValueError, match="MU_ID values must be non-null and unique"):
        _comparison_module().compare_segmentations(
            reference,
            candidate,
            reference_name="reference",
            candidate_name="candidate",
        )


def test_compare_segmentations_requires_polygonal_positive_area():
    reference = gpd.GeoDataFrame(
        {"MU_ID": ["r"], "Acres": [1.0]},
        geometry=[Point(0, 0)],
        crs="EPSG:5070",
    )
    candidate = gpd.GeoDataFrame(
        {"MU_ID": ["c"], "Acres": [1.0]},
        geometry=[box(0, 0, 1, 1)],
        crs="EPSG:5070",
    )

    with pytest.raises(ValueError, match="Polygon or MultiPolygon"):
        _comparison_module().compare_segmentations(
            reference,
            candidate,
            reference_name="reference",
            candidate_name="candidate",
        )

    reference.geometry = [Polygon([(0, 0), (1, 0), (2, 0), (0, 0)])]
    with pytest.raises(ValueError, match="finite positive area"):
        _comparison_module().compare_segmentations(
            reference,
            candidate,
            reference_name="reference",
            candidate_name="candidate",
        )


def test_compare_segmentations_revalidates_candidate_after_reprojection():
    reference = gpd.GeoDataFrame(
        {"MU_ID": ["r"], "Acres": [1.0]},
        geometry=[box(0, 0, 1, 1)],
        crs="EPSG:5070",
    )
    candidate = gpd.GeoDataFrame(
        {"MU_ID": ["c"], "Acres": [1.0]},
        geometry=[box(0, 95, 1, 96)],
        crs="EPSG:4326",
    )

    with pytest.raises(ValueError, match="Candidate segmentation after reprojection"):
        _comparison_module().compare_segmentations(
            reference,
            candidate,
            reference_name="reference",
            candidate_name="candidate",
        )


def test_compare_segmentations_reports_crosswalk_ownership_and_smz_agreement():
    reference = gpd.GeoDataFrame(
        {
            "MU_ID": ["r1", "r2"],
            "Acres": [1.0, 1.0],
            "CROSSWALK_ID": ["x", "y"],
            "OWN_CODE": [3, 4],
            "SMZ_Pct": [10.0, 40.0],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:5070",
    )
    candidate = gpd.GeoDataFrame(
        {
            "MU_ID": ["c1", "c2"],
            "Acres": [1.0, 1.0],
            "CROSSWALK_ID": ["x", "y"],
            "OWN_CODE": [3, 8],
            "SMZ_Pct": [16.0, 30.0],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:5070",
    )

    metrics = _comparison_module().compare_segmentations(
        reference,
        candidate,
        reference_name="reference",
        candidate_name="candidate",
    )

    assert metrics["explicit_unit_crosswalk_available"] is True
    assert metrics["ownership_comparable_units"] == 2
    assert metrics["ownership_agreement_rate"] == pytest.approx(0.5)
    assert metrics["smz_comparable_units"] == 2
    assert metrics["smz_abs_difference_mean_pct_points"] == pytest.approx(8.0)
    assert metrics["smz_abs_difference_median_pct_points"] == pytest.approx(8.0)
    assert metrics["smz_abs_difference_p95_pct_points"] == pytest.approx(9.8)
    assert metrics["smz_abs_difference_max_pct_points"] == pytest.approx(10.0)


def test_compare_segmentations_rejects_non_bijective_unit_crosswalk():
    reference = gpd.GeoDataFrame(
        {
            "MU_ID": ["r1", "r2"],
            "Acres": [1.0, 1.0],
            "CROSSWALK_ID": ["x", "x"],
            "OWN_CODE": [3, 3],
            "SMZ_Pct": [1.0, 2.0],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:5070",
    )
    candidate = reference.copy()
    candidate["MU_ID"] = ["c1", "c2"]

    with pytest.raises(ValueError, match="exactly one MU_ID"):
        _comparison_module().compare_segmentations(
            reference,
            candidate,
            reference_name="reference",
            candidate_name="candidate",
        )


def _weights(*, crosswalk: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    reference = pd.DataFrame(
        {
            "MU_ID": ["r1", "r1", "r2"],
            "TM_VALUE": [1, 2, 3],
            "PLT_CN": ["p1", "p2", "p3"],
            "CELL_COUNT": [6, 4, 1],
            "TOTAL_CELLS": [10, 10, 1],
            "WEIGHT": [0.6, 0.4, 1.0],
        }
    )
    candidate = pd.DataFrame(
        {
            "MU_ID": ["c1", "c1", "c2"],
            "TM_VALUE": [1, 2, 4],
            "PLT_CN": ["p1", "p2", "p4"],
            "CELL_COUNT": [7, 3, 1],
            "TOTAL_CELLS": [10, 10, 1],
            "WEIGHT": [0.7, 0.3, 1.0],
        }
    )
    if crosswalk:
        reference["CROSSWALK_ID"] = ["x", "x", "y"]
        candidate["CROSSWALK_ID"] = ["x", "x", "y"]
    return reference, candidate


def test_compare_attribution_reports_donor_and_weight_diagnostics():
    reference, candidate = _weights(crosswalk=True)

    metrics = _comparison_module().compare_attribution(reference, candidate)

    assert metrics["reference_unit_count"] == 2
    assert metrics["reference_donor_count_median"] == pytest.approx(1.5)
    assert metrics["reference_mixed_plot_rate"] == pytest.approx(0.5)
    assert metrics["candidate_mixed_plot_rate"] == pytest.approx(0.5)
    assert metrics["reference_raw_weight_sum_max_abs_error"] == pytest.approx(0)
    assert metrics["candidate_normalized_weight_sum_max_abs_error"] == pytest.approx(0)
    assert metrics["modal_plot_comparable_units"] == 2
    assert metrics["modal_plot_agreement_rate"] == pytest.approx(0.5)


def test_compare_attribution_does_not_match_mu_ids_without_crosswalk():
    reference, candidate = _weights(crosswalk=False)
    candidate["MU_ID"] = reference["MU_ID"]

    metrics = _comparison_module().compare_attribution(reference, candidate)

    assert metrics["modal_plot_comparable_units"] == 0
    assert pd.isna(metrics["modal_plot_agreement_rate"])


def test_compare_attribution_rejects_one_sided_crosswalk():
    reference, candidate = _weights(crosswalk=True)
    candidate = candidate.drop(columns="CROSSWALK_ID")

    with pytest.raises(ValueError, match="both weight tables"):
        _comparison_module().compare_attribution(reference, candidate)


def test_compare_attribution_rejects_mu_id_mapped_to_multiple_crosswalk_ids():
    reference, candidate = _weights(crosswalk=True)
    reference.loc[1, "CROSSWALK_ID"] = "z"

    with pytest.raises(
        ValueError, match="MU_ID must identify exactly one CROSSWALK_ID"
    ):
        _comparison_module().compare_attribution(reference, candidate)


def test_compare_attribution_rejects_crosswalk_id_mapped_to_multiple_mu_ids():
    reference, candidate = _weights(crosswalk=True)
    reference.loc[2, "CROSSWALK_ID"] = "x"

    with pytest.raises(
        ValueError, match="CROSSWALK_ID must identify exactly one MU_ID"
    ):
        _comparison_module().compare_attribution(reference, candidate)


@pytest.mark.parametrize("column", ["PLT_CN", "TM_VALUE"])
def test_compare_attribution_rejects_ambiguous_duplicate_donors(column):
    reference, candidate = _weights(crosswalk=True)
    reference.loc[1, column] = reference.loc[0, column]

    with pytest.raises(ValueError, match="duplicate donor rows"):
        _comparison_module().compare_attribution(reference, candidate)


@pytest.mark.parametrize("tm_values", [None, ["not-numeric", 2, 3]])
def test_compare_attribution_requires_numeric_tm_value_for_modal_agreement(tm_values):
    reference, candidate = _weights(crosswalk=True)
    if tm_values is None:
        reference = reference.drop(columns="TM_VALUE")
    else:
        reference["TM_VALUE"] = tm_values

    with pytest.raises(ValueError, match="TM_VALUE"):
        _comparison_module().compare_attribution(reference, candidate)


def test_compare_attribution_requires_numeric_cell_count_for_modal_agreement():
    reference, candidate = _weights(crosswalk=True)
    reference["CELL_COUNT"] = ["not-numeric", 4, 1]

    with pytest.raises(ValueError, match="CELL_COUNT must be finite and non-negative"):
        _comparison_module().compare_attribution(reference, candidate)


def test_compare_attribution_modal_tie_break_is_independent_of_input_order():
    reference, candidate = _weights(crosswalk=True)
    reference = reference.loc[reference["MU_ID"] == "r1"].copy()
    candidate = candidate.loc[candidate["MU_ID"] == "c1"].copy()
    reference.loc[0:1, "CELL_COUNT"] = [5, 5]
    reference.loc[0:1, "WEIGHT"] = [0.5, 0.5]
    candidate.loc[0:1, "CELL_COUNT"] = [5, 5]
    candidate.loc[0:1, "WEIGHT"] = [0.5, 0.5]
    candidate = candidate.iloc[[1, 0]].reset_index(drop=True)

    metrics = _comparison_module().compare_attribution(reference, candidate)

    assert metrics["modal_plot_agreement_rate"] == pytest.approx(1.0)


def test_research_review_cites_direct_source_functions():
    note = (
        Path(__file__).resolve().parents[1]
        / "docs/research/leto-vs-boundary-overlay.md"
    ).read_text()

    assert "leto.build_treemap_domain" in note
    assert "leto.subdivide_large_units" in note
    assert "boundary_overlay.process_county" in note
    assert "weights.build_plot_weights" in note
    assert "geometry-derived acreage" in note
    assert "ownership agreement" in note
    assert "SMZ absolute-difference" in note
    assert "FVS workload proxy" in note
    assert "JSON" in note


def test_write_comparison_writes_stable_markdown(tmp_path: Path):
    metrics = pd.Series(
        {
            "reference_name": "leto",
            "coverage_jaccard": 1.0,
            "modal_plot_agreement_rate": pd.NA,
        }
    )
    output = tmp_path / "comparison.md"

    _comparison_module().write_comparison(metrics, output)

    assert output.read_text() == (
        "# Segmentation comparison metrics\n\n"
        "| Metric | Value |\n"
        "|---|---:|\n"
        "| reference_name | leto |\n"
        "| coverage_jaccard | 1 |\n"
        "| modal_plot_agreement_rate | unavailable |\n"
    )


def test_compare_initial_states_reports_readiness_and_workload_proxy():
    reference = SimpleNamespace(
        crosswalk=pd.DataFrame({"MU_ID": ["1", "2", "3", "4"]}),
        weights=pd.DataFrame(
            {
                "MU_ID": ["1", "1", "2", "3", "3", "3", "4"],
                "PLT_CN": ["a", "b", "a", "a", "b", "c", "d"],
            }
        ),
        trees=pd.DataFrame(
            {
                "MU_ID": ["1", "1", "2"],
                "TREE_SOURCE": [
                    "FIA_WEIGHTED_DIRECT",
                    "FIA_WEIGHTED_DIRECT",
                    "IMPUTED_NEAREST",
                ],
            }
        ),
        stands=pd.DataFrame({"STAND_ID": ["MU_1", "MU_2"]}),
        missing_stands=pd.DataFrame({"MU_ID": ["3", "4"]}),
    )
    candidate = SimpleNamespace(
        crosswalk=pd.DataFrame({"MU_ID": ["a", "b"]}),
        weights=pd.DataFrame({"MU_ID": ["a", "b"], "PLT_CN": ["a", "b"]}),
        trees=pd.DataFrame(
            {
                "MU_ID": ["a", "b", "b"],
                "TREE_SOURCE": [
                    "FIA_WEIGHTED_DIRECT",
                    "FIA_WEIGHTED_DIRECT",
                    "FIA_WEIGHTED_DIRECT",
                ],
            }
        ),
        stands=pd.DataFrame({"STAND_ID": ["MU_a", "MU_b"]}),
        missing_stands=pd.DataFrame({"MU_ID": []}),
    )

    metrics = _comparison_module().compare_initial_states(reference, candidate)

    assert metrics["reference_direct_stand_count"] == 1
    assert metrics["reference_direct_stand_rate"] == pytest.approx(0.25)
    assert metrics["reference_imputed_stand_count"] == 1
    assert metrics["reference_missing_stand_count"] == 2
    assert metrics["reference_tree_row_count"] == 3
    assert metrics["reference_donor_plots_per_mu_mean"] == pytest.approx(1.75)
    assert metrics["reference_fvs_workload_proxy_stand_runs"] == 2
    assert metrics["candidate_direct_stand_rate"] == pytest.approx(1.0)


def test_hierarchical_bootstrap_resamples_aois_before_nested_seeds():
    records = pd.DataFrame(
        {
            "AOI_ID": ["small", "large", "large", "large"],
            "seed": [0, 0, 1, 2],
            "reference": [0.0, 0.0, 0.0, 0.0],
            "candidate": [0.0, 10.0, 10.0, 10.0],
        }
    )

    result = _comparison_module().hierarchical_paired_bootstrap(
        records, samples=200, bootstrap_seed=20260720
    )

    assert result["bootstrap_method"] == "aoi_first_hierarchical"
    assert result["observed_mean"] == pytest.approx(5.0)
    assert result["aoi_count"] == 2
    assert result["block_count"] == 4
    assert result["per_block_differences"] == [0.0, 10.0, 10.0, 10.0]


def test_write_comparison_writes_stable_json(tmp_path: Path):
    metrics = pd.Series(
        {
            "reference_name": "leto",
            "coverage_jaccard": np.float64(1.0),
            "modal_plot_agreement_rate": pd.NA,
        }
    )
    output = tmp_path / "comparison.json"

    _comparison_module().write_comparison(metrics, output)

    assert json.loads(output.read_text()) == {
        "coverage_jaccard": 1.0,
        "modal_plot_agreement_rate": None,
        "reference_name": "leto",
    }
    assert output.read_text().endswith("\n")
