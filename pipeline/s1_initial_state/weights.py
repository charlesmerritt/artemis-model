"""Build management-unit to FIA plot weights on the native TreeMap grid."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.errors import WindowError
from rasterio.features import geometry_window, rasterize

WEIGHT_COLUMNS = [
    "MU_ID",
    "TM_VALUE",
    "CELL_COUNT",
    "TOTAL_CELLS",
    "WEIGHT",
    "PLT_CN",
]


def _normalized_lookup(lookup: pd.DataFrame, value_column: str) -> pd.DataFrame:
    required = {value_column, "PLT_CN"}
    missing = required.difference(lookup.columns)
    if missing:
        raise ValueError(f"TreeMap lookup missing columns: {sorted(missing)}")

    normalized = lookup[[value_column, "PLT_CN"]].rename(
        columns={value_column: "TM_VALUE"}
    )
    normalized = normalized.dropna(subset=["TM_VALUE", "PLT_CN"]).copy()
    normalized["PLT_CN"] = normalized["PLT_CN"].astype("string")

    if normalized.groupby("TM_VALUE")["PLT_CN"].nunique().gt(1).any():
        raise ValueError("TreeMap lookup maps one raster value to multiple PLT_CNs")

    return normalized.drop_duplicates("TM_VALUE")


def _validated_units(management_units: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if "MU_ID" not in management_units.columns:
        raise ValueError("Management units missing column: MU_ID")
    if management_units.crs is None:
        raise ValueError("Management units must define a CRS")

    units = management_units.copy()
    units["MU_ID"] = units["MU_ID"].astype("string")
    if units["MU_ID"].isna().any() or units["MU_ID"].duplicated().any():
        raise ValueError("MU_ID values must be non-null and unique")
    return units


def build_plot_weights(
    management_units: gpd.GeoDataFrame,
    treemap_path: Path | str,
    treemap_lookup: pd.DataFrame,
    *,
    lookup_value_column: str = "VALUE",
) -> pd.DataFrame:
    """Count TreeMap plot cells within each management unit and calculate weights."""
    units = _validated_units(management_units)
    lookup = _normalized_lookup(treemap_lookup, lookup_value_column)

    with rasterio.open(treemap_path) as source:
        units = units.to_crs(source.crs)
        try:
            window = geometry_window(source, units.geometry)
        except WindowError as error:
            raise ValueError("Management units overlap no TreeMap cells") from error
        transform = source.window_transform(window)
        code_by_mu = {
            mu_id: code for code, mu_id in enumerate(units["MU_ID"].tolist(), 1)
        }
        mu_by_code = {code: mu_id for mu_id, code in code_by_mu.items()}
        mu_grid = rasterize(
            (
                (geometry, code_by_mu[mu_id])
                for mu_id, geometry in zip(units["MU_ID"], units.geometry, strict=True)
            ),
            out_shape=(int(window.height), int(window.width)),
            transform=transform,
            fill=0,
            all_touched=False,
            dtype="int32",
        )
        treemap = source.read(1, window=window, masked=True)

    valid = (mu_grid > 0) & ~np.ma.getmaskarray(treemap)
    if not valid.any():
        raise ValueError("Management units overlap no valid TreeMap cells")
    cells = pd.DataFrame(
        {
            "MU_CODE": mu_grid[valid].astype("int64"),
            "TM_VALUE": treemap.data[valid].astype("int64"),
        }
    )
    counts = (
        cells.groupby(["MU_CODE", "TM_VALUE"]).size().reset_index(name="CELL_COUNT")
    )
    counts["MU_ID"] = counts["MU_CODE"].map(mu_by_code).astype("string")
    counts["CELL_COUNT"] = counts["CELL_COUNT"].astype("int64")
    result = counts.merge(lookup, on="TM_VALUE", how="inner")
    if result.empty:
        raise ValueError("Weighted MU x PLT_CN table is empty after TreeMap lookup")
    result["TOTAL_CELLS"] = (
        result.groupby("MU_ID")["CELL_COUNT"].transform("sum").astype("int64")
    )
    result["WEIGHT"] = result["CELL_COUNT"] / result["TOTAL_CELLS"]

    return (
        result[WEIGHT_COLUMNS]
        .sort_values(["MU_ID", "WEIGHT", "TM_VALUE"], ascending=[True, False, True])
        .reset_index(drop=True)
    )
