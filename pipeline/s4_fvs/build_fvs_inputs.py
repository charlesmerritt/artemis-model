"""
Build FVS StandInit / TreeInit tables for management units (LETO CSV-pipeline port).

Given management units, their area-weighted PLT_CN table (from
``pipeline.s3_management.assign_plt_cn``), and Chaz's FVS-ready per-plot tables
(``FL_FVS_TREEINIT_PLOT.csv`` / ``FL_FVS_STANDINIT_PLOT.csv``, keyed by ``STAND_CN`` =
PLT_CN), this assembles the per-unit FVS input tables. Follows the LETO ArcGIS prototype
(`scripts/LETO_CSV_PIPELINE.txt`) but in the geopandas/pandas stack — and because the FIA
tree lists are already FVS-ready per plot, it joins those directly rather than re-reading
raw FIA ``TREE.csv`` and re-running a species crosswalk.

Steps:
    1. Filter the weighted PLT_CN table to plots contributing at least ``min_weight`` of a
       unit, then renormalise (LETO ``MIN_PLT_WEIGHT = 0.05``).
    2. Join to the per-plot TreeInit on PLT_CN; the unit's tree list is the union of its
       donor plots' trees, with each tree's TPA (``TREE_COUNT``) scaled by the plot weight.
    3. Build the per-unit StandInit for units that received live trees.
    4. Impute tree lists for units with no live trees from their **nearest runnable unit**
       (LETO ``GenerateNearTable`` nearest-runnable; here via a geometry STRtree).

The reshaping functions are pure and unit-tested; ``build_fvs_inputs`` wires them together.

Usage:
    uv run python -m pipeline.s4_fvs.build_fvs_inputs \\
        --units .../management_units_state0.gpkg \\
        --weights .../MU_PLT_CN_Weights.csv \\
        --tree-init .../FL_FVS_TREEINIT_PLOT.csv \\
        --out-dir data/interim/fvs_inputs/12125
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

MIN_PLT_WEIGHT = 0.05          # LETO: keep PLT_CNs contributing >= 5% of a unit
STAND_PREFIX = "MU_"
TREE_STAND_KEY = "STAND_CN"    # FVS TreeInit/StandInit key == PLT_CN
TREE_COUNT_COL = "TREE_COUNT"  # FVS trees-per-acre column scaled by plot weight


def filter_and_renormalize_weights(weights: pd.DataFrame, min_weight: float = MIN_PLT_WEIGHT) -> pd.DataFrame:
    """Drop PLT_CNs below ``min_weight`` within a unit, then renormalise weights to sum to 1."""
    w = weights.copy()
    w["MU_ID"] = w["MU_ID"].astype(str)
    w["PLT_CN"] = w["PLT_CN"].astype(str)
    w["WEIGHT"] = pd.to_numeric(w["WEIGHT"], errors="coerce")
    w = w[w["WEIGHT"] >= min_weight].copy()
    totals = w.groupby("MU_ID")["WEIGHT"].transform("sum")
    w["WEIGHT"] = w["WEIGHT"] / totals
    return w.reset_index(drop=True)


def build_tree_init(
    weights: pd.DataFrame,
    tree_init: pd.DataFrame,
    min_weight: float = MIN_PLT_WEIGHT,
    stand_prefix: str = STAND_PREFIX,
    stand_key: str = TREE_STAND_KEY,
    count_col: str = TREE_COUNT_COL,
) -> tuple[pd.DataFrame, set[str]]:
    """
    Join weighted plots to per-plot trees and scale each tree's TPA by its plot weight.

    Returns ``(tree_final, runnable_mu_ids)`` where ``tree_final`` has one row per
    (unit × donor tree) with ``STAND_ID = "MU_<MU_ID>"`` and a scaled ``TREE_COUNT``, and
    ``runnable_mu_ids`` is the set of units that received at least one live tree.
    """
    w = filter_and_renormalize_weights(weights, min_weight)
    trees = tree_init.copy()
    trees[stand_key] = trees[stand_key].astype(str)

    merged = w.merge(trees, left_on="PLT_CN", right_on=stand_key, how="inner")
    if count_col in merged.columns:
        merged[count_col] = pd.to_numeric(merged[count_col], errors="coerce") * merged["WEIGHT"]
        merged = merged[merged[count_col] > 0]

    merged["STAND_ID"] = stand_prefix + merged["MU_ID"].astype(str)
    merged["TREE_SOURCE"] = "FIA_WEIGHTED_DIRECT"
    merged["DONOR_STAND_ID"] = ""
    merged["NEAR_DIST"] = pd.NA

    runnable = set(merged["MU_ID"].astype(str))
    logger.info("TreeInit: %d units runnable, %d tree rows", len(runnable), len(merged))
    return merged.reset_index(drop=True), runnable


def build_stand_init(
    unit_attrs: pd.DataFrame,
    runnable_mu_ids: set[str],
    stand_prefix: str = STAND_PREFIX,
    variant: str = "SN",
    inv_year: int = 2022,
    state: str = "FL",
) -> pd.DataFrame:
    """One StandInit row per runnable unit, carrying FVS bookkeeping + unit attributes."""
    df = unit_attrs.copy()
    df["MU_ID"] = df["MU_ID"].astype(str)
    df = df[df["MU_ID"].isin({str(m) for m in runnable_mu_ids})].copy()
    df["STAND_ID"] = stand_prefix + df["MU_ID"]
    df["VARIANT"] = variant
    df["INV_YEAR"] = inv_year
    df["STATE"] = state
    lead = ["STAND_ID", "VARIANT", "INV_YEAR", "STATE", "MU_ID"]
    cols = lead + [c for c in df.columns if c not in lead]
    return df[cols].reset_index(drop=True)


def impute_nearest_runnable(
    units_gdf,
    tree_final: pd.DataFrame,
    runnable_mu_ids: set[str],
    id_field: str = "MU_ID",
    stand_prefix: str = STAND_PREFIX,
):
    """
    Give every non-runnable unit the tree list of its nearest runnable unit (by geometry),
    relabelled to the recipient. Mirrors LETO's ``GenerateNearTable`` nearest-runnable step.
    """
    import numpy as np
    import shapely

    gdf = units_gdf.copy()
    gdf[id_field] = gdf[id_field].astype(str)
    runnable = {str(m) for m in runnable_mu_ids}

    is_runnable = gdf[id_field].isin(runnable).to_numpy()
    missing_pos = np.where(~is_runnable)[0]
    runnable_pos = np.where(is_runnable)[0]
    if len(missing_pos) == 0 or len(runnable_pos) == 0:
        return tree_final.reset_index(drop=True)

    geoms = gdf.geometry.to_numpy()
    ids = gdf[id_field].to_numpy()
    tree = shapely.STRtree(geoms[runnable_pos])
    nearest_local = tree.nearest(geoms[missing_pos])
    dists = shapely.distance(geoms[missing_pos], geoms[runnable_pos][nearest_local])

    trees_by_stand = {sid: df for sid, df in tree_final.groupby("STAND_ID")}
    imputed = []
    for k, mpos in enumerate(missing_pos):
        recipient = str(ids[mpos])
        donor = str(ids[runnable_pos[nearest_local[k]]])
        donor_trees = trees_by_stand.get(stand_prefix + donor)
        if donor_trees is None or donor_trees.empty:
            continue
        rows = donor_trees.copy()
        rows["STAND_ID"] = stand_prefix + recipient
        rows["MU_ID"] = recipient
        rows["DONOR_STAND_ID"] = stand_prefix + donor
        rows["TREE_SOURCE"] = "IMPUTED_NEAREST"
        rows["NEAR_DIST"] = float(dists[k])
        imputed.append(rows)

    logger.info("Imputed tree lists for %d of %d non-runnable units", len(imputed), len(missing_pos))
    if imputed:
        return pd.concat([tree_final, *imputed], ignore_index=True)
    return tree_final.reset_index(drop=True)


def build_fvs_inputs(
    units_gdf,
    weights: pd.DataFrame,
    tree_init: pd.DataFrame,
    id_field: str = "MU_ID",
    min_weight: float = MIN_PLT_WEIGHT,
    impute: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """End-to-end: weighted trees → StandInit + TreeInit (with nearest imputation)."""
    units = units_gdf.copy()
    units[id_field] = units[id_field].astype(str)

    tree_final, runnable = build_tree_init(weights, tree_init, min_weight=min_weight)
    if impute:
        tree_final = impute_nearest_runnable(units, tree_final, runnable, id_field=id_field)
    covered = set(tree_final["MU_ID"].astype(str))

    attrs = pd.DataFrame(units.drop(columns=units.geometry.name))
    stand_final = build_stand_init(attrs, covered)
    logger.info("FVS inputs: %d stands, %d tree rows (%d units total)",
                len(stand_final), len(tree_final), units[id_field].nunique())
    return stand_final, tree_final


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="Build FVS StandInit/TreeInit for management units")
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True, help="MU_PLT_CN_Weights.csv")
    parser.add_argument("--tree-init", type=Path, required=True, help="FL_FVS_TREEINIT_PLOT.csv")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--min-weight", type=float, default=MIN_PLT_WEIGHT)
    parser.add_argument("--no-impute", action="store_true")
    parser.add_argument("--id-field", type=str, default="MU_ID")
    args = parser.parse_args()

    import geopandas as gpd

    units = gpd.read_file(args.units)
    weights = pd.read_csv(args.weights, dtype={"MU_ID": str, "PLT_CN": str})
    tree_init = pd.read_csv(args.tree_init, dtype={TREE_STAND_KEY: str}, low_memory=False)

    stand_final, tree_final = build_fvs_inputs(
        units, weights, tree_init, id_field=args.id_field,
        min_weight=args.min_weight, impute=not args.no_impute,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stand_final.to_csv(args.out_dir / "FVS_StandInit.csv", index=False)
    tree_final.to_csv(args.out_dir / "FVS_TreeInit.csv", index=False)
    logger.info("Wrote FVS_StandInit.csv (%d) and FVS_TreeInit.csv (%d) to %s",
                len(stand_final), len(tree_final), args.out_dir)


if __name__ == "__main__":
    main()
