"""LETO stage 4: the weighted FVS tree list built from live FIA trees.

Ports aauslander480/Leto `LETO_v1_project/leto/stage4_inventory.py` (`build_tree_list`):

- Every live FIA TREE row (`STATUSCD == 1`) on a retained donor plot, across all of the
  plot's conditions; `TREE_COUNT = TPA_UNADJ x WEIGHT`.
- SPCD is zero-padded to three digits and mapped through the FVS species crosswalk.
- HT falls back to ACTUALHT; CR becomes CRRATIO.
- Rows missing SPECIES, DIAMETER or TREE_COUNT, or with TREE_COUNT <= 0, are dropped.
- TREE_ID is sequential within each stand after sorting on SEG_ID, PLT_CN, TREE_ID_FIA.

`impute_missing_tree_lists` ports the nearest-stand imputation: a stand with no usable live
trees copies the whole tree list of the nearest runnable stand. LETO measures distance with
ArcGIS `GenerateNearTable` (PLANAR, closest 1, no radius, any owner or class); here it is the
shapely polygon edge distance. ArcGIS leaves ties unspecified, and adjacent stands always tie
at 0, so ties break on the lowest donor SEG_ID.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
from shapely import STRtree

from pipeline import leto_imputation_qa as imputation_qa
from pipeline.ids import as_id_series
from pipeline.leto_donors import MINIMUM_PLOT_WEIGHT, filter_weights

LIVE_TREE_STATUS_CODE = 1
WEIGHT_COLUMNS = ("STAND_ID", "SEG_ID", "PLT_CN", "WEIGHT", "WEIGHT_ORIGINAL")
TREE_COLUMNS = [
    "STAND_ID", "TREE_ID", "SPECIES", "DIAMETER", "HT", "CRRATIO", "TREE_COUNT", "MU_ID",
    "SEG_ID", "PLT_CN", "WEIGHT", "WEIGHT_ORIGINAL", "Species_FIA", "TREE_ID_FIA",
    "TREE_SOURCE", "DONOR_STAND_ID", "NEAR_DIST",
]
MISSING_SPECIES_COLUMNS = [
    "Species_FIA", "Tree_Records", "Weighted_TPA", "Mean_Diameter", "Mean_Height",
    "Donor_Plots", "Stands",
]
MISSING_BY_STAND_COLUMNS = [
    "STAND_ID", "Missing_TPA", "Missing_Trees", "Missing_Species", "Percent_Stand_TPA_Missing",
]


class TreeSource(StrEnum):
    FIA_WEIGHTED_DIRECT = "FIA_WEIGHTED_DIRECT"
    IMPUTED_NEAREST = "IMPUTED_NEAREST"


@dataclass(frozen=True)
class TreeList:
    """The direct tree list plus LETO's missing-species QA tables and metrics."""

    trees: pd.DataFrame
    missing_species: pd.DataFrame
    missing_species_by_stand: pd.DataFrame
    metrics: dict = field(default_factory=dict)


def species_key(spcd: pd.Series) -> pd.Series:
    """FIA SPCD as the crosswalk's three-digit key ("68" -> "068")."""
    return (pd.to_numeric(spcd, errors="coerce").astype("Int64").astype("string")
            .str.zfill(3))


def build_tree_list(weights: pd.DataFrame, fia_trees: pd.DataFrame,
                    species_lookup: Mapping[str, str]) -> TreeList:
    """Join retained donor weights to live FIA trees and emit LETO's FVS tree rows."""
    missing = [c for c in WEIGHT_COLUMNS if c not in weights.columns]
    if missing:
        raise ValueError(f"Donor weights missing columns: {missing}")
    w = weights.copy()
    w["PLT_CN"] = as_id_series(w["PLT_CN"], column="PLT_CN")
    trees = fia_trees.copy()
    trees["PLT_CN"] = as_id_series(trees["PLT_CN"], column="PLT_CN")
    trees = trees[trees["PLT_CN"].isin(set(w["PLT_CN"].dropna()))]

    joined = w.merge(trees, on="PLT_CN", how="inner", validate="many_to_many")
    joined = joined[pd.to_numeric(joined["STATUSCD"], errors="coerce")
                    == LIVE_TREE_STATUS_CODE].copy()
    joined["Species_FIA"] = joined["SPCD"]
    joined["SPECIES"] = species_key(joined["SPCD"]).map(species_lookup)
    for column in ["DIA", "HT", "ACTUALHT", "CR", "TPA_UNADJ", "WEIGHT", "WEIGHT_ORIGINAL"]:
        if column in joined.columns:
            joined[column] = pd.to_numeric(joined[column], errors="coerce")
    joined["DIAMETER"] = joined["DIA"]
    joined["CRRATIO"] = joined["CR"]
    joined["TREE_COUNT"] = joined["TPA_UNADJ"] * joined["WEIGHT"]
    if "HT" not in joined.columns:
        joined["HT"] = np.nan
    if "ACTUALHT" in joined.columns:
        joined["HT"] = joined["HT"].fillna(joined["ACTUALHT"])
    joined["TREE_ID_FIA"] = as_id_series(joined["CN"], column="CN")

    unmapped = joined[joined["SPECIES"].isna()]
    total_tpa = float(joined["TREE_COUNT"].sum())
    missing_tpa = float(unmapped["TREE_COUNT"].sum()) if not unmapped.empty else 0.0
    missing_pct = 100.0 * missing_tpa / total_tpa if total_tpa > 0 else 0.0
    by_species, by_stand = _missing_species_qa(unmapped, joined)

    usable = joined.dropna(
        subset=["STAND_ID", "SEG_ID", "PLT_CN", "SPECIES", "DIAMETER", "TREE_COUNT"])
    usable = usable[usable["TREE_COUNT"] > 0].copy()
    usable["_SEG_ORDER"] = pd.to_numeric(usable["SEG_ID"], errors="raise")
    usable = (usable.sort_values(["_SEG_ORDER", "PLT_CN", "TREE_ID_FIA"])
              .drop(columns="_SEG_ORDER").reset_index(drop=True))
    usable["TREE_ID"] = usable.groupby("STAND_ID").cumcount() + 1
    usable["MU_ID"] = usable["SEG_ID"].astype(str)
    usable["TREE_SOURCE"] = TreeSource.FIA_WEIGHTED_DIRECT
    usable["DONOR_STAND_ID"] = ""
    usable["NEAR_DIST"] = np.nan
    final = usable[TREE_COLUMNS].copy()

    metrics = {
        "fia_tree_records_matched": int(len(trees)),
        "fia_donor_plots_matched": int(trees["PLT_CN"].nunique()),
        "untranslated_live_tree_records": int(len(unmapped)),
        "untranslated_fia_species": int(unmapped["Species_FIA"].nunique()),
        "weighted_tpa_before_species_filter": total_tpa,
        "missing_weighted_tpa": missing_tpa,
        "missing_weighted_tpa_percent": missing_pct,
        "usable_weighted_tree_records": int(len(final)),
        "direct_stands": int(final["STAND_ID"].nunique()),
    }
    return TreeList(final, by_species, by_stand, metrics)


def _missing_species_qa(unmapped: pd.DataFrame,
                        joined: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """LETO's QA_Missing_FVS_Species.csv and QA_Missing_FVS_Species_By_Stand.csv."""
    if unmapped.empty:
        return (pd.DataFrame(columns=MISSING_SPECIES_COLUMNS),
                pd.DataFrame(columns=MISSING_BY_STAND_COLUMNS))
    by_species = (
        unmapped.groupby("Species_FIA", dropna=False)
        .agg(Tree_Records=("Species_FIA", "size"), Weighted_TPA=("TREE_COUNT", "sum"),
             Mean_Diameter=("DIAMETER", "mean"), Mean_Height=("HT", "mean"),
             Donor_Plots=("PLT_CN", "nunique"), Stands=("SEG_ID", "nunique"))
        .reset_index().sort_values("Weighted_TPA", ascending=False).reset_index(drop=True))
    by_stand = (
        unmapped.groupby("STAND_ID")
        .agg(Missing_TPA=("TREE_COUNT", "sum"), Missing_Trees=("TREE_COUNT", "size"),
             Missing_Species=("Species_FIA", "nunique"))
        .reset_index().sort_values("Missing_TPA", ascending=False).reset_index(drop=True))
    stand_tpa = joined.groupby("STAND_ID")["TREE_COUNT"].sum()
    by_stand["Percent_Stand_TPA_Missing"] = (
        by_stand["Missing_TPA"] / stand_tpa.reindex(by_stand["STAND_ID"]).to_numpy() * 100.0)
    return by_species, by_stand


def impute_missing_tree_lists(stands, tree_list: pd.DataFrame
                              ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Give each tree-less stand its nearest runnable stand's trees; return (trees, still missing).

    ``stands`` is a GeoDataFrame with STAND_ID, SEG_ID and a projected geometry.
    """
    direct_ids = set(as_id_series(tree_list["STAND_ID"]))
    is_direct = as_id_series(stands["STAND_ID"]).isin(direct_ids)
    missing = stands[~is_direct]
    if missing.empty:
        return tree_list.reset_index(drop=True), missing.copy()
    if stands.crs is None or stands.crs.is_geographic:
        raise ValueError("Nearest-stand imputation requires a projected CRS")
    donors = stands[is_direct].sort_values("SEG_ID").reset_index(drop=True)
    if donors.empty:
        raise ValueError("No runnable stands are available for nearest-stand imputation")

    index = STRtree(donors.geometry.to_numpy())
    imputed = []
    for stand in missing.itertuples(index=False):
        positions, distances = index.query_nearest(stand.geometry, all_matches=True,
                                                   return_distance=True)
        # donors are sorted by SEG_ID, so the lowest position among ties is the lowest SEG_ID
        nearest = positions[distances == distances.min()].min()
        distance, donor_stand = float(distances.min()), donors.at[nearest, "STAND_ID"]
        rows = tree_list[as_id_series(tree_list["STAND_ID"]) == str(donor_stand)].copy()
        rows["DONOR_STAND_ID"] = str(donor_stand)
        rows["TREE_SOURCE"] = TreeSource.IMPUTED_NEAREST
        rows["NEAR_DIST"] = float(distance)
        rows["STAND_ID"] = stand.STAND_ID
        rows["SEG_ID"] = stand.SEG_ID
        rows["MU_ID"] = str(stand.SEG_ID)
        rows["TREE_ID"] = range(1, len(rows) + 1)
        imputed.append(rows)

    trees = pd.concat([tree_list, *imputed], ignore_index=True)
    still_missing = stands[~as_id_series(stands["STAND_ID"]).isin(set(trees["STAND_ID"]))]
    return trees, still_missing.copy()


FINAL_STAND_COLUMNS = [
    "STAND_ID", "VARIANT", "INV_YEAR", "STATE", "Stand_ID", "MU_ID", "SEG_ID", "ParentSEG_ID",
    "Acres", "OWN_CODE", "OWN_TYPE", "OWN_RAW", "OWN_PCT", "OWN_MIXED", "MGMT_CLASS",
    "MGMT_TYPE", "DB_GROUP", "PLT_CN", "AGE",
    "STDAGE_MEAN", "STDAGE_SD", "STDAGE_MIN", "STDAGE_MAX", "STDAGE_HOM", "FORTYPCD_DOM",
    "FORTYPE_PCT", "BALIVE_MEAN", "BALIVE_SD", "BALIVE_HOM", "QMD_MEAN", "QMD_SD", "QMD_HOM",
    "TPA_MEAN", "TPA_SD", "TPA_HOM", "HOMOGENEITY",
]


def prepare_stands(units: pd.DataFrame, *, inventory_year: int = 2022, variant: str = "SN",
                   state: str = "FL") -> pd.DataFrame:
    """One FVS stand row per management unit (stage3 `read_stand_polygons` +
    pipeline `_prepare_stands_dataframe`, single-part units).

    AGE is STDAGE_MEAN rounded half to even: LETO sets it with `Series.round()` before
    `_prepare_stands_dataframe` would apply `floor(x + 0.5)`, so the floor never runs.
    """
    stands = units.copy()
    stands["SEG_ID"] = pd.to_numeric(stands["MU_ID"], errors="raise").astype("int64")
    stands["STAND_ID"] = "MU_" + stands["SEG_ID"].astype(str)
    stands["Stand_ID"] = stands["SEG_ID"].astype(str)
    stands["MU_ID"] = stands["SEG_ID"].astype(str)
    stands["Acres"] = pd.to_numeric(stands["Acres"], errors="coerce")
    stands["AGE"] = (pd.to_numeric(stands["STDAGE_MEAN"], errors="coerce").round()
                     .astype("Int64"))
    stands["INV_YEAR"] = inventory_year
    stands["VARIANT"] = variant
    stands["STATE"] = state
    return stands.sort_values("SEG_ID").reset_index(drop=True)


def build_final_stands(stands: pd.DataFrame, weights: pd.DataFrame,
                       tree_list: pd.DataFrame) -> pd.DataFrame:
    """Runnable stands with their largest retained donor as the representative PLT_CN."""
    representative = (weights.sort_values(["SEG_ID", "WEIGHT"], ascending=[True, False])
                      .drop_duplicates("SEG_ID")[["SEG_ID", "PLT_CN"]])
    representative["PLT_CN"] = as_id_series(representative["PLT_CN"], column="PLT_CN")
    merged = stands.drop(columns="PLT_CN", errors="ignore").merge(
        representative, on="SEG_ID", how="left", validate="one_to_one")
    final = merged[as_id_series(merged["STAND_ID"]).isin(set(as_id_series(tree_list["STAND_ID"])))]
    return final[[c for c in FINAL_STAND_COLUMNS if c in final.columns]].reset_index(drop=True)


def validate_final_inventory(stands: pd.DataFrame, final_stands: pd.DataFrame,
                             trees: pd.DataFrame) -> None:
    """One row per STAND_ID, every final stand has trees, no tree points at an unknown stand."""
    for label, frame in (("stand list", stands), ("final stand list", final_stands)):
        if as_id_series(frame["STAND_ID"]).duplicated().any():
            raise RuntimeError(f"{label} contains duplicate STAND_ID values.")
    expected = set(as_id_series(final_stands["STAND_ID"]))
    tree_ids = set(as_id_series(trees["STAND_ID"]))
    if missing := sorted(expected - tree_ids):
        raise RuntimeError(f"Final FVS stands without tree records: {missing[:20]}")
    if unknown := sorted(tree_ids - expected):
        raise RuntimeError(f"Tree records reference unknown final stands: {unknown[:20]}")


@dataclass(frozen=True)
class Inventory:
    """Every Stage 4 product of `pipeline.run_pipeline`."""

    stands: pd.DataFrame
    weights: pd.DataFrame
    final_stands: pd.DataFrame
    trees: pd.DataFrame
    still_missing: pd.DataFrame
    tree_list: TreeList
    imputation_diagnostics: pd.DataFrame
    imputation_summary: pd.DataFrame


def build_inventory(units, weights: pd.DataFrame, fia_trees: pd.DataFrame,
                    species_lookup: Mapping[str, str], *,
                    minimum_weight: float = MINIMUM_PLOT_WEIGHT, inventory_year: int = 2022,
                    variant: str = "SN", state: str = "FL") -> Inventory:
    """LETO Stage 4 for single-part management units.

    ``units``: a projected GeoDataFrame with MU_ID, Acres, STDAGE_MEAN and DB_GROUP.
    ``weights``: unfiltered MU_ID x PLT_CN donor weights (`leto_donors.expand_to_units`).
    """
    stands = prepare_stands(units, inventory_year=inventory_year, variant=variant,
                            state=state)
    original = weights.copy()
    original["SEG_ID"] = pd.to_numeric(original["MU_ID"], errors="raise").astype("int64")
    original["STAND_ID"] = "MU_" + original["SEG_ID"].astype(str)
    original["PLT_CN"] = as_id_series(original["PLT_CN"], column="PLT_CN")
    retained = filter_weights(original, minimum_weight, key="SEG_ID")

    plots = set(original["PLT_CN"].dropna())
    donor_trees = fia_trees[as_id_series(fia_trees["PLT_CN"], column="PLT_CN").isin(plots)]
    tree_list = build_tree_list(retained, donor_trees, species_lookup)
    trees, still_missing = impute_missing_tree_lists(stands, tree_list.trees)

    table = pd.DataFrame(stands.drop(columns=stands.geometry.name))
    audit = imputation_qa.summarize_fia_donors(donor_trees, species_lookup,
                                               LIVE_TREE_STATUS_CODE)
    diagnostics = imputation_qa.build_imputation_diagnostics(table, original, retained, audit,
                                                             trees)
    summary = imputation_qa.build_imputation_summary(table, diagnostics, "Meters")
    final = build_final_stands(table, retained, trees)
    validate_final_inventory(table, final, trees)
    return Inventory(table, retained, final, trees,
                     pd.DataFrame(still_missing.drop(columns=still_missing.geometry.name)),
                     tree_list, diagnostics, summary)


def write_inventory(inventory: Inventory, out_dir: Path | str) -> dict[str, Path]:
    """Write the Stage 4 CSVs under LETO's file names into one folder."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "Segment_PLT_CN_Weights_Filtered.csv": inventory.weights,
        "Segment_Stand_List.csv": inventory.stands,
        "FVS_StandInit.csv": inventory.final_stands,
        "FVS_TreeInit.csv": inventory.trees,
        "Segment_FVS_Stands_No_Live_Trees.csv": inventory.still_missing,
        "QA_Missing_FVS_Species.csv": inventory.tree_list.missing_species,
        "QA_Missing_FVS_Species_By_Stand.csv": inventory.tree_list.missing_species_by_stand,
        "QA_Imputation_Diagnostics.csv": inventory.imputation_diagnostics,
        "QA_Imputation_Summary.csv": inventory.imputation_summary,
    }
    paths = {}
    for name, table in tables.items():
        paths[name] = out_dir / name
        table.to_csv(paths[name], index=False)
    return paths
