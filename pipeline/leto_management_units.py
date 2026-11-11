"""LETO's final management-unit identity and proximity-chain QA.

A verbatim port of aauslander480/Leto `LETO_v1_project/leto/management_units.py` at the pin in
`research/leto_parity/README.md`; `tests/test_leto_upstream.py` runs upstream's tests on it.

:func:`assign_final_mu_ids` gives one ID per proximity group (``BIO_MU_ID``) and compatibility
class, and :func:`assign_mu_parts` a deterministic part number. With proximity off, the
existing ``MU_ID`` values stand. These routines are geometry-agnostic: the caller measures the
pairwise distances :func:`summarize_mu_chain` reads.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
import math

import pandas as pd


CHAIN_QA_COLUMNS = [
    "MU_ID", "PART_COUNT", "TOTAL_ACRES", "IS_MULTIPART",
    "MAX_PAIR_DIST_M", "MEAN_PAIR_DIST_M", "MAX_CENTROID_DIST_M",
    "MU_SPAN_M", "CHAIN_FLAG", "CHAIN_RATIO", "SOURCE_SEG_COUNT",
    "MIN_SEG_ACRES", "MAX_SEG_ACRES", "MEAN_SEG_ACRES",
]


def assign_final_mu_ids(
    components: pd.DataFrame,
    *,
    compatibility_fields: Sequence[str],
    proximity_enabled: bool,
) -> pd.DataFrame:
    """Assign final MU identity without renumbering SEG_ID.

    BIO_MU_ID records the biological proximity group.  When proximity is
    enabled, incompatible post-split classes receive distinct final MU_IDs.
    With proximity disabled, existing MU_ID values are preserved verbatim so
    the verified baseline identifiers and outputs remain unchanged.
    """
    required = {"SEG_ID", "MU_ID", "BIO_MU_ID", *compatibility_fields}
    missing = required - set(components)
    if missing:
        raise ValueError(f"Components are missing fields: {sorted(missing)}")
    out = components.copy()
    if not proximity_enabled:
        _assert_compatible(out, compatibility_fields)
        return assign_mu_parts(out)

    reserved = set(pd.to_numeric(out["BIO_MU_ID"], errors="raise").astype("int64"))
    next_id = max(reserved, default=0) + 1
    assigned: dict[tuple[object, tuple[object, ...]], int] = {}
    for bio_id, group in out.groupby("BIO_MU_ID", sort=True, dropna=False):
        keys = sorted(
            {_compatibility_key(row, compatibility_fields)
             for _, row in group.iterrows()},
            key=lambda value: tuple("" if pd.isna(item) else str(item) for item in value),
        )
        for index, key in enumerate(keys):
            if len(keys) == 1:
                mu_id = int(bio_id)
            else:
                while next_id in reserved:
                    next_id += 1
                mu_id, next_id = next_id, next_id + 1
            assigned[(bio_id, key)] = mu_id
    out["MU_ID"] = [
        assigned[(row["BIO_MU_ID"], _compatibility_key(row, compatibility_fields))]
        for _, row in out.iterrows()
    ]
    _assert_compatible(out, compatibility_fields)
    return assign_mu_parts(out)


def assign_mu_parts(components: pd.DataFrame) -> pd.DataFrame:
    """Number component polygons deterministically by SEG_ID then source OID."""
    out = components.copy()
    oid = next((c for c in ("SOURCE_OID", "OID@", "OBJECTID") if c in out), None)
    order = ["MU_ID", "SEG_ID"] + ([oid] if oid else [])
    ranked = out.sort_values(order, kind="mergesort")
    parts = ranked.groupby("MU_ID", sort=False).cumcount().add(1)
    out.loc[ranked.index, "MU_PART"] = parts.astype("int64")
    out["MU_PART"] = out["MU_PART"].astype("int64")
    return out


def summarize_mu_chain(
    components: pd.DataFrame,
    pair_distances: Iterable[dict[str, float]],
    *,
    maximum_distance_meters: float,
    warning_chain_ratio: float = 2.0,
) -> dict[str, float | int | bool]:
    """Summarize one final MU from unique component-pair measurements.

    MU_SPAN_M is the maximum shortest boundary-to-boundary distance among
    unique component pairs; it intentionally equals MAX_PAIR_DIST_M.
    """
    if components.empty:
        raise ValueError("Cannot summarize an empty management unit.")
    mu_values = components["MU_ID"].drop_duplicates()
    if len(mu_values) != 1:
        raise ValueError("summarize_mu_chain requires exactly one MU_ID.")
    acres = pd.to_numeric(components["Acres"], errors="raise")
    if (acres <= 0).any():
        raise ValueError("Component acreage must be positive.")
    pairs = list(pair_distances)
    boundary = [float(p["boundary_distance_m"]) for p in pairs]
    centroid = [float(p["centroid_distance_m"]) for p in pairs]
    max_pair = max(boundary, default=0.0)
    mean_pair = sum(boundary) / len(boundary) if boundary else 0.0
    max_centroid = max(centroid, default=0.0)
    ratio = max_pair / maximum_distance_meters
    return {
        "MU_ID": int(mu_values.iloc[0]),
        "PART_COUNT": int(len(components)),
        "TOTAL_ACRES": float(acres.sum()),
        "IS_MULTIPART": bool(len(components) > 1),
        "MAX_PAIR_DIST_M": max_pair,
        "MEAN_PAIR_DIST_M": mean_pair,
        "MAX_CENTROID_DIST_M": max_centroid,
        "MU_SPAN_M": max_pair,
        "CHAIN_FLAG": bool(max_pair > maximum_distance_meters),
        "CHAIN_RATIO": ratio,
        "SOURCE_SEG_COUNT": int(components["SEG_ID"].nunique()),
        "MIN_SEG_ACRES": float(acres.min()),
        "MAX_SEG_ACRES": float(acres.max()),
        "MEAN_SEG_ACRES": float(acres.mean()),
        "WARNING_CHAIN_FLAG": bool(ratio >= warning_chain_ratio),
    }


def summarize_chain_run(qa: pd.DataFrame) -> dict[str, float | int]:
    """Return the requested run-level chain QA counters."""
    if qa.empty:
        return {
            "multipart_management_units": 0, "chain_flagged_units": 0,
            "maximum_observed_span_meters": 0.0,
            "average_span_of_multipart_units_meters": 0.0,
        }
    multipart = qa[qa["IS_MULTIPART"].astype(bool)]
    return {
        "multipart_management_units": int(len(multipart)),
        "chain_flagged_units": int(qa["CHAIN_FLAG"].astype(bool).sum()),
        "maximum_observed_span_meters": float(qa["MU_SPAN_M"].max()),
        "average_span_of_multipart_units_meters": (
            float(multipart["MU_SPAN_M"].mean()) if not multipart.empty else 0.0
        ),
    }


def validate_final_units(
    units: pd.DataFrame,
    components: pd.DataFrame,
    *,
    compatibility_fields: Sequence[str],
    acreage_tolerance: float = 1e-9,
) -> None:
    """Validate uniqueness, component coverage, class consistency, and acreage."""
    if units["MU_ID"].duplicated().any():
        raise RuntimeError("ManagementUnits_Final must contain one row per MU_ID.")
    missing = set(components["MU_ID"]) - set(units["MU_ID"])
    if missing:
        raise RuntimeError(f"Components reference absent MU_ID values: {sorted(missing)[:20]}")
    _assert_compatible(components, compatibility_fields)
    expected = components.groupby("MU_ID")["Acres"].sum().sort_index()
    actual = units.set_index("MU_ID")["Acres"].sort_index()
    aligned = actual.reindex(expected.index)
    if aligned.isna().any() or not all(
        math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=acreage_tolerance)
        for a, b in zip(aligned, expected)
    ):
        raise RuntimeError("Dissolved MU acreage does not match component acreage.")


def _compatibility_key(row: pd.Series, fields: Sequence[str]) -> tuple[object, ...]:
    return tuple(row[field] for field in fields)


def _assert_compatible(frame: pd.DataFrame, fields: Sequence[str]) -> None:
    for field in fields:
        conflicts = frame.groupby("MU_ID", dropna=False)[field].nunique(dropna=False)
        if (conflicts > 1).any():
            ids = conflicts[conflicts > 1].index.tolist()[:20]
            raise RuntimeError(f"Final MU_ID mixes {field}: {ids}")
