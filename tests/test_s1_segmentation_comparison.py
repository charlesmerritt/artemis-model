from importlib import import_module
from pathlib import Path
import sys

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

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
    assert metrics["reference_median_acres"] == pytest.approx(0.000247)
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
