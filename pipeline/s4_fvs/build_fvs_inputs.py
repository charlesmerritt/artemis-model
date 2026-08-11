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
    4. Give every remaining unit a tree list by walking the **initialization ladder** in
       `config/fallback_treelists.yaml`.

Step 4 is LETO's ``GenerateNearTable`` nearest-runnable step, bounded. LETO takes the
nearest runnable unit at any distance and with no forest-type test, which lets a bottomland
hardwood unit inherit a pine plantation from 40 km away with nothing in the output
recording it. The ladder keeps that behaviour where it is defensible and replaces it where
it is not:

    1. nearest runnable unit of the same forest-type group, within 5 km
    2. nearest runnable unit of any type, within 2 km
    3. a pinned fixed tree list chosen by forest type
    4. the default fixed tree list

Every tree row carries the rung that produced it (``TREE_SOURCE``, ``FALLBACK_SLOT``,
``NEAR_DIST``), and :func:`summarize_tree_sources` produces the three reporting cuts the
config requires. A landscape where 8% of the acres came from a fixed list is a different
result from one where 0.3% did.

:func:`ladder_decisions` is the measurement tool: it reports which rung every unit lands on
using geometry and config alone, with no resolved lock file needed — which is how to answer
the open "hole prevalence is unmeasured" question before committing to donor plots.

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

from pipeline.ids import as_id_series, read_id_csv, report_key_overlap
from pipeline.s4_fvs.fallback_treelists import (
    SOURCE_DIRECT,
    SOURCE_FALLBACK,
    forest_type_group,
    load_fallback_policy,
    ladder_type_key,
    plt_cn_for_slot,
    resolve_initialization,
)
from pipeline.spatial_ref import assert_project_crs, to_project_crs

logger = logging.getLogger(__name__)

MIN_PLT_WEIGHT = 0.05          # LETO: keep PLT_CNs contributing >= 5% of a unit
STAND_PREFIX = "MU_"
TREE_STAND_KEY = "STAND_CN"    # FVS TreeInit/StandInit key == PLT_CN
TREE_COUNT_COL = "TREE_COUNT"  # FVS trees-per-acre column scaled by plot weight

# Unit columns that may carry a FIA forest type, in priority order.
_FOREST_TYPE_FIELDS = ("FORTYPCD", "forest_type_code", "FOREST_TYPE", "fortypcd")


def filter_and_renormalize_weights(weights: pd.DataFrame, min_weight: float = MIN_PLT_WEIGHT) -> pd.DataFrame:
    """Drop PLT_CNs below ``min_weight`` within a unit, then renormalise weights to sum to 1."""
    w = weights.copy()
    # Both are join keys; see ``pipeline.ids`` for why ``.astype(str)`` is wrong here.
    w["MU_ID"] = as_id_series(w["MU_ID"], column="MU_ID")
    w["PLT_CN"] = as_id_series(w["PLT_CN"], column="PLT_CN")
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
    # STAND_CN is a FIA control number. If the tree list was read without a pinned dtype it
    # arrives as float64 and ``.astype(str)`` would yield "2.36048879010661e+14", matching
    # no PLT_CN at all — a join that returns zero rows and looks like a clean run.
    trees[stand_key] = as_id_series(trees[stand_key], column=stand_key)

    report_key_overlap(w["PLT_CN"], trees[stand_key],
                       left_name="weighted PLT_CN", right_name=f"tree-list {stand_key}")

    merged = w.merge(trees, left_on="PLT_CN", right_on=stand_key, how="inner")
    if count_col in merged.columns:
        merged[count_col] = pd.to_numeric(merged[count_col], errors="coerce") * merged["WEIGHT"]
        merged = merged[merged[count_col] > 0]

    merged["STAND_ID"] = stand_prefix + merged["MU_ID"]
    merged["TREE_SOURCE"] = SOURCE_DIRECT
    merged["DONOR_STAND_ID"] = ""
    merged["NEAR_DIST"] = pd.NA
    merged["FALLBACK_SLOT"] = pd.NA

    runnable = set(merged["MU_ID"].dropna())
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
    df["MU_ID"] = as_id_series(df["MU_ID"], column="MU_ID")
    df = df[df["MU_ID"].isin(set(as_id_series(list(runnable_mu_ids), column="MU_ID")))].copy()
    df["STAND_ID"] = stand_prefix + df["MU_ID"]
    df["VARIANT"] = variant
    df["INV_YEAR"] = inv_year
    df["STATE"] = state
    lead = ["STAND_ID", "VARIANT", "INV_YEAR", "STATE", "MU_ID"]
    cols = lead + [c for c in df.columns if c not in lead]
    return df[cols].reset_index(drop=True)


def _forest_type_field(gdf) -> str | None:
    """The unit column carrying a FIA forest type, if any."""
    return next((f for f in _FOREST_TYPE_FIELDS if f in gdf.columns), None)


def _nearest_within(geoms, ids, source_pos, target_pos) -> dict[int, tuple[str, float]]:
    """Nearest ``source`` unit for each ``target``, as ``{target_pos: (source_id, distance)}``."""
    import shapely

    if len(source_pos) == 0 or len(target_pos) == 0:
        return {}
    tree = shapely.STRtree(geoms[source_pos])
    nearest_local = tree.nearest(geoms[target_pos])
    distances = shapely.distance(geoms[target_pos], geoms[source_pos][nearest_local])
    return {
        int(pos): (str(ids[source_pos[nearest_local[k]]]), float(distances[k]))
        for k, pos in enumerate(target_pos)
    }


def ladder_decisions(
    units_gdf,
    runnable_mu_ids: set[str],
    id_field: str = "MU_ID",
    forest_type_field: str | None = None,
    policy: dict | None = None,
) -> pd.DataFrame:
    """
    Which rung of the initialization ladder each non-runnable unit lands on.

    Pure geometry plus config — it needs no resolved lock file and no tree table, so it can
    be run before any donor plots are pinned. That makes it the measurement tool for the
    open question in `config/fallback_treelists.yaml`: how much area actually falls to a
    fixed list rather than to a real neighbour.

    Returns one row per non-runnable unit with ``MU_ID, rung, method, tree_source, slot,
    donor_id, donor_distance_m, same_forest_type``. Units whose forest type is unknown can
    never satisfy the same-type rung, so they fall to the tighter any-type radius — a
    deliberate degradation, not an oversight.

    The units must be in the project CRS. The ladder's 5 km and 2 km bounds are distances
    in metres, so a units file in degrees would report every donor as well under a
    kilometre and the bound would never fire — the stand would silently take a donor from
    the far side of the state instead of falling through to a fixed list. Before the bounds
    existed this was cosmetic, because the distance was only recorded; now it decides.
    """
    import numpy as np

    assert_project_crs(units_gdf, context="ladder_decisions: donor distances are in metres")

    policy = policy or load_fallback_policy()
    gdf = units_gdf.copy()
    gdf[id_field] = as_id_series(gdf[id_field], column=id_field)
    runnable = set(as_id_series(list(runnable_mu_ids), column=id_field))

    is_runnable = gdf[id_field].isin(runnable).to_numpy()
    missing_pos = np.where(~is_runnable)[0]
    runnable_pos = np.where(is_runnable)[0]

    columns = ["MU_ID", "fortypcd", "rung", "method", "tree_source", "slot", "donor_id",
               "donor_distance_m", "same_forest_type"]
    if len(missing_pos) == 0:
        return pd.DataFrame(columns=columns)

    geoms = gdf.geometry.to_numpy()
    ids = gdf[id_field].to_numpy()

    ft_field = forest_type_field or _forest_type_field(gdf)
    fortypcds = gdf[ft_field].to_numpy() if ft_field else np.full(len(gdf), None)
    groups = np.array([forest_type_group(c, policy) for c in fortypcds], dtype=object)

    any_donor = _nearest_within(geoms, ids, runnable_pos, missing_pos)
    same_donor: dict[int, tuple[str, float]] = {}
    for group in {g for g in groups[runnable_pos] if g is not None}:
        sources = runnable_pos[groups[runnable_pos] == group]
        targets = missing_pos[groups[missing_pos] == group]
        same_donor.update(_nearest_within(geoms, ids, sources, targets))

    rows = []
    for pos in missing_pos:
        pos = int(pos)
        fortypcd = fortypcds[pos]
        decision, donor_id, donor_distance, same_type = None, None, None, False

        # Rung 1 wants the same-type donor; rung 2 wants the nearest of any type. Ask the
        # resolver about each candidate and take the first that it accepts.
        if pos in same_donor:
            candidate_id, candidate_distance = same_donor[pos]
            attempt = resolve_initialization(
                fortypcd=fortypcd, donor_distance_m=candidate_distance,
                donor_same_forest_type=True, policy=policy,
            )
            if attempt.method == "nearest_runnable_unit":
                decision, donor_id, donor_distance, same_type = (
                    attempt, candidate_id, candidate_distance, True
                )
        if decision is None and pos in any_donor:
            candidate_id, candidate_distance = any_donor[pos]
            attempt = resolve_initialization(
                fortypcd=fortypcd, donor_distance_m=candidate_distance,
                donor_same_forest_type=False, policy=policy,
            )
            if attempt.method == "nearest_runnable_unit":
                decision, donor_id, donor_distance = attempt, candidate_id, candidate_distance
        if decision is None:
            decision = resolve_initialization(fortypcd=fortypcd, policy=policy)

        rows.append({
            "MU_ID": str(ids[pos]), "fortypcd": fortypcd,
            "rung": decision.rung, "method": decision.method,
            "tree_source": decision.tree_source, "slot": decision.slot,
            "donor_id": donor_id, "donor_distance_m": donor_distance,
            "same_forest_type": same_type,
        })

    return pd.DataFrame(rows, columns=columns)


def _relabel(rows: pd.DataFrame, mu_id: str, stand_prefix: str, tree_source: str) -> pd.DataFrame:
    """Point a block of donor tree rows at its recipient unit and tag its provenance."""
    rows["STAND_ID"] = stand_prefix + mu_id
    rows["MU_ID"] = mu_id
    rows["TREE_SOURCE"] = tree_source
    return rows


def _fallback_slot_resolver(policy: dict):
    """A ``(mu_id, decisions) -> slot`` function for units rerouted off a dead donor rung.

    Reuses the ladder's own forest-type mapping, so a rerouted unit lands on the same fixed
    slot it would have reached had no donor been found at all — rather than on some
    separate rule that could drift from the ladder.
    """
    ladder = {rung["rung"]: rung for rung in policy["initialization_ladder"]}
    by_type = next((r for r in ladder.values() if r["method"] == "fallback_slot" and "mapping" in r), None)
    default = next(r for r in reversed(policy["initialization_ladder"])
                   if r["method"] == "fallback_slot" and r.get("slot"))

    def resolve(mu_id: str, decisions: pd.DataFrame) -> str:
        row = decisions[decisions["MU_ID"] == mu_id]
        key = None
        if by_type is not None and not row.empty:
            key = ladder_type_key(row.iloc[0].get("fortypcd"), policy)
        if by_type is not None and key is not None:
            return by_type["mapping"].get(key, default["slot"])
        return default["slot"]

    return resolve


def impute_nearest_runnable(
    units_gdf,
    tree_final: pd.DataFrame,
    runnable_mu_ids: set[str],
    id_field: str = "MU_ID",
    stand_prefix: str = STAND_PREFIX,
    *,
    tree_init: pd.DataFrame | None = None,
    forest_type_field: str | None = None,
    policy: dict | None = None,
    on_missing_fallback: str = "raise",
    stand_key: str = TREE_STAND_KEY,
):
    """
    Give every non-runnable unit a tree list, following the initialization ladder.

    Donor rungs copy a neighbouring unit's list, relabelled to the recipient (LETO's
    ``GenerateNearTable`` behaviour, now bounded by distance and forest type). Fixed-list
    rungs pull the slot's pinned donor plot out of ``tree_init``.

    ``on_missing_fallback`` decides what happens when a unit needs a fixed list that cannot
    be produced — the slots are unresolved, or ``tree_init`` was not supplied:

        ``"raise"`` (default) — fail, per the config's stated position that substituting an
            arbitrary tree list for a missing pin would be invisible downstream.
        ``"skip"`` — leave those units without trees and log the count per slot. This is the
            measurement mode: it answers how much of the landscape needs fixed lists before
            anyone commits to donor plots. Units skipped here get no StandInit row, so they
            are absent from the run rather than silently wrong.
    """
    if on_missing_fallback not in {"raise", "skip"}:
        raise ValueError(f"on_missing_fallback must be 'raise' or 'skip', got {on_missing_fallback!r}")

    policy = policy or load_fallback_policy()
    decisions = ladder_decisions(
        units_gdf, runnable_mu_ids, id_field=id_field,
        forest_type_field=forest_type_field, policy=policy,
    )
    if decisions.empty:
        return tree_final.reset_index(drop=True)

    trees_by_stand = {sid: df for sid, df in tree_final.groupby("STAND_ID")}

    # Normalise the per-plot table once. Doing it inside the loop copies the whole statewide
    # tree table per gap unit, which is O(units x table rows) the moment slots are pinned.
    trees_by_plt: dict[str, pd.DataFrame] = {}
    if tree_init is not None:
        source = tree_init.copy()
        source[stand_key] = source[stand_key].astype(str)
        trees_by_plt = {plt: df for plt, df in source.groupby(stand_key)}

    fallback_slot_for = _fallback_slot_resolver(policy)
    imputed, skipped, rerouted = [], {}, 0

    for row in decisions.itertuples(index=False):
        slot, tree_source = row.slot, row.tree_source

        if row.method == "nearest_runnable_unit":
            donor_trees = trees_by_stand.get(stand_prefix + row.donor_id)
            if donor_trees is None or donor_trees.empty:
                # The donor rung matched a unit that turned out to have no tree rows. Fall
                # through to the fixed-list rungs, which exist for exactly this situation,
                # rather than dropping the unit — a dropped unit leaves no row to carry
                # provenance, which is the one thing this module promises.
                slot = fallback_slot_for(row.MU_ID, decisions)
                tree_source = SOURCE_FALLBACK
                rerouted += 1
            else:
                rows = donor_trees.copy()
                rows["DONOR_STAND_ID"] = stand_prefix + row.donor_id
                rows["NEAR_DIST"] = row.donor_distance_m
                rows["FALLBACK_SLOT"] = pd.NA
                imputed.append(_relabel(rows, row.MU_ID, stand_prefix, tree_source))
                continue

        try:
            plt_cn = plt_cn_for_slot(slot, policy)
            if not trees_by_plt:
                raise RuntimeError(
                    f"unit {row.MU_ID} needs fixed tree list {slot!r} but no per-plot tree "
                    f"table was supplied to pull PLT_CN {plt_cn} from"
                )
            rows = trees_by_plt.get(plt_cn)
            if rows is None or rows.empty:
                raise RuntimeError(
                    f"fixed tree list {slot!r} is pinned to PLT_CN {plt_cn}, which "
                    f"is not present in the supplied tree table"
                )
            rows = rows.copy()
        except RuntimeError:
            if on_missing_fallback == "raise":
                raise
            skipped[slot] = skipped.get(slot, 0) + 1
            continue

        rows["DONOR_STAND_ID"] = ""
        rows["NEAR_DIST"] = pd.NA
        rows["FALLBACK_SLOT"] = slot
        imputed.append(_relabel(rows, row.MU_ID, stand_prefix, tree_source))

    by_rung = decisions["rung"].value_counts().to_dict()
    logger.info("Initialization ladder over %d non-runnable units: %s", len(decisions), by_rung)
    if rerouted:
        logger.warning("%d units matched a donor with no tree rows and fell through to a "
                       "fixed list", rerouted)
    if skipped:
        logger.warning(
            "%d units left without trees — fixed lists unavailable per slot: %s. "
            "Resolve with `fallback_treelists --resolve`.",
            sum(skipped.values()), skipped,
        )

    if imputed:
        return pd.concat([tree_final, *imputed], ignore_index=True)
    return tree_final.reset_index(drop=True)


def summarize_tree_sources(tree_final: pd.DataFrame, units_gdf=None, id_field: str = "MU_ID",
                           area_field: str = "unit_area_ha") -> dict[str, pd.DataFrame]:
    """
    The three reporting cuts `config/fallback_treelists.yaml` requires of any FVS result.

    Returns ``{"by_source", "donor_distance", "by_slot"}``: area and share of the landscape
    by ``TREE_SOURCE``, the donor-distance distribution with the share over 2 km, and area
    per fixed slot. Falls back to unit counts when ``units_gdf`` carries no area column,
    since a count is still a check even where acres are not available.
    """
    per_unit = tree_final.drop_duplicates(subset=[id_field])[
        [c for c in [id_field, "TREE_SOURCE", "FALLBACK_SLOT", "NEAR_DIST"] if c in tree_final.columns]
    ].copy()

    weight = "units"
    if units_gdf is not None:
        attrs = units_gdf
        if hasattr(attrs, "geometry"):
            attrs = attrs.drop(columns=attrs.geometry.name)
        attrs = pd.DataFrame(attrs).copy()
        attrs[id_field] = attrs[id_field].astype(str)
        if area_field in attrs.columns:
            per_unit = per_unit.merge(attrs[[id_field, area_field]], on=id_field, how="left")
            weight = area_field
    if weight == "units":
        per_unit["units"] = 1.0

    by_source = per_unit.groupby("TREE_SOURCE", dropna=False)[weight].sum().reset_index(name=weight)
    by_source["share"] = by_source[weight] / by_source[weight].sum()

    distances = per_unit["NEAR_DIST"].dropna() if "NEAR_DIST" in per_unit.columns else pd.Series(dtype=float)
    distances = pd.to_numeric(distances, errors="coerce").dropna()
    donor_distance = pd.DataFrame([{
        "n_units": int(len(distances)),
        "median_m": float(distances.median()) if len(distances) else None,
        "max_m": float(distances.max()) if len(distances) else None,
        "share_over_2km": float((distances > 2000).mean()) if len(distances) else None,
    }])

    if "FALLBACK_SLOT" in per_unit.columns:
        by_slot = (per_unit.dropna(subset=["FALLBACK_SLOT"])
                   .groupby("FALLBACK_SLOT")[weight].sum().reset_index(name=weight))
    else:
        by_slot = pd.DataFrame(columns=["FALLBACK_SLOT", weight])

    return {"by_source": by_source, "donor_distance": donor_distance, "by_slot": by_slot}


def build_fvs_inputs(
    units_gdf,
    weights: pd.DataFrame,
    tree_init: pd.DataFrame,
    id_field: str = "MU_ID",
    min_weight: float = MIN_PLT_WEIGHT,
    impute: bool = True,
    on_missing_fallback: str = "raise",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """End-to-end: weighted trees → StandInit + TreeInit, gaps filled via the ladder."""
    units = units_gdf.copy()
    # The units layer is the other side of the MU_ID join. A GeoPackage stores this field
    # as REAL whenever any row is NULL, so reading it back can hand us 1.0 where the
    # weights table holds "1"; normalising both sides is what keeps them joinable.
    units[id_field] = as_id_series(units[id_field], column=id_field)

    tree_final, runnable = build_tree_init(weights, tree_init, min_weight=min_weight)
    # Compare against the normalised MU_ID, not the caller's raw column: the join itself
    # runs on the normalised copy inside filter_and_renormalize_weights, so diffing the raw
    # one here would manufacture a "no key matched" warning for a join that is fine.
    report_key_overlap(units[id_field], as_id_series(weights["MU_ID"], column="MU_ID"),
                       left_name=f"units {id_field}", right_name="weights MU_ID")
    if impute:
        tree_final = impute_nearest_runnable(
            units, tree_final, runnable, id_field=id_field,
            tree_init=tree_init, on_missing_fallback=on_missing_fallback,
        )
    covered = set(as_id_series(tree_final["MU_ID"], column="MU_ID").dropna())

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
    parser.add_argument("--ladder-report", action="store_true",
                        help="Report which initialization rung each gap unit lands on, then "
                             "exit. Needs no resolved fallback slots — use this to measure "
                             "how much area depends on fixed tree lists.")
    parser.add_argument("--allow-unresolved-fallback", action="store_true",
                        help="Leave units without trees when their fixed list is unavailable, "
                             "instead of failing. Those units are absent from the run.")
    args = parser.parse_args()

    import geopandas as gpd

    # Reproject rather than assert: a units file in another CRS is a common, recoverable
    # input, and the ladder's distance bounds are only meaningful in metres.
    units = to_project_crs(gpd.read_file(args.units))
    # read_id_csv pins every identifier column to text and validates it at the read, so a
    # control number that a producing step wrote from a double fails here rather than
    # quietly dropping stands out of the join.
    weights = read_id_csv(args.weights)
    tree_init = read_id_csv(args.tree_init, low_memory=False)

    if args.ladder_report:
        _, runnable = build_tree_init(weights, tree_init, min_weight=args.min_weight)
        decisions = ladder_decisions(units, runnable, id_field=args.id_field)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        out = args.out_dir / "initialization_ladder.csv"
        decisions.to_csv(out, index=False)
        total = units[args.id_field].nunique()
        print(f"{len(decisions)} of {total} units need initialization from elsewhere\n")
        print(decisions.groupby(["rung", "tree_source"]).size().to_string())
        logger.info("Wrote %s", out)
        return

    stand_final, tree_final = build_fvs_inputs(
        units, weights, tree_init, id_field=args.id_field,
        min_weight=args.min_weight, impute=not args.no_impute,
        on_missing_fallback="skip" if args.allow_unresolved_fallback else "raise",
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stand_final.to_csv(args.out_dir / "FVS_StandInit.csv", index=False)
    tree_final.to_csv(args.out_dir / "FVS_TreeInit.csv", index=False)

    # Provenance is not optional: an FVS result without these cuts is not reportable.
    for name, table in summarize_tree_sources(tree_final, units, id_field=args.id_field).items():
        table.to_csv(args.out_dir / f"provenance_{name}.csv", index=False)
        print(f"\n-- {name} --\n{table.to_string(index=False)}")

    logger.info("Wrote FVS_StandInit.csv (%d) and FVS_TreeInit.csv (%d) to %s",
                len(stand_final), len(tree_final), args.out_dir)


if __name__ == "__main__":
    main()
