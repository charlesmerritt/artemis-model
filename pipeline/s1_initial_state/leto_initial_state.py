"""Build LETO-compatible FVS initial-state tables without ArcPy."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import STRtree

from pipeline.s1_initial_state.weights import build_plot_weights

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
TREE_RENAME = {
    "CN": "TREE_ID",
    "INVYR": "INV_YEAR",
    "SPCD": "Species_FIA",
    "DIA": "DIAMETER",
    "CR": "CRRATIO",
    "TPA_UNADJ": "TREE_COUNT",
}
TREE_OUTPUT_COLUMNS = [
    "STAND_ID",
    "TREE_ID",
    "SPECIES",
    "DIAMETER",
    "HT",
    "CRRATIO",
    "TREE_COUNT",
    "MU_ID",
    "PLT_CN",
    "WEIGHT",
    "Species_FIA",
    "TREE_SOURCE",
    "DONOR_STAND_ID",
    "NEAR_DIST",
]
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


def build_management_unit_crosswalk(
    management_units: pd.DataFrame,
    weights: pd.DataFrame,
) -> pd.DataFrame:
    """Attach each management unit's majority TreeMap donor plot."""
    missing = set(MU_COLUMNS).difference(management_units.columns)
    if missing:
        raise ValueError(f"Management units missing columns: {sorted(missing)}")

    units = management_units[MU_COLUMNS].copy()
    units["MU_ID"] = units["MU_ID"].astype("string")
    if units["MU_ID"].isna().any() or units["MU_ID"].duplicated().any():
        raise ValueError("MU_ID values must be non-null and unique")

    ranked = weights.copy()
    ranked["MU_ID"] = ranked["MU_ID"].astype("string")
    ranked["PLT_CN"] = ranked["PLT_CN"].astype("string")
    majority = ranked.sort_values(
        ["MU_ID", "CELL_COUNT", "PLT_CN"], ascending=[True, False, True]
    ).drop_duplicates("MU_ID")[["MU_ID", "PLT_CN"]]

    crosswalk = units.merge(majority, on="MU_ID", how="left", validate="one_to_one")
    crosswalk.insert(0, "Stand_ID", crosswalk["MU_ID"])
    return crosswalk[CROSSWALK_COLUMNS]


def filter_and_normalize_weights(
    weights: pd.DataFrame,
    crosswalk: pd.DataFrame,
    min_plot_weight: float = 0.05,
) -> pd.DataFrame:
    """Apply LETO's donor threshold and renormalize within each retained unit."""
    retained = weights.copy()
    retained["MU_ID"] = retained["MU_ID"].astype("string")
    retained["PLT_CN"] = retained["PLT_CN"].astype("string")
    retained["WEIGHT"] = pd.to_numeric(retained["WEIGHT"], errors="coerce")
    retained = retained.loc[retained["WEIGHT"] >= min_plot_weight].copy()
    retained["WEIGHT"] = retained["WEIGHT"] / retained.groupby("MU_ID")[
        "WEIGHT"
    ].transform("sum")

    attributes = crosswalk[
        ["MU_ID", "Stand_ID", "Acres", "OWN_CODE", "OWN_TYPE", "SMZ_Pct"]
    ]
    return retained.merge(attributes, on="MU_ID", how="left", validate="many_to_one")


def load_species_lookup(path: Path | str, sheet_name: str) -> dict[str, str]:
    """Read the FIA-to-FVS species translator used by LETO."""
    frame = pd.read_excel(path, sheet_name=sheet_name, dtype=str)
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
    """Join weighted donor plots to live FIA trees and emit FVS tree rows."""
    trees = fia_trees.copy()
    trees["PLT_CN"] = trees["PLT_CN"].astype("string")
    joined = normalized_weights.merge(trees, on="PLT_CN", how="inner")
    joined = joined.loc[joined["STATUSCD"] == "1"].copy()
    joined = joined.rename(columns=TREE_RENAME)
    joined["Species_FIA_CLEAN"] = (
        pd.to_numeric(joined["Species_FIA"], errors="coerce")
        .astype("Int64")
        .astype("string")
        .str.zfill(3)
    )
    joined["SPECIES"] = joined["Species_FIA_CLEAN"].map(species_lookup)
    joined["STAND_ID"] = "MU_" + joined["Stand_ID"].astype("string")

    for column in ["DIAMETER", "HT", "ACTUALHT", "CRRATIO", "TREE_COUNT", "WEIGHT"]:
        if column in joined.columns:
            joined[column] = pd.to_numeric(joined[column], errors="coerce")
    if "HT" in joined.columns and "ACTUALHT" in joined.columns:
        joined["HT"] = joined["HT"].fillna(joined["ACTUALHT"])

    joined["TREE_COUNT"] = joined["TREE_COUNT"] * joined["WEIGHT"]
    joined = joined.dropna(subset=["STAND_ID", "SPECIES", "DIAMETER", "TREE_COUNT"])
    joined = joined.loc[joined["TREE_COUNT"] > 0].copy()
    joined["TREE_SOURCE"] = "FIA_WEIGHTED_DIRECT"
    joined["DONOR_STAND_ID"] = ""
    joined["NEAR_DIST"] = ""
    return joined[TREE_OUTPUT_COLUMNS].reset_index(drop=True)


def impute_missing_tree_rows(
    management_units: gpd.GeoDataFrame,
    crosswalk: pd.DataFrame,
    direct_trees: pd.DataFrame,
) -> pd.DataFrame:
    """Copy the nearest runnable unit's trees into each missing unit."""
    units = management_units[["MU_ID", "geometry"]].copy()
    units["MU_ID"] = units["MU_ID"].astype("string")
    crosswalk_ids = crosswalk["MU_ID"].astype("string")
    runnable_ids = set(direct_trees["MU_ID"].astype("string"))
    missing_ids = [mu_id for mu_id in crosswalk_ids if mu_id not in runnable_ids]
    if not missing_ids:
        return direct_trees.copy().reset_index(drop=True)
    if management_units.crs is None or management_units.crs.is_geographic:
        raise ValueError("Nearest-tree imputation requires a projected CRS")
    if not runnable_ids:
        raise ValueError("No runnable management unit is available for imputation")

    donor_units = units.loc[units["MU_ID"].isin(runnable_ids)].sort_values("MU_ID")
    if donor_units.empty:
        raise ValueError("No runnable management unit is available for imputation")
    missing_units = units.set_index("MU_ID").reindex(missing_ids)
    if missing_units.geometry.isna().any():
        raise ValueError("Every missing management unit must have a geometry")

    spatial_index = STRtree(donor_units.geometry.to_numpy())
    imputed_groups = []
    for missing_id, geometry in missing_units.geometry.items():
        donor_positions, distances = spatial_index.query_nearest(
            geometry,
            all_matches=False,
            return_distance=True,
        )
        donor_id = donor_units.iloc[int(donor_positions[0])]["MU_ID"]
        donor_stand_id = f"MU_{donor_id}"
        donor_trees = direct_trees.loc[
            direct_trees["STAND_ID"].astype("string") == donor_stand_id
        ].copy()
        donor_trees["DONOR_STAND_ID"] = donor_stand_id
        donor_trees["TREE_SOURCE"] = "IMPUTED_NEAREST"
        donor_trees["NEAR_DIST"] = float(distances[0])
        donor_trees["STAND_ID"] = f"MU_{missing_id}"
        donor_trees["MU_ID"] = missing_id
        donor_trees["TREE_ID"] = range(1, len(donor_trees) + 1)
        imputed_groups.append(donor_trees)

    return pd.concat([direct_trees, *imputed_groups], ignore_index=True)


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
    return InitialStateTables(
        crosswalk=crosswalk,
        weights=weights.copy(),
        stands=stands,
        trees=trees,
        missing_stands=missing_stands,
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
