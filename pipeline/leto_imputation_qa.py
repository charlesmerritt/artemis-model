"""LETO stage 4 imputation QA: why each stand was imputed, and how far its donor was.

Ported from aauslander480/Leto `LETO_v1_project/leto/imputation_qa.py` (d2b92ee) with one
change: plot control numbers go through `pipeline.ids.as_id_series` instead of LETO's
strip-".0" cleanup, which cannot repair an ID that has been through a float.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.ids import as_id_series


FAILURE_REASONS = (
    "MISSING_FIA_DONOR", "NO_LIVE_TREES", "SPECIES_MAPPING_FAILURE",
    "TREE_DATA_FAILURE", "NO_USABLE_TREES_OTHER",
)


def summarize_fia_donors(trees, species_lookup, live_status_code):
    """Count raw, live, mapped-live, and usable records for each FIA donor."""
    frame = trees.copy()
    frame["PLT_CN"] = _clean(frame["PLT_CN"])
    frame["STATUSCD_NUM"] = pd.to_numeric(frame["STATUSCD"], errors="coerce")
    frame["SPCD_NUM"] = pd.to_numeric(frame["SPCD"], errors="coerce")
    frame["DIA_NUM"] = pd.to_numeric(frame["DIA"], errors="coerce")
    frame["TPA_NUM"] = pd.to_numeric(frame["TPA_UNADJ"], errors="coerce")
    frame["SPECIES_KEY"] = frame["SPCD_NUM"].astype("Int64").astype(str).str.zfill(3)
    frame["IS_LIVE"] = frame["STATUSCD_NUM"] == int(live_status_code)
    frame["IS_MAPPED"] = frame["SPECIES_KEY"].map(species_lookup).notna()
    # Mirror the existing Stage 4 usable-tree filter exactly: diameter
    # must be present and weighted TPA must be positive. CN and crown ratio
    # are retained QA attributes but are not current exclusion criteria.
    frame["VALID_TREE_DATA"] = (
        frame["DIA_NUM"].notna()
        & frame["TPA_NUM"].notna() & (frame["TPA_NUM"] > 0)
    )
    frame["IS_USABLE"] = frame["IS_LIVE"] & frame["IS_MAPPED"] & frame["VALID_TREE_DATA"]
    rows = []
    for plot, group in frame.groupby("PLT_CN", sort=True):
        live = group["IS_LIVE"]
        rows.append({
            "PLT_CN": plot,
            "FIA_TREE_RECORDS_TOTAL": int(len(group)),
            "LIVE_TREE_RECORDS": int(live.sum()),
            "MAPPED_LIVE_TREE_RECORDS": int((live & group["IS_MAPPED"]).sum()),
            "USABLE_TREE_RECORDS": int(group["IS_USABLE"].sum()),
            "UNMAPPED_SPECIES_RECORDS": int((live & ~group["IS_MAPPED"]).sum()),
            "INVALID_TREE_RECORDS": int(
                (live & group["IS_MAPPED"] & ~group["VALID_TREE_DATA"]).sum()
            ),
        })
    return pd.DataFrame(rows)


def build_imputation_diagnostics(
    stands, original_weights, retained_weights, donor_audit, tree_list
):
    """Create one explanatory row for every nearest-imputed final MU."""
    stands = stands.copy()
    stands["STAND_ID"] = as_id_series(stands["STAND_ID"])
    original = _prepare_weights(original_weights)
    retained = _prepare_weights(retained_weights)
    audit = donor_audit.set_index("PLT_CN").to_dict("index") if not donor_audit.empty else {}
    imputed_trees = tree_list[
        tree_list["TREE_SOURCE"].astype(str) == "IMPUTED_NEAREST"
    ].copy()
    nearest = (
        imputed_trees.sort_values(["STAND_ID", "NEAR_DIST", "DONOR_STAND_ID"])
        .drop_duplicates("STAND_ID")
        .set_index("STAND_ID")
    )
    rows = []
    for stand_id in sorted(nearest.index.astype(str)):
        stand = stands.loc[stands["STAND_ID"] == stand_id]
        if len(stand) != 1:
            raise RuntimeError(f"Expected one stand row for {stand_id}.")
        stand = stand.iloc[0]
        before = original[original["STAND_ID"] == stand_id]
        after = retained[retained["STAND_ID"] == stand_id]
        retained_plots = after["PLT_CN"].tolist()
        dropped = before[~before["PLT_CN"].isin(set(retained_plots))]
        counts = _sum_audit(retained_plots, audit)
        missing_plots = sorted(set(retained_plots) - set(audit))
        found_plots = sorted(set(retained_plots) & set(audit))
        dropped_live = sum(
            int(audit.get(plot, {}).get("LIVE_TREE_RECORDS", 0) > 0)
            for plot in dropped["PLT_CN"].unique()
        )
        dropped_usable = sum(
            int(audit.get(plot, {}).get("USABLE_TREE_RECORDS", 0) > 0)
            for plot in dropped["PLT_CN"].unique()
        )
        reasons = []
        if missing_plots:
            reasons.append("MISSING_FIA_DONOR")
        if counts["FIA_TREE_RECORDS_TOTAL"] > 0 and counts["LIVE_TREE_RECORDS"] == 0:
            reasons.append("NO_LIVE_TREES")
        if counts["LIVE_TREE_RECORDS"] > 0 and counts["MAPPED_LIVE_TREE_RECORDS"] == 0:
            reasons.append("SPECIES_MAPPING_FAILURE")
        if counts["MAPPED_LIVE_TREE_RECORDS"] > 0 and counts["USABLE_TREE_RECORDS"] == 0:
            reasons.append("TREE_DATA_FAILURE")
        if not reasons:
            reasons.append("NO_USABLE_TREES_OTHER")
        near = nearest.loc[stand_id]
        record = stand.to_dict()
        record.update({
            "MU_ID": stand.get("MU_ID", stand.get("SEG_ID")),
            "TREE_SOURCE": "IMPUTED_NEAREST",
            "FAILURE_REASON": reasons[0],
            "FAILURE_REASONS": ";".join(reasons),
            "TOTAL_RETAINED_DONORS": int(after["PLT_CN"].nunique()),
            "FOUND_FIA_DONORS": len(found_plots),
            "MISSING_FIA_DONORS": len(missing_plots),
            "RETAINED_PLT_CN": ";".join(retained_plots),
            "RETAINED_DONOR_WEIGHTS": ";".join(
                f"{row.PLT_CN}:{row.WEIGHT:.12g}" for row in after.itertuples()
            ),
            "MISSING_PLT_CN": ";".join(missing_plots),
            "FOUND_PLT_CN": ";".join(found_plots),
            **counts,
            "ORIGINAL_DONOR_COUNT": int(before["PLT_CN"].nunique()),
            "RETAINED_DONOR_COUNT": int(after["PLT_CN"].nunique()),
            "DROPPED_DONOR_COUNT": int(dropped["PLT_CN"].nunique()),
            "DROPPED_DONOR_WEIGHT_TOTAL": float(dropped["WEIGHT_ORIGINAL"].sum()),
            "DROPPED_DONORS_WITH_FIA_LIVE_TREES": int(dropped_live),
            "WOULD_HAVE_USABLE_TREES_WITHOUT_5PCT_FILTER": bool(dropped_usable > 0),
            "DONOR_STAND_ID": str(near["DONOR_STAND_ID"]),
            "NEAR_DIST": float(near["NEAR_DIST"]),
        })
        rows.append(record)
    result = pd.DataFrame(rows)
    _validate_diagnostics(result, audit)
    return result


def build_imputation_summary(stands, diagnostics, distance_units):
    """Build long-form overall, failure, distance-statistic, and bin QA rows."""
    stands = stands.copy()
    stands["Acres"] = pd.to_numeric(stands["Acres"], errors="raise")
    total_mus, total_acres = len(stands), float(stands["Acres"].sum())
    imputed_ids = set(as_id_series(diagnostics["STAND_ID"])) if not diagnostics.empty else set()
    imputed_acres = float(
        stands.loc[as_id_series(stands["STAND_ID"]).isin(imputed_ids), "Acres"].sum()
    )
    imputed_mus = len(imputed_ids)
    rows = [{
        "SECTION": "OVERALL", "TOTAL_MUS": total_mus,
        "DIRECT_MUS": total_mus - imputed_mus, "IMPUTED_MUS": imputed_mus,
        "PCT_MUS_IMPUTED": _pct(imputed_mus, total_mus),
        "TOTAL_ACRES": total_acres, "DIRECT_ACRES": total_acres - imputed_acres,
        "IMPUTED_ACRES": imputed_acres,
        "PCT_ACRES_IMPUTED": _pct(imputed_acres, total_acres),
        "DISTANCE_UNITS": distance_units,
    }]
    for reason in FAILURE_REASONS:
        group = (
            diagnostics[diagnostics["FAILURE_REASON"] == reason]
            if not diagnostics.empty else pd.DataFrame()
        )
        acres = (
            float(pd.to_numeric(group["Acres"], errors="coerce").sum())
            if "Acres" in group else 0.0
        )
        rows.append({
            "SECTION": "FAILURE_REASON", "FAILURE_REASON": reason,
            "MU_COUNT": len(group), "PCT_OF_ALL_MUS": _pct(len(group), total_mus),
            "ACRES": acres, "PCT_OF_ALL_ACRES": _pct(acres, total_acres),
            "PCT_OF_IMPUTED_ACRES": _pct(acres, imputed_acres),
            "DISTANCE_UNITS": distance_units,
        })
    if not diagnostics.empty:
        distances = pd.to_numeric(diagnostics["NEAR_DIST"], errors="raise")
        for name, value in (
            ("MIN", distances.min()), ("MEDIAN", distances.median()),
            ("MEAN", distances.mean()), ("P90", distances.quantile(.90)),
            ("P95", distances.quantile(.95)), ("MAX", distances.max()),
        ):
            rows.append({"SECTION": "DISTANCE_STAT", "STATISTIC": name,
                         "DISTANCE": float(value), "DISTANCE_UNITS": distance_units})
        bins = [-np.inf, 100, 250, 500, 1000, 2000, np.inf]
        labels = ["0-100", "100-250", "250-500", "500-1000", "1000-2000", ">2000"]
        assigned = pd.cut(distances, bins=bins, labels=labels, right=True)
        for label in labels:
            group = diagnostics.loc[assigned == label]
            rows.append({"SECTION": "DISTANCE_BIN", "DISTANCE_BIN": label,
                         "MU_COUNT": len(group),
                         "ACRES": float(pd.to_numeric(group.get("Acres"), errors="coerce").sum()),
                         "DISTANCE_UNITS": distance_units})
    summary = pd.DataFrame(rows)
    overall = rows[0]
    if overall["DIRECT_MUS"] + overall["IMPUTED_MUS"] != total_mus:
        raise RuntimeError("Direct plus imputed MU count does not equal total MUs.")
    if abs(overall["DIRECT_ACRES"] + imputed_acres - total_acres) > 1e-6:
        raise RuntimeError("Direct plus imputed acreage does not equal total acreage.")
    return summary


def _prepare_weights(frame):
    out = frame.copy()
    out["STAND_ID"] = as_id_series(out["STAND_ID"])
    out["PLT_CN"] = _clean(out["PLT_CN"])
    out["WEIGHT"] = pd.to_numeric(out["WEIGHT"], errors="raise")
    if "WEIGHT_ORIGINAL" not in out:
        out["WEIGHT_ORIGINAL"] = out["WEIGHT"]
    out["WEIGHT_ORIGINAL"] = pd.to_numeric(out["WEIGHT_ORIGINAL"], errors="raise")
    return out.sort_values(["STAND_ID", "WEIGHT"], ascending=[True, False])


def _sum_audit(plots, audit):
    fields = [
        "FIA_TREE_RECORDS_TOTAL", "LIVE_TREE_RECORDS",
        "MAPPED_LIVE_TREE_RECORDS", "USABLE_TREE_RECORDS",
        "UNMAPPED_SPECIES_RECORDS", "INVALID_TREE_RECORDS",
    ]
    return {field: sum(int(audit.get(plot, {}).get(field, 0)) for plot in set(plots))
            for field in fields}


def _validate_diagnostics(frame, audit):
    if frame.empty:
        return
    if frame["MU_ID"].duplicated().any():
        raise RuntimeError("QA_Imputation_Diagnostics contains duplicate MU_ID rows.")
    if frame["DONOR_STAND_ID"].isna().any() or (frame["DONOR_STAND_ID"] == "").any():
        raise RuntimeError("Every imputed MU must have a donor stand.")
    if frame["FAILURE_REASON"].isna().any():
        raise RuntimeError("Every imputed MU must have a failure reason.")
    missing = frame["FAILURE_REASON"] == "MISSING_FIA_DONOR"
    if missing.any() and (frame.loc[missing, "MISSING_FIA_DONORS"] < 1).any():
        raise RuntimeError("MISSING_FIA_DONOR requires an absent FIA PLT_CN.")


def _clean(series):
    return as_id_series(series, column="PLT_CN")


def _pct(value, total):
    return 100.0 * float(value) / float(total) if total else 0.0
