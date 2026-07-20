"""Build LETO-compatible FVS initial-state tables without ArcPy."""

from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd

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
    majority = (
        ranked.sort_values(
            ["MU_ID", "CELL_COUNT", "PLT_CN"], ascending=[True, False, True]
        )
        .drop_duplicates("MU_ID")[["MU_ID", "PLT_CN"]]
    )

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
    joined = joined.dropna(
        subset=["STAND_ID", "SPECIES", "DIAMETER", "TREE_COUNT"]
    )
    joined = joined.loc[joined["TREE_COUNT"] > 0].copy()
    joined["TREE_SOURCE"] = "FIA_WEIGHTED_DIRECT"
    joined["DONOR_STAND_ID"] = ""
    joined["NEAR_DIST"] = ""
    return joined[TREE_OUTPUT_COLUMNS].reset_index(drop=True)
