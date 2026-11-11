"""LETO's proximity grouping of small parent stands and its per-unit donor weights.

A verbatim port of aauslander480/Leto `LETO_v1_project/leto/small_stands.py` at the pin in
`research/leto_parity/README.md`; `tests/test_leto_upstream.py` runs upstream's tests on it.

- :func:`plan_proximity_merges` (the ``proximity_merge`` small-stand action): smallest parent
  stands first, each joins the eligible candidate within ``maximum_distance_meters`` (same
  owner and dominant forest type by default, combined acres capped) ranked by mean-age
  difference, then distance, then ID. Only group membership changes: no geometry, no ``SEG_ID``.
- :func:`aggregate_management_unit_weights`: a unit's donor weights are its parent stands'
  weights combined by each piece's acres.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from pipeline.ids import as_id_series


@dataclass
class _Group:
    members: set[int]
    acres: float
    ownership: object
    forest_type: object
    age: float | None


def plan_proximity_merges(
    units: pd.DataFrame,
    candidates: pd.DataFrame,
    *,
    minimum_acres: float,
    maximum_distance_meters: float,
    maximum_combined_acres: float,
    require_same_ownership: bool,
    require_same_forest_type: bool,
    retain_when_no_candidate: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float | int]]:
    """Plan deterministic non-geometric grouping of residual small segments.

    Candidate ranking is biological similarity (absolute mean-age difference)
    followed by distance and target SEG_ID. Missing ages sort after known ages.
    Each accepted pair combines management-unit membership only; geometries and
    original SEG_ID values are never changed.
    """
    required_units = {"SEG_ID", "Acres", "OWN_CODE", "FORTYPCD_DOM", "STDAGE_MEAN"}
    missing_units = required_units - set(units.columns)
    if missing_units:
        raise ValueError(
            f"Proximity units are missing fields: {sorted(missing_units)}"
        )
    required_candidates = {"SOURCE_SEG_ID", "TARGET_SEG_ID", "DISTANCE_METERS"}
    missing_candidates = required_candidates - set(candidates.columns)
    if missing_candidates:
        raise ValueError(
            f"Proximity candidates are missing fields: {sorted(missing_candidates)}"
        )
    if minimum_acres <= 0 or maximum_distance_meters <= 0:
        raise ValueError("Proximity acreage and distance limits must be positive.")
    if maximum_combined_acres < minimum_acres:
        raise ValueError(
            "maximum_combined_acres must be at least minimum_acres."
        )

    frame = units.copy()
    frame["SEG_ID"] = pd.to_numeric(frame["SEG_ID"], errors="raise").astype("int64")
    frame["Acres"] = pd.to_numeric(frame["Acres"], errors="raise")
    frame["STDAGE_MEAN"] = pd.to_numeric(frame["STDAGE_MEAN"], errors="coerce")
    if frame["SEG_ID"].duplicated().any():
        raise ValueError("Proximity planning requires one row per SEG_ID.")
    if (frame["Acres"] <= 0).any():
        raise ValueError("Proximity planning requires positive segment acreage.")

    groups: dict[int, _Group] = {}
    member_to_root: dict[int, int] = {}
    for row in frame.itertuples(index=False):
        seg_id = int(row.SEG_ID)
        age = None if pd.isna(row.STDAGE_MEAN) else float(row.STDAGE_MEAN)
        groups[seg_id] = _Group(
            members={seg_id},
            acres=float(row.Acres),
            ownership=row.OWN_CODE,
            forest_type=row.FORTYPCD_DOM,
            age=age,
        )
        member_to_root[seg_id] = seg_id

    nearby = candidates.copy()
    for column in ("SOURCE_SEG_ID", "TARGET_SEG_ID"):
        nearby[column] = pd.to_numeric(
            nearby[column], errors="raise"
        ).astype("int64")
    nearby["DISTANCE_METERS"] = pd.to_numeric(
        nearby["DISTANCE_METERS"], errors="raise"
    )
    nearby = nearby[
        (nearby["SOURCE_SEG_ID"] != nearby["TARGET_SEG_ID"])
        & (nearby["DISTANCE_METERS"] <= maximum_distance_meters)
    ].copy()
    by_source = {
        int(source): group.copy()
        for source, group in nearby.groupby("SOURCE_SEG_ID", sort=False)
    }

    merge_rows: list[dict[str, float | int]] = []
    unmatched: list[int] = []
    initial_small = frame.loc[frame["Acres"] < minimum_acres].sort_values(
        ["Acres", "SEG_ID"]
    )

    for source_id in initial_small["SEG_ID"].astype(int):
        source_root = member_to_root[source_id]
        source_group = groups[source_root]
        if source_group.acres >= minimum_acres:
            continue

        ranked: list[tuple[float, float, int, int]] = []
        for row in by_source.get(source_id, pd.DataFrame()).itertuples(index=False):
            target_id = int(row.TARGET_SEG_ID)
            if target_id not in member_to_root:
                continue
            target_root = member_to_root[target_id]
            if target_root == source_root:
                continue
            target_group = groups[target_root]
            if source_group.acres + target_group.acres > maximum_combined_acres:
                continue
            if (
                require_same_ownership
                and source_group.ownership != target_group.ownership
            ):
                continue
            if (
                require_same_forest_type
                and source_group.forest_type != target_group.forest_type
            ):
                continue
            if source_group.age is None or target_group.age is None:
                age_difference = float("inf")
            else:
                age_difference = abs(source_group.age - target_group.age)
            ranked.append(
                (
                    age_difference,
                    float(row.DISTANCE_METERS),
                    target_root,
                    target_id,
                )
            )

        if not ranked:
            unmatched.append(source_id)
            continue

        age_difference, distance, target_root, target_id = min(ranked)
        target_group = groups[target_root]
        combined_acres = source_group.acres + target_group.acres
        known_age_acres = 0.0
        weighted_age = 0.0
        for group in (source_group, target_group):
            if group.age is not None:
                known_age_acres += group.acres
                weighted_age += group.age * group.acres
        combined_age = (
            weighted_age / known_age_acres if known_age_acres > 0 else None
        )
        new_root = min(source_group.members | target_group.members)
        combined = _Group(
            members=source_group.members | target_group.members,
            acres=combined_acres,
            ownership=target_group.ownership,
            forest_type=target_group.forest_type,
            age=combined_age,
        )
        del groups[source_root]
        del groups[target_root]
        groups[new_root] = combined
        for member in combined.members:
            member_to_root[member] = new_root
        merge_rows.append(
            {
                "SOURCE_SEG_ID": source_id,
                "TARGET_SEG_ID": target_id,
                "MU_ID": new_root,
                "DISTANCE_METERS": distance,
                "AGE_DIFFERENCE": age_difference,
                "COMBINED_ACRES": combined_acres,
            }
        )

    if unmatched and not retain_when_no_candidate:
        raise RuntimeError(
            "No eligible proximity candidate was found for SEG_ID values: "
            f"{unmatched[:20]}"
        )

    mapping_rows = []
    multipart_sizes = []
    remaining_small = 0
    for root, group in sorted(groups.items()):
        mu_id = min(group.members)
        if len(group.members) > 1:
            multipart_sizes.append(len(group.members))
        if group.acres < minimum_acres:
            remaining_small += 1
        segment_list = ",".join(str(value) for value in sorted(group.members))
        for part, seg_id in enumerate(sorted(group.members), start=1):
            mapping_rows.append(
                {
                    "SEG_ID": seg_id,
                    "MU_ID": mu_id,
                    "MU_PART": part,
                    "MU_SEG_IDS": segment_list,
                }
            )

    mapping = pd.DataFrame(mapping_rows).sort_values("SEG_ID").reset_index(drop=True)
    merges = pd.DataFrame(
        merge_rows,
        columns=[
            "SOURCE_SEG_ID",
            "TARGET_SEG_ID",
            "MU_ID",
            "DISTANCE_METERS",
            "AGE_DIFFERENCE",
            "COMBINED_ACRES",
        ],
    )
    total_acres = float(frame["Acres"].sum())
    qa = {
        "proximity_merges": int(len(merges)),
        "average_merge_distance_meters": (
            float(merges["DISTANCE_METERS"].mean()) if not merges.empty else 0.0
        ),
        "maximum_merge_distance_meters": (
            float(merges["DISTANCE_METERS"].max()) if not merges.empty else 0.0
        ),
        "multipart_management_units": int(len(multipart_sizes)),
        "average_segments_per_multipart_unit": (
            float(sum(multipart_sizes) / len(multipart_sizes))
            if multipart_sizes
            else 0.0
        ),
        "small_stands_remaining": int(remaining_small),
        "total_acres_before": total_acres,
        "total_acres_after": total_acres,
    }
    return mapping, merges, qa


def aggregate_management_unit_weights(
    parent_weights: pd.DataFrame,
    components: pd.DataFrame,
    *,
    tolerance: float = 1e-12,
) -> pd.DataFrame:
    """Combine parent-segment donor weights by component acreage within MU_ID."""
    required_weights = {"SEG_ID", "PLT_CN", "WEIGHT"}
    required_components = {"SEG_ID", "MU_ID", "Acres"}
    if required_weights - set(parent_weights.columns):
        raise ValueError("Parent weights are missing SEG_ID, PLT_CN, or WEIGHT.")
    if required_components - set(components.columns):
        raise ValueError("Components are missing SEG_ID, MU_ID, or Acres.")

    donor = parent_weights.copy()
    donor["SEG_ID"] = pd.to_numeric(donor["SEG_ID"], errors="raise").astype("int64")
    donor["WEIGHT"] = pd.to_numeric(donor["WEIGHT"], errors="raise")
    pieces = components[["SEG_ID", "MU_ID", "Acres"]].copy()
    pieces["SEG_ID"] = pd.to_numeric(pieces["SEG_ID"], errors="raise").astype("int64")
    pieces["MU_ID"] = pd.to_numeric(pieces["MU_ID"], errors="raise").astype("int64")
    pieces["Acres"] = pd.to_numeric(pieces["Acres"], errors="raise")

    joined = pieces.merge(donor, on="SEG_ID", how="left", validate="many_to_many")
    if joined["PLT_CN"].isna().any():
        missing = joined.loc[joined["PLT_CN"].isna(), "SEG_ID"].unique()[:20]
        raise RuntimeError(
            f"Management-unit components lack donor weights: {missing.tolist()}"
        )
    joined["WEIGHTED_DONOR_ACRES"] = joined["Acres"] * joined["WEIGHT"]
    grouped = (
        joined.groupby(["MU_ID", "PLT_CN"], as_index=False, sort=True)
        .agg(
            DONOR_ACRES=("WEIGHTED_DONOR_ACRES", "sum"),
            SOURCE_SEG_IDS=(
                "SEG_ID",
                lambda values: ",".join(
                    str(value) for value in sorted(set(map(int, values)))
                ),
            ),
        )
    )
    totals = pieces.groupby("MU_ID", as_index=False)["Acres"].sum().rename(
        columns={"Acres": "MU_ACRES"}
    )
    grouped = grouped.merge(totals, on="MU_ID", how="left", validate="many_to_one")
    grouped["WEIGHT"] = grouped["DONOR_ACRES"] / grouped["MU_ACRES"]
    grouped["WEIGHT"] = grouped["WEIGHT"] / grouped.groupby(
        "MU_ID"
    )["WEIGHT"].transform("sum")
    grouped["SEG_ID"] = grouped["MU_ID"]
    grouped["STAND_ID"] = "MU_" + as_id_series(grouped["MU_ID"], column="MU_ID")
    grouped["FVS_Stand_ID"] = grouped["STAND_ID"]

    sums = grouped.groupby("MU_ID")["WEIGHT"].sum()
    maximum_error = float((sums - 1.0).abs().max()) if not sums.empty else 0.0
    if maximum_error > tolerance:
        raise RuntimeError(
            "Management-unit donor weights do not sum to 1; "
            f"maximum error={maximum_error:.16g}."
        )
    grouped["WEIGHT_SUM_ERROR"] = grouped["MU_ID"].map((sums - 1.0).abs())
    return grouped.sort_values(
        ["MU_ID", "WEIGHT", "PLT_CN"],
        ascending=[True, False, True],
    ).reset_index(drop=True)
