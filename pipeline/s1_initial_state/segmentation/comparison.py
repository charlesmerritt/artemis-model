"""Method-neutral diagnostics for S1 segmentation and plot attribution."""

from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import union_all

SQUARE_METERS_PER_ACRE = 4_046.872609874251
SLIVER_ACRES = 5.0
OVERSIZED_ACRES = 200.0
CROSSWALK_COLUMN = "CROSSWALK_ID"


def _linear_unit_to_meters(features: gpd.GeoDataFrame) -> float:
    if features.crs is None:
        raise ValueError("Segmentation must define a CRS")
    if not features.crs.is_projected:
        raise ValueError("Segmentation comparison requires a projected CRS")
    axis_info = features.crs.axis_info
    if not axis_info or axis_info[0].unit_conversion_factor is None:
        raise ValueError("Projected CRS must declare its linear units")
    return float(axis_info[0].unit_conversion_factor)


def _validated_segmentation(features: gpd.GeoDataFrame, label: str) -> gpd.GeoDataFrame:
    missing = {"MU_ID", "Acres"}.difference(features.columns)
    if missing:
        raise ValueError(f"{label} segmentation missing columns: {sorted(missing)}")
    if features.empty:
        raise ValueError(f"{label} segmentation must contain at least one unit")
    if features.crs is None:
        raise ValueError(f"{label} segmentation must define a CRS")
    if features.geometry.isna().any() or features.geometry.is_empty.any():
        raise ValueError(f"{label} segmentation contains null or empty geometry")
    if not features.geometry.is_valid.all():
        raise ValueError(f"{label} segmentation contains invalid geometry")

    result = features.copy()
    result["Acres"] = pd.to_numeric(result["Acres"], errors="coerce")
    acres = result["Acres"].to_numpy(dtype="float64")
    if not np.isfinite(acres).all() or (acres < 0).any():
        raise ValueError(f"{label} segmentation Acres must be finite and non-negative")
    return result


def _distribution(values: pd.Series, prefix: str, unit: str) -> dict[str, float]:
    return {
        f"{prefix}_total_{unit}": float(values.sum()),
        f"{prefix}_median_{unit}": float(values.median()),
        f"{prefix}_p05_{unit}": float(values.quantile(0.05)),
        f"{prefix}_p95_{unit}": float(values.quantile(0.95)),
    }


def _segmentation_metrics(
    features: gpd.GeoDataFrame,
    prefix: str,
    meters_per_unit: float,
) -> tuple[dict[str, Any], Any]:
    coverage = union_all(features.geometry.array)
    geometry_areas = features.geometry.area
    overlap_square_meters = (
        float(geometry_areas.sum()) - float(coverage.area)
    ) * meters_per_unit**2
    acres = features["Acres"]
    boundaries_meters = features.geometry.length * meters_per_unit

    metrics: dict[str, Any] = {
        f"{prefix}_unit_count": int(len(features)),
        f"{prefix}_coverage_acres": (
            float(coverage.area) * meters_per_unit**2 / SQUARE_METERS_PER_ACRE
        ),
        f"{prefix}_overlap_acres": max(
            0.0, overlap_square_meters / SQUARE_METERS_PER_ACRE
        ),
        **_distribution(acres, prefix, "acres"),
        f"{prefix}_sliver_count": int((acres < SLIVER_ACRES).sum()),
        f"{prefix}_oversized_count": int((acres > OVERSIZED_ACRES).sum()),
        **_distribution(boundaries_meters, f"{prefix}_boundary_length", "meters"),
    }
    return metrics, coverage


def compare_segmentations(
    reference: gpd.GeoDataFrame,
    candidate: gpd.GeoDataFrame,
    *,
    reference_name: str,
    candidate_name: str,
) -> pd.Series:
    """Compare coverage and unit distributions without matching unit identifiers."""
    reference_units = _validated_segmentation(reference, "Reference")
    candidate_units = _validated_segmentation(candidate, "Candidate")
    meters_per_unit = _linear_unit_to_meters(reference_units)
    candidate_units = candidate_units.to_crs(reference_units.crs)

    reference_metrics, reference_coverage = _segmentation_metrics(
        reference_units, "reference", meters_per_unit
    )
    candidate_metrics, candidate_coverage = _segmentation_metrics(
        candidate_units, "candidate", meters_per_unit
    )
    intersection = reference_coverage.intersection(candidate_coverage)
    coverage_union = reference_coverage.union(candidate_coverage)
    symmetric_difference = reference_coverage.symmetric_difference(candidate_coverage)
    square_units_to_acres = meters_per_unit**2 / SQUARE_METERS_PER_ACRE

    return pd.Series(
        {
            "reference_name": reference_name,
            "candidate_name": candidate_name,
            **reference_metrics,
            **candidate_metrics,
            "coverage_intersection_acres": (
                float(intersection.area) * square_units_to_acres
            ),
            "coverage_union_acres": float(coverage_union.area) * square_units_to_acres,
            "coverage_symmetric_difference_acres": (
                float(symmetric_difference.area) * square_units_to_acres
            ),
            "coverage_jaccard": (float(intersection.area) / float(coverage_union.area)),
        },
        dtype="object",
    )


def _validated_weights(weights: pd.DataFrame, label: str) -> pd.DataFrame:
    required = {"MU_ID", "PLT_CN", "CELL_COUNT", "TOTAL_CELLS", "WEIGHT"}
    missing = required.difference(weights.columns)
    if missing:
        raise ValueError(f"{label} weights missing columns: {sorted(missing)}")
    if weights.empty:
        raise ValueError(f"{label} weights must contain at least one donor")

    result = weights.copy()
    for column in ("CELL_COUNT", "TOTAL_CELLS", "WEIGHT"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
        values = result[column].to_numpy(dtype="float64")
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(
                f"{label} weights {column} must be finite and non-negative"
            )
    if (result["TOTAL_CELLS"] <= 0).any():
        raise ValueError(f"{label} weights TOTAL_CELLS must be positive")
    if result[["MU_ID", "PLT_CN"]].isna().any().any():
        raise ValueError(f"{label} weights contain null MU_ID or PLT_CN")
    return result


def _weight_sum_metrics(weights: pd.DataFrame, prefix: str) -> dict[str, Any]:
    donor_counts = cast(pd.Series, weights.groupby("MU_ID")["PLT_CN"].nunique())
    raw_weight = weights["CELL_COUNT"] / weights["TOTAL_CELLS"]
    raw_sums = raw_weight.groupby(weights["MU_ID"]).sum()
    normalized_sums = weights.groupby("MU_ID")["WEIGHT"].sum()

    metrics: dict[str, Any] = {
        f"{prefix}_unit_count": int(len(donor_counts)),
        f"{prefix}_donor_count_min": int(donor_counts.min()),
        f"{prefix}_donor_count_median": float(donor_counts.median()),
        f"{prefix}_donor_count_p05": float(donor_counts.quantile(0.05)),
        f"{prefix}_donor_count_p95": float(donor_counts.quantile(0.95)),
        f"{prefix}_donor_count_max": int(donor_counts.max()),
        f"{prefix}_mixed_plot_rate": float((donor_counts > 1).mean()),
    }
    for label, sums in (("raw", raw_sums), ("normalized", normalized_sums)):
        metrics.update(
            {
                f"{prefix}_{label}_weight_sum_min": float(sums.min()),
                f"{prefix}_{label}_weight_sum_median": float(sums.median()),
                f"{prefix}_{label}_weight_sum_max": float(sums.max()),
                f"{prefix}_{label}_weight_sum_max_abs_error": float(
                    (sums - 1.0).abs().max()
                ),
            }
        )
    return metrics


def _modal_plots_by_crosswalk(weights: pd.DataFrame, label: str) -> pd.DataFrame:
    if weights.groupby(CROSSWALK_COLUMN)["MU_ID"].nunique().gt(1).any():
        raise ValueError(
            f"{label} {CROSSWALK_COLUMN} must identify at most one management unit"
        )
    ranked = weights.copy()
    sort_columns = [CROSSWALK_COLUMN, "CELL_COUNT"]
    ascending = [True, False]
    if "TM_VALUE" in ranked.columns:
        sort_columns.append("TM_VALUE")
        ascending.append(True)
    return ranked.sort_values(sort_columns, ascending=ascending).drop_duplicates(
        CROSSWALK_COLUMN
    )[[CROSSWALK_COLUMN, "PLT_CN"]]


def _modal_agreement(
    reference: pd.DataFrame, candidate: pd.DataFrame
) -> dict[str, Any]:
    reference_has_crosswalk = CROSSWALK_COLUMN in reference
    candidate_has_crosswalk = CROSSWALK_COLUMN in candidate
    if reference_has_crosswalk != candidate_has_crosswalk:
        raise ValueError(f"{CROSSWALK_COLUMN} must be present in both weight tables")
    if not reference_has_crosswalk:
        return {
            "explicit_crosswalk_available": False,
            "modal_plot_comparable_units": 0,
            "modal_plot_agreement_rate": pd.NA,
        }
    if (
        reference[CROSSWALK_COLUMN].isna().any()
        or candidate[CROSSWALK_COLUMN].isna().any()
    ):
        raise ValueError(f"{CROSSWALK_COLUMN} values must be non-null")

    reference_modal = _modal_plots_by_crosswalk(reference, "Reference").rename(
        columns={"PLT_CN": "reference_plot"}
    )
    candidate_modal = _modal_plots_by_crosswalk(candidate, "Candidate").rename(
        columns={"PLT_CN": "candidate_plot"}
    )
    comparable = reference_modal.merge(
        candidate_modal, on=CROSSWALK_COLUMN, how="inner", validate="one_to_one"
    )
    agreement: Any = pd.NA
    if not comparable.empty:
        agreement = float(
            (comparable["reference_plot"] == comparable["candidate_plot"]).mean()
        )
    return {
        "explicit_crosswalk_available": True,
        "modal_plot_comparable_units": int(len(comparable)),
        "modal_plot_agreement_rate": agreement,
    }


def compare_attribution(
    reference_weights: pd.DataFrame,
    candidate_weights: pd.DataFrame,
) -> pd.Series:
    """Compare donor distributions, using only an explicit crosswalk for agreement."""
    reference = _validated_weights(reference_weights, "Reference")
    candidate = _validated_weights(candidate_weights, "Candidate")
    return pd.Series(
        {
            **_weight_sum_metrics(reference, "reference"),
            **_weight_sum_metrics(candidate, "candidate"),
            **_modal_agreement(reference, candidate),
        },
        dtype="object",
    )


def _markdown_value(value: Any) -> str:
    if value is pd.NA or pd.isna(value):
        return "unavailable"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.12g}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_comparison(metrics: pd.Series, path: Path) -> None:
    """Write an ordered metric series as a stable Markdown table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Segmentation comparison metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {name} | {_markdown_value(value)} |" for name, value in metrics.items()
    )
    path.write_text("\n".join(lines) + "\n")
