"""Build LETO-compatible FVS initial-state tables without ArcPy."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

from pipeline import leto_inventory
from pipeline.leto_donors import filter_weights
from pipeline.s1_initial_state.weights import attach_modal_plot, build_plot_weights

MU_COLUMNS = ["MU_ID", "Acres", "OWN_CODE", "OWN_TYPE", "SMZ_Pct"]
CROSSWALK_COLUMNS = [
    "Stand_ID",
    "MU_ID",
    "Acres",
    "PLT_CN",
    "OWN_CODE",
    "OWN_TYPE",
    "SMZ_Pct",
]
TREE_OUTPUT_COLUMNS = leto_inventory.TREE_COLUMNS
STAND_OUTPUT_COLUMNS = [
    "STAND_ID",
    "VARIANT",
    "INV_YEAR",
    "STATE",
    "Stand_ID",
    "MU_ID",
    "Acres",
    "PLT_CN",
    "OWN_CODE",
    "OWN_TYPE",
    "SMZ_Pct",
]
OUTPUT_NAMES = {
    "crosswalk": "MU_FVS_Crosswalk.csv",
    "weights": "MU_PLT_CN_Weights.csv",
    "stands": "FVS_StandInit.csv",
    "trees": "FVS_TreeInit.csv",
    "missing_stands": "MU_FVS_Stands_No_Live_Trees.csv",
}


@dataclass(frozen=True)
class InitialStateTables:
    """All tabular products from the LETO initial-state build."""

    crosswalk: pd.DataFrame
    weights: pd.DataFrame
    stands: pd.DataFrame
    trees: pd.DataFrame
    missing_stands: pd.DataFrame
    diagnostics: pd.Series


def build_management_unit_crosswalk(
    management_units: pd.DataFrame,
    weights: pd.DataFrame,
) -> pd.DataFrame:
    """Attach each management unit's majority TreeMap donor plot."""
    missing = set(MU_COLUMNS).difference(management_units.columns)
    if missing:
        raise ValueError(f"Management units missing columns: {sorted(missing)}")

    crosswalk = attach_modal_plot(management_units[MU_COLUMNS], weights)
    crosswalk.insert(0, "Stand_ID", crosswalk["MU_ID"])
    return crosswalk[CROSSWALK_COLUMNS]


def filter_and_normalize_weights(
    weights: pd.DataFrame,
    crosswalk: pd.DataFrame,
    min_plot_weight: float = 0.05,
) -> pd.DataFrame:
    """Apply LETO's donor threshold and renormalize within each retained unit.

    A unit whose donors all fall below the threshold keeps its largest one
    (LETO v1 `pipeline._filter_weights`, via `pipeline.leto_donors.filter_weights`).
    """
    retained = weights.copy()
    retained["MU_ID"] = retained["MU_ID"].astype("string")
    retained["PLT_CN"] = retained["PLT_CN"].astype("string")
    retained = filter_weights(retained, min_plot_weight, key="MU_ID")

    attributes = crosswalk[
        ["MU_ID", "Stand_ID", "Acres", "OWN_CODE", "OWN_TYPE", "SMZ_Pct"]
    ]
    return retained.merge(attributes, on="MU_ID", how="left", validate="many_to_one")


def load_species_lookup(path: Path | str, sheet_name: str) -> dict[str, str]:
    """Read the FIA-to-FVS species translator used by LETO."""
    frame = pd.read_excel(path, sheet_name=sheet_name, dtype=str)
    required = {"FIA CODE", "SN_Mapped_To"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Species crosswalk missing columns: {sorted(missing)}")
    clean_code = (
        pd.to_numeric(frame["FIA CODE"], errors="coerce")
        .astype("Int64")
        .astype("string")
        .str.zfill(3)
    )
    normalized = pd.DataFrame(
        {"FIA_CODE_CLEAN": clean_code, "FVS_SPECIES": frame["SN_Mapped_To"]}
    ).dropna()
    if normalized.groupby("FIA_CODE_CLEAN")["FVS_SPECIES"].nunique().gt(1).any():
        raise ValueError("Species crosswalk maps one FIA code to multiple FVS species")
    normalized = normalized.drop_duplicates("FIA_CODE_CLEAN")
    return dict(
        zip(normalized["FIA_CODE_CLEAN"], normalized["FVS_SPECIES"], strict=True)
    )


def load_fia_tree_files(paths: Sequence[Path | str]) -> pd.DataFrame:
    """Combine state FIA TREE.csv files while retaining identifiers as strings."""
    if not paths:
        raise ValueError("At least one FIA TREE.csv path is required")
    return pd.concat(
        [pd.read_csv(path, dtype=str) for path in paths],
        ignore_index=True,
    )


def prepare_direct_tree_rows(
    normalized_weights: pd.DataFrame,
    fia_trees: pd.DataFrame,
    species_lookup: Mapping[str, str],
) -> pd.DataFrame:
    """Join weighted donor plots to live FIA trees (LETO v1 `stage4.build_tree_list`)."""
    weights = normalized_weights.copy()
    weights["SEG_ID"] = pd.to_numeric(weights["MU_ID"], errors="raise").astype("int64")
    weights["STAND_ID"] = "MU_" + weights["Stand_ID"].astype("string")
    if "WEIGHT_ORIGINAL" not in weights.columns:
        weights["WEIGHT_ORIGINAL"] = weights["WEIGHT"]
    return leto_inventory.build_tree_list(weights, fia_trees, species_lookup).trees


def impute_missing_tree_rows(
    management_units: gpd.GeoDataFrame,
    crosswalk: pd.DataFrame,
    direct_trees: pd.DataFrame,
) -> pd.DataFrame:
    """Copy the nearest runnable unit's trees into each missing unit
    (LETO v1 `stage4.impute_missing_tree_lists`)."""
    stands = management_units[["MU_ID", "geometry"]].copy()
    stands["MU_ID"] = stands["MU_ID"].astype("string")
    stands = stands[stands["MU_ID"].isin(set(crosswalk["MU_ID"].astype("string")))]
    stands["SEG_ID"] = pd.to_numeric(stands["MU_ID"], errors="raise").astype("int64")
    stands["STAND_ID"] = "MU_" + stands["MU_ID"]
    trees, _ = leto_inventory.impute_missing_tree_lists(stands, direct_trees)
    return trees


def build_stand_rows(
    crosswalk: pd.DataFrame,
    trees: pd.DataFrame,
    *,
    inventory_year: int = 2022,
    variant: str = "SN",
    state: str = "FL",
) -> pd.DataFrame:
    """Build LETO FVS stand rows for every unit with a tree list."""
    stands = crosswalk.copy()
    stands["STAND_ID"] = "MU_" + stands["Stand_ID"].astype("string")
    stands["INV_YEAR"] = inventory_year
    stands["VARIANT"] = variant
    stands["STATE"] = state
    live_stands = set(trees["STAND_ID"].astype("string"))
    stands = stands.loc[stands["STAND_ID"].isin(live_stands)].copy()
    return stands[STAND_OUTPUT_COLUMNS].reset_index(drop=True)


def build_initial_state(
    management_units: gpd.GeoDataFrame,
    weights: pd.DataFrame,
    fia_trees: pd.DataFrame,
    species_lookup: Mapping[str, str],
    *,
    min_plot_weight: float = 0.05,
) -> InitialStateTables:
    """Build all LETO initial-state tables from in-memory inputs."""
    fia_trees = fia_trees.copy()
    fia_trees["PLT_CN"] = fia_trees["PLT_CN"].astype("string")
    crosswalk = build_management_unit_crosswalk(management_units, weights)
    normalized_weights = filter_and_normalize_weights(
        weights, crosswalk, min_plot_weight
    )
    direct_trees = prepare_direct_tree_rows(
        normalized_weights, fia_trees, species_lookup
    )
    trees = impute_missing_tree_rows(management_units, crosswalk, direct_trees)
    stands = build_stand_rows(crosswalk, trees)
    live_stands = set(trees["STAND_ID"].astype("string"))
    missing_stands = crosswalk.copy()
    missing_stands["STAND_ID"] = "MU_" + missing_stands["Stand_ID"].astype("string")
    missing_stands = missing_stands.loc[
        ~missing_stands["STAND_ID"].isin(live_stands)
    ].reset_index(drop=True)
    weighted_plot_ids = set(normalized_weights["PLT_CN"].astype("string"))
    fia_plot_ids = set(fia_trees["PLT_CN"].astype("string"))
    matched_live_trees = normalized_weights.merge(fia_trees, on="PLT_CN", how="inner")
    matched_live_trees = matched_live_trees.loc[
        matched_live_trees["STATUSCD"] == "1"
    ].copy()
    matched_live_trees["FIA_CODE_CLEAN"] = (
        pd.to_numeric(matched_live_trees["SPCD"], errors="coerce")
        .astype("Int64")
        .astype("string")
        .str.zfill(3)
    )
    mapped_species = matched_live_trees["FIA_CODE_CLEAN"].map(species_lookup)
    weight_sums = normalized_weights.groupby("MU_ID")["WEIGHT"].sum()
    donor_counts = normalized_weights.groupby("MU_ID").size()
    diagnostics = pd.Series(
        {
            "raw_weight_rows": len(weights),
            "filtered_weight_rows": len(normalized_weights),
            "unmatched_weighted_plots": len(weighted_plot_ids - fia_plot_ids),
            "missing_fvs_species_tree_rows": int(mapped_species.isna().sum()),
            "mean_weight_sum": float(weight_sums.mean()),
            "min_weight_sum": float(weight_sums.min()),
            "max_weight_sum": float(weight_sums.max()),
            "average_donor_plots": float(donor_counts.mean()),
            "maximum_donor_plots": int(donor_counts.max()),
            "direct_stands": direct_trees["STAND_ID"].nunique(),
            "imputed_stands": trees.loc[
                trees["TREE_SOURCE"] == "IMPUTED_NEAREST", "STAND_ID"
            ].nunique(),
            "missing_stands": len(missing_stands),
        },
        name="value",
    )
    return InitialStateTables(
        crosswalk=crosswalk,
        weights=weights.copy(),
        stands=stands,
        trees=trees,
        missing_stands=missing_stands,
        diagnostics=diagnostics,
    )


def write_initial_state(
    tables: InitialStateTables,
    output_dir: Path | str,
) -> dict[str, Path]:
    """Write all LETO-compatible CSV products."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {name: destination / filename for name, filename in OUTPUT_NAMES.items()}
    for name, path in paths.items():
        getattr(tables, name).to_csv(path, index=False)
    return paths


def run_initial_state_from_sqlite(
    management_units: gpd.GeoDataFrame,
    weights: pd.DataFrame,
    fiadb_path: Path,
    species_crosswalk_path: Path,
    output_dir: Path | None = None,
) -> InitialStateTables:
    """Build initial-state tables from segmented units and read-only FIADB data."""
    from pipeline.s1_initial_state.data_sources import (
        SPECIES_CROSSWALK_SHEET,
        load_fia_trees_sqlite,
    )

    attributed_units = attach_modal_plot(management_units, weights)
    plot_ids = weights["PLT_CN"].dropna().astype("string").tolist()
    fia_trees = load_fia_trees_sqlite(fiadb_path, plot_ids)
    species_lookup = load_species_lookup(
        species_crosswalk_path,
        SPECIES_CROSSWALK_SHEET,
    )
    tables = build_initial_state(
        attributed_units,
        weights,
        fia_trees,
        species_lookup,
    )
    if output_dir is not None:
        write_initial_state(tables, output_dir)
    return tables


def run_leto_initial_state(
    *,
    management_units_path: Path | str,
    treemap_path: Path | str,
    treemap_lookup_path: Path | str,
    species_crosswalk_path: Path | str,
    species_crosswalk_sheet: str,
    fia_tree_paths: Sequence[Path | str],
    output_dir: Path | str,
    management_units_layer: str | None = None,
    lookup_value_column: str = "VALUE",
    min_plot_weight: float = 0.05,
) -> InitialStateTables:
    """Run the non-ArcPy LETO initial-state pipeline from file inputs."""
    management_units = gpd.read_file(
        management_units_path,
        layer=management_units_layer,
    )
    treemap_lookup = pd.read_csv(
        treemap_lookup_path,
        dtype={"PLT_CN": "string"},
    )
    weights = build_plot_weights(
        management_units,
        treemap_path,
        treemap_lookup,
        lookup_value_column=lookup_value_column,
    )
    species_lookup = load_species_lookup(
        species_crosswalk_path,
        species_crosswalk_sheet,
    )
    fia_trees = load_fia_tree_files(fia_tree_paths)
    tables = build_initial_state(
        management_units,
        weights,
        fia_trees,
        species_lookup,
        min_plot_weight=min_plot_weight,
    )
    write_initial_state(tables, output_dir)
    return tables
