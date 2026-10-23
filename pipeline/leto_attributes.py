"""LETO stage 1: the five segmentation attribute rasters, FIA first (pure arrays, no I/O).

A port of aauslander480/Leto `LETO_v1_project/leto/stage1_rasters.py`:

- **Dominant condition.** Each plot's accessible-forest condition (``COND_STATUS_CD == 1``)
  with the largest ``CONDPROP_UNADJ``; ``CONDID`` breaks ties. It supplies STDAGE, FORTYPCD
  and BALIVE.
- **Live-tree summary.** TPA = sum of ``TPA_UNADJ`` and QMD = sqrt(sum TPA·DIA² / sum TPA)
  over live trees (``STATUSCD == 1``, ``DIA > 0``, ``TPA_UNADJ > 0``) in that condition.
- **Fallback.** A field FIA cannot supply comes from the TreeMap raster attribute table
  (``TPA_LIVE`` for TPA); a cell still without a value takes the nearest assigned cell's.
- **Storage.** STDAGE and FORTYPCD are whole numbers; BALIVE, QMD and TPA are kept in tenths,
  exactly as LETO's integer rasters store and reread them.

Provenance per cell follows LETO's QA rasters: 1 FIA, 2 TreeMap RAT, 3 nearest-cell fill.
"""

from __future__ import annotations

from enum import IntEnum

import numpy as np
import pandas as pd
from scipy import ndimage

from pipeline.ids import as_id_series

FIELDS = ("STDAGE", "FORTYPCD", "BALIVE", "QMD", "TPA")
RAT_FALLBACK = {"STDAGE": "STDAGE", "FORTYPCD": "FORTYPCD", "BALIVE": "BALIVE",
                "QMD": "QMD", "TPA": "TPA_LIVE"}
# LETO configs/maine_test.py raster_model.stored_value_multipliers
STORED_MULTIPLIER = {"STDAGE": 1.0, "FORTYPCD": 1.0, "BALIVE": 10.0, "QMD": 10.0, "TPA": 10.0}
FOREST_CONDITION_STATUS = 1
LIVE_TREE_STATUS = 1


class Provenance(IntEnum):
    MISSING = 0
    FIA = 1
    TREEMAP_RAT = 2
    NEAREST = 3


def dominant_conditions(cond: pd.DataFrame) -> pd.DataFrame:
    forest = cond[(cond["COND_STATUS_CD"] == FOREST_CONDITION_STATUS)
                  & cond["PLT_CN"].notna() & cond["CONDID"].notna()].copy()
    forest["PLT_CN"] = as_id_series(forest["PLT_CN"], column="PLT_CN")
    forest = forest.sort_values(["PLT_CN", "CONDPROP_UNADJ", "CONDID"],
                                ascending=[True, False, True], na_position="last")
    return forest.drop_duplicates("PLT_CN", keep="first")


def live_tree_summary(trees: pd.DataFrame, dominant: pd.DataFrame) -> pd.DataFrame:
    live = trees[(trees["STATUSCD"] == LIVE_TREE_STATUS) & (trees["DIA"] > 0)
                 & (trees["TPA_UNADJ"] > 0)].copy()
    live["PLT_CN"] = as_id_series(live["PLT_CN"], column="PLT_CN")
    live = live.merge(dominant[["PLT_CN", "CONDID"]], on=["PLT_CN", "CONDID"], how="inner")
    live["TPA_DIA2"] = live["TPA_UNADJ"] * live["DIA"] ** 2
    out = live.groupby(["PLT_CN", "CONDID"], as_index=False).agg(
        TPA=("TPA_UNADJ", "sum"), TPA_DIA2=("TPA_DIA2", "sum"),
        LIVE_TREE_RECORDS=("DIA", "size"))
    out["QMD"] = np.sqrt(out.pop("TPA_DIA2") / out["TPA"])
    return out


def plot_lookup(cond: pd.DataFrame, trees: pd.DataFrame) -> pd.DataFrame:
    """One row per PLT_CN: dominant-condition STDAGE/FORTYPCD/BALIVE plus live TPA/QMD."""
    dominant = dominant_conditions(cond)
    summary = live_tree_summary(trees, dominant)
    lookup = dominant.merge(summary, on=["PLT_CN", "CONDID"], how="left", validate="one_to_one")
    return lookup[["PLT_CN", "CONDID", "STDAGE", "FORTYPCD", "BALIVE", "TPA", "QMD"]]


def value_records(vat: pd.DataFrame, lookup: pd.DataFrame) -> pd.DataFrame:
    """Attributes per raster VALUE: FIA-derived where present, TreeMap RAT otherwise.

    ``vat`` has ``Value`` and ``PLT_CN`` plus whichever RAT fallback columns it carries.
    Each field gets a ``SOURCE_<field>`` provenance column.
    """
    rat = vat.copy()
    rat["PLT_CN"] = as_id_series(rat["PLT_CN"], column="PLT_CN")
    merged = rat[["Value", "PLT_CN"]].merge(
        lookup.drop_duplicates("PLT_CN"), on="PLT_CN", how="left")
    merged.index = rat["Value"].astype(np.int64).to_numpy()
    merged.index.name = "Value"
    out = pd.DataFrame(index=merged.index)
    out["PLT_CN"] = merged["PLT_CN"].to_numpy()
    for field in FIELDS:
        fia = pd.to_numeric(merged[field], errors="coerce").to_numpy(dtype=float)
        fallback_column = RAT_FALLBACK[field]
        fallback = (pd.to_numeric(rat[fallback_column], errors="coerce").to_numpy(dtype=float)
                    if fallback_column in rat else np.full(len(rat), np.nan))
        use_fia = ~np.isnan(fia)
        out[field] = np.where(use_fia, fia, fallback)
        out[f"SOURCE_{field}"] = np.where(
            use_fia, Provenance.FIA,
            np.where(np.isnan(fallback), Provenance.MISSING, Provenance.TREEMAP_RAT))
    return out


def _stored(values: np.ndarray, field: str) -> np.ndarray:
    multiplier = STORED_MULTIPLIER[field]
    return np.rint(values * multiplier) / multiplier


def attribute_rasters(tm: np.ndarray, valid: np.ndarray, records: pd.DataFrame
                      ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Per-field float32 grids over ``valid`` (NaN elsewhere) and uint8 provenance grids."""
    lut_size = int(max(records.index.max(), tm[valid].max())) + 1
    grids, provenance = {}, {}
    for field in FIELDS:
        lut = np.full(lut_size, np.nan)
        lut[records.index.to_numpy()] = records[field].to_numpy(dtype=float)
        source_lut = np.zeros(lut_size, dtype=np.uint8)
        source_column = f"SOURCE_{field}"
        if source_column in records:
            source_lut[records.index.to_numpy()] = records[source_column].to_numpy()
        else:
            source_lut[records.index.to_numpy()] = Provenance.FIA
        work = np.full(tm.shape, np.nan)
        work[valid] = lut[tm[valid]]
        source = np.zeros(tm.shape, dtype=np.uint8)
        source[valid] = source_lut[tm[valid]]
        assigned = valid & np.isfinite(work)
        missing = valid & ~assigned
        if missing.any():
            if not assigned.any():
                raise ValueError(f"{field} has no assigned cell to fill from")
            nearest = ndimage.distance_transform_edt(~assigned, return_distances=False,
                                                     return_indices=True)
            work[missing] = work[tuple(nearest)][missing]
            source[missing] = Provenance.NEAREST
        grid = np.full(tm.shape, np.nan, dtype=np.float32)
        grid[valid] = _stored(work[valid], field)
        grids[field] = grid
        source[~valid] = Provenance.MISSING
        provenance[field] = source
    return grids, provenance
