"""LETO stages 3-4 donor weights: parent-segment shares inherited by every management unit.

Ports aauslander480/Leto `LETO_v1_project/leto/stage3_crosswalk.py`
(`build_weights_table`, `expand_parent_weights_to_management_units`) and
`pipeline.py` (`_filter_weights`):

- Weights are each TreeMap plot's pixel share of its **parent segment** (SEG_ID), not of the
  management unit, so the riparian and upland pieces of one stand carry the same donor mix.
- The 5% filter drops small donors, but a segment whose donors all fall below it keeps its
  largest one; retained weights are renormalised to sum to 1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.ids import as_id_series

MINIMUM_PLOT_WEIGHT = 0.05  # configs/maine_test.py inventory.minimum_plot_weight


def parent_weights(seg_labels: np.ndarray, tm_values: np.ndarray,
                   value_to_plt: dict[int, str]) -> pd.DataFrame:
    """SEG_ID x PLT_CN pixel shares over cells where both rasters are valid (seg > 0)."""
    valid = seg_labels > 0
    pairs = pd.DataFrame({"SEG_ID": seg_labels[valid].astype(np.int64),
                          "TREEMAP_VALUE": tm_values[valid].astype(np.int64)})
    counts = pairs.value_counts().rename("PIXEL_COUNT").reset_index()
    counts["PLT_CN"] = counts["TREEMAP_VALUE"].map(value_to_plt)
    if counts["PLT_CN"].isna().any():
        missing = sorted(counts.loc[counts["PLT_CN"].isna(), "TREEMAP_VALUE"].unique())[:20]
        raise ValueError(f"TreeMap values without a PLT_CN: {missing}")
    counts["PLT_CN"] = as_id_series(counts["PLT_CN"], column="PLT_CN")
    counts["STAND_PIXEL_COUNT"] = counts.groupby("SEG_ID")["PIXEL_COUNT"].transform("sum")
    counts["WEIGHT"] = counts["PIXEL_COUNT"] / counts["STAND_PIXEL_COUNT"]
    return counts.sort_values(["SEG_ID", "WEIGHT"], ascending=[True, False]).reset_index(drop=True)


def expand_to_units(weights: pd.DataFrame, units: pd.DataFrame) -> pd.DataFrame:
    """Every management unit takes its parent segment's donor rows (``units``: MU_ID, PARENT_SEG)."""
    expanded = units[["MU_ID", "PARENT_SEG"]].merge(
        weights.rename(columns={"SEG_ID": "PARENT_SEG"}), on="PARENT_SEG", how="left",
        validate="many_to_many")
    if expanded["PLT_CN"].isna().any():
        missing = expanded.loc[expanded["PLT_CN"].isna(), "PARENT_SEG"].unique()[:20]
        raise ValueError(f"Management units whose parent has no donors: {list(missing)}")
    return expanded


def filter_weights(weights: pd.DataFrame, minimum_weight: float = MINIMUM_PLOT_WEIGHT, *,
                   key: str) -> pd.DataFrame:
    """Drop donors under ``minimum_weight``, keeping each ``key``'s largest donor if none pass."""
    w = weights.copy()
    w["WEIGHT_ORIGINAL"] = pd.to_numeric(w["WEIGHT"], errors="coerce")
    w = w[w["WEIGHT_ORIGINAL"] > 0]
    retained = w[w["WEIGHT_ORIGINAL"] >= minimum_weight]
    lost = set(w[key]) - set(retained[key])
    if lost:
        largest = (w[w[key].isin(lost)].sort_values([key, "WEIGHT_ORIGINAL"],
                                                     ascending=[True, False])
                   .drop_duplicates(key))
        retained = pd.concat([retained, largest], ignore_index=True)
    retained = retained.copy()
    retained["WEIGHT"] = (retained["WEIGHT_ORIGINAL"]
                          / retained.groupby(key)["WEIGHT_ORIGINAL"].transform("sum"))
    return retained.sort_values([key, "WEIGHT"], ascending=[True, False]).reset_index(drop=True)
