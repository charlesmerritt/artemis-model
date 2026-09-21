"""Method-neutral diagnostics for S1 segmentation and plot attribution."""

import json
import math
from pathlib import Path
from typing import Any, cast
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import union_all
from shapely.geometry import MultiPolygon, Polygon

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
        raise ValueError(f"{label} missing columns: {sorted(missing)}")
    if features.empty:
        raise ValueError(f"{label} must contain at least one unit")
    if features.crs is None:
        raise ValueError(f"{label} must define a CRS")
    if features["MU_ID"].isna().any() or features["MU_ID"].duplicated().any():
        raise ValueError(f"{label} MU_ID values must be non-null and unique")

    for unit_id, geometry in zip(features["MU_ID"], features.geometry, strict=True):
        if geometry is None or geometry.is_empty:
            raise ValueError(f"{label} unit {unit_id!r} has null or empty geometry")
        if not isinstance(geometry, (Polygon, MultiPolygon)):
            raise ValueError(
                f"{label} unit {unit_id!r} must be Polygon or MultiPolygon"
            )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            area = geometry.area
        if not math.isfinite(area) or area <= 0:
            raise ValueError(f"{label} unit {unit_id!r} must have finite positive area")
        if not geometry.is_valid:
            raise ValueError(f"{label} unit {unit_id!r} has invalid geometry")

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
    acres = geometry_areas * meters_per_unit**2 / SQUARE_METERS_PER_ACRE
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


def _unit_attribute_agreement(
    reference: gpd.GeoDataFrame,
    candidate: gpd.GeoDataFrame,
) -> dict[str, Any]:
    reference_has_crosswalk = CROSSWALK_COLUMN in reference
    candidate_has_crosswalk = CROSSWALK_COLUMN in candidate
    if reference_has_crosswalk != candidate_has_crosswalk:
        raise ValueError(f"{CROSSWALK_COLUMN} must be present in both segmentations")
    if not reference_has_crosswalk:
        return {
            "explicit_unit_crosswalk_available": False,
            "ownership_comparable_units": 0,
            "ownership_agreement_rate": pd.NA,
            "smz_comparable_units": 0,
            "smz_abs_difference_mean_pct_points": pd.NA,
            "smz_abs_difference_median_pct_points": pd.NA,
            "smz_abs_difference_p95_pct_points": pd.NA,
            "smz_abs_difference_max_pct_points": pd.NA,
        }

    required = {CROSSWALK_COLUMN, "MU_ID", "OWN_CODE", "SMZ_Pct"}
    for features, label in ((reference, "Reference"), (candidate, "Candidate")):
        missing = required.difference(features.columns)
        if missing:
            raise ValueError(f"{label} segmentation missing columns: {sorted(missing)}")
        if features[CROSSWALK_COLUMN].isna().any():
            raise ValueError(f"{label} {CROSSWALK_COLUMN} values must be non-null")
        if features[CROSSWALK_COLUMN].duplicated().any():
            raise ValueError(
                f"{label} {CROSSWALK_COLUMN} must identify exactly one MU_ID"
            )

    comparable = reference[[CROSSWALK_COLUMN, "OWN_CODE", "SMZ_Pct"]].merge(
        candidate[[CROSSWALK_COLUMN, "OWN_CODE", "SMZ_Pct"]],
        on=CROSSWALK_COLUMN,
        how="inner",
        suffixes=("_reference", "_candidate"),
        validate="one_to_one",
    )
    ownership = comparable.dropna(subset=["OWN_CODE_reference", "OWN_CODE_candidate"])
    ownership_rate: Any = pd.NA
    if not ownership.empty:
        ownership_rate = float(
            (
                ownership["OWN_CODE_reference"].astype("string")
                == ownership["OWN_CODE_candidate"].astype("string")
            ).mean()
        )

    smz = comparable.dropna(subset=["SMZ_Pct_reference", "SMZ_Pct_candidate"]).copy()
    for column in ("SMZ_Pct_reference", "SMZ_Pct_candidate"):
        smz[column] = pd.to_numeric(smz[column], errors="coerce")
        if not np.isfinite(smz[column].to_numpy(dtype="float64")).all():
            raise ValueError("SMZ_Pct values must be finite and numeric")
    differences = (smz["SMZ_Pct_reference"] - smz["SMZ_Pct_candidate"]).abs()

    def summary(method: str) -> Any:
        if differences.empty:
            return pd.NA
        if method == "mean":
            return float(differences.mean())
        if method == "median":
            return float(differences.median())
        if method == "p95":
            return float(differences.quantile(0.95))
        return float(differences.max())

    return {
        "explicit_unit_crosswalk_available": True,
        "ownership_comparable_units": int(len(ownership)),
        "ownership_agreement_rate": ownership_rate,
        "smz_comparable_units": int(len(smz)),
        "smz_abs_difference_mean_pct_points": summary("mean"),
        "smz_abs_difference_median_pct_points": summary("median"),
        "smz_abs_difference_p95_pct_points": summary("p95"),
        "smz_abs_difference_max_pct_points": summary("max"),
    }


def compare_segmentations(
    reference: gpd.GeoDataFrame,
    candidate: gpd.GeoDataFrame,
    *,
    reference_name: str,
    candidate_name: str,
) -> pd.Series:
    """Compare coverage and unit distributions without matching unit identifiers."""
    reference_units = _validated_segmentation(reference, "Reference segmentation")
    candidate_units = _validated_segmentation(candidate, "Candidate segmentation")
    meters_per_unit = _linear_unit_to_meters(reference_units)
    candidate_units = _validated_segmentation(
        candidate_units.to_crs(reference_units.crs),
        "Candidate segmentation after reprojection",
    )

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
            **_unit_attribute_agreement(reference_units, candidate_units),
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
        raise ValueError(f"{label} {CROSSWALK_COLUMN} must identify exactly one MU_ID")
    if weights.groupby("MU_ID")[CROSSWALK_COLUMN].nunique().gt(1).any():
        raise ValueError(f"{label} MU_ID must identify exactly one {CROSSWALK_COLUMN}")
    return weights.sort_values(
        [CROSSWALK_COLUMN, "CELL_COUNT", "TM_VALUE"],
        ascending=[True, False, True],
    ).drop_duplicates(CROSSWALK_COLUMN)[[CROSSWALK_COLUMN, "PLT_CN"]]


def _validated_modal_weights(weights: pd.DataFrame, label: str) -> pd.DataFrame:
    if "TM_VALUE" not in weights:
        raise ValueError(f"{label} weights missing column: TM_VALUE")
    result = weights.copy()
    result["TM_VALUE"] = pd.to_numeric(result["TM_VALUE"], errors="coerce")
    tm_values = result["TM_VALUE"].to_numpy(dtype="float64")
    if not np.isfinite(tm_values).all():
        raise ValueError(f"{label} weights TM_VALUE must be finite and numeric")
    if (
        result.duplicated(["MU_ID", "PLT_CN"]).any()
        or result.duplicated(["MU_ID", "TM_VALUE"]).any()
    ):
        raise ValueError(f"{label} weights contain ambiguous duplicate donor rows")
    return result


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

    reference = _validated_modal_weights(reference, "Reference")
    candidate = _validated_modal_weights(candidate, "Candidate")

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


def _initial_state_metrics(tables: Any, prefix: str) -> dict[str, Any]:
    required = {"crosswalk", "weights", "trees", "stands", "missing_stands"}
    missing = {name for name in required if not hasattr(tables, name)}
    if missing:
        raise ValueError(f"{prefix} initial-state tables missing: {sorted(missing)}")
    total_ids = set(tables.crosswalk["MU_ID"].astype("string"))
    if not total_ids:
        raise ValueError(f"{prefix} initial-state crosswalk must not be empty")
    weight_ids = set(tables.weights["MU_ID"].astype("string"))
    if total_ids != weight_ids:
        raise ValueError(f"{prefix} initial-state weights must cover every MU_ID")
    direct_ids = set(
        tables.trees.loc[
            tables.trees["TREE_SOURCE"] == "FIA_WEIGHTED_DIRECT", "MU_ID"
        ].astype("string")
    )
    imputed_ids = set(
        tables.trees.loc[
            tables.trees["TREE_SOURCE"] == "IMPUTED_NEAREST", "MU_ID"
        ].astype("string")
    )
    missing_ids = set(tables.missing_stands["MU_ID"].astype("string"))
    if (
        direct_ids & imputed_ids
        or (direct_ids | imputed_ids | missing_ids) != total_ids
    ):
        raise ValueError(
            f"{prefix} direct, imputed, and missing stands must partition MU_ID values"
        )
    donor_counts = tables.weights.groupby("MU_ID")["PLT_CN"].nunique()
    total = len(total_ids)
    runnable_stands = int(tables.stands["STAND_ID"].astype("string").nunique())
    return {
        f"{prefix}_management_unit_count": total,
        f"{prefix}_direct_stand_count": len(direct_ids),
        f"{prefix}_direct_stand_rate": len(direct_ids) / total,
        f"{prefix}_imputed_stand_count": len(imputed_ids),
        f"{prefix}_imputed_stand_rate": len(imputed_ids) / total,
        f"{prefix}_missing_stand_count": len(missing_ids),
        f"{prefix}_missing_stand_rate": len(missing_ids) / total,
        f"{prefix}_tree_row_count": int(len(tables.trees)),
        f"{prefix}_donor_plots_per_mu_min": int(donor_counts.min()),
        f"{prefix}_donor_plots_per_mu_mean": float(donor_counts.mean()),
        f"{prefix}_donor_plots_per_mu_median": float(donor_counts.median()),
        f"{prefix}_donor_plots_per_mu_p95": float(donor_counts.quantile(0.95)),
        f"{prefix}_donor_plots_per_mu_max": int(donor_counts.max()),
        f"{prefix}_fvs_workload_proxy_stand_runs": runnable_stands,
    }


def compare_initial_states(reference: Any, candidate: Any) -> pd.Series:
    """Compare FVS readiness and input-size workload proxies."""
    return pd.Series(
        {
            **_initial_state_metrics(reference, "reference"),
            **_initial_state_metrics(candidate, "candidate"),
        },
        dtype="object",
    )


def hierarchical_paired_bootstrap(
    records: pd.DataFrame,
    *,
    samples: int = 10_000,
    bootstrap_seed: int = 20_260_720,
) -> pd.Series:
    """Bootstrap paired effects by AOI, then seeds nested within sampled AOIs."""
    required = {"AOI_ID", "seed", "reference", "candidate"}
    missing = required.difference(records.columns)
    if missing:
        raise ValueError(f"Bootstrap records missing columns: {sorted(missing)}")
    if samples < 1:
        raise ValueError("Bootstrap sample count must be positive")
    if records.empty or records[["AOI_ID", "seed"]].isna().any().any():
        raise ValueError("Bootstrap records must contain complete AOI and seed blocks")
    if records.duplicated(["AOI_ID", "seed"]).any():
        raise ValueError("Bootstrap AOI and seed blocks must be unique")
    frame = records.copy()
    for column in ("reference", "candidate"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if not np.isfinite(frame[column].to_numpy(dtype="float64")).all():
            raise ValueError("Bootstrap paired values must be finite and numeric")
    frame["difference"] = frame["candidate"] - frame["reference"]
    groups = [group["difference"].to_numpy() for _, group in frame.groupby("AOI_ID")]
    observed = float(np.mean([values.mean() for values in groups]))
    rng = np.random.Generator(np.random.PCG64(bootstrap_seed))
    bootstrap_means = np.empty(samples, dtype="float64")
    for index in range(samples):
        sampled_aois = rng.integers(0, len(groups), size=len(groups))
        aoi_means = []
        for aoi_index in sampled_aois:
            values = groups[int(aoi_index)]
            sampled_seeds = rng.integers(0, len(values), size=len(values))
            aoi_means.append(float(values[sampled_seeds].mean()))
        bootstrap_means[index] = np.mean(aoi_means)
    lower, upper = np.quantile(bootstrap_means, [0.025, 0.975])
    return pd.Series(
        {
            "bootstrap_method": "aoi_first_hierarchical",
            "observed_mean": observed,
            "ci_95_lower": float(lower),
            "ci_95_upper": float(upper),
            "aoi_count": len(groups),
            "block_count": len(frame),
            "per_block_differences": frame["difference"].astype(float).tolist(),
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
    """Write metrics as stable JSON or, for ``.md``, a Markdown table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        serializable = {}
        for name, value in metrics.items():
            if value is pd.NA or (
                not isinstance(value, (list, dict)) and pd.isna(value)
            ):
                serializable[name] = None
            elif isinstance(value, np.generic):
                serializable[name] = value.item()
            else:
                serializable[name] = value
        path.write_text(json.dumps(serializable, indent=2, sort_keys=True) + "\n")
        return
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
