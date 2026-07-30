"""
Assign weighted FIA plot CNs to management units (LETO ``assign_plt_cn`` port).

For each management unit, this builds the **area-weighted set of TreeMap plot CNs** that
fall inside it — the crosswalk the FVS-input builder needs (a unit is imputed from a mix of
FIA plots in proportion to the TreeMap pixels it covers). Follows the LETO ArcGIS prototype
(`scripts/LETO.V1.1.txt`, ``assign_plt_cn``) but uses rasterio instead of arcpy:

    1. Rasterize the units onto the TreeMap grid by ``MU_ID`` (max-area cell assignment).
    2. Read the aligned TreeMap value raster.
    3. Count TreeMap pixels per (MU_ID, TreeMap value); map value → PLT_CN via the
       TreeMap-value→PLT_CN lookup; drop pixels with no PLT_CN.
    4. Weight = pixels of that PLT_CN in the unit / total mapped pixels in the unit.

Outputs a long weighted table (``MU_ID, TM_VALUE, CELL_COUNT, PLT_CN, TOTAL_CELLS, WEIGHT``)
and, optionally, the majority PLT_CN per unit for QA. The pure array routine
(``build_weighted_plt_cn``) is unit-tested; the raster wrapper needs real inputs.

Usage:
    uv run python -m pipeline.s3_management.assign_plt_cn \\
        --units data/interim/management_units_5co/12125/management_units_state0.gpkg \\
        --treemap data/raw/TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif \\
        --tmid-plt data/raw/TreeMap_Chaz/output/FL_5county_TMID_PLT_lookup.csv \\
        --out data/interim/fvs_inputs/MU_PLT_CN_Weights.csv
"""

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

NODATA_ID = -999999999
DEFAULT_VALUE_COL = "Value"
DEFAULT_PLT_COL = "PLT_CN"


def load_tmid_plt_lookup(
    path: Path, value_col: str = DEFAULT_VALUE_COL, plt_col: str = DEFAULT_PLT_COL
) -> dict[int, str]:
    """Load the TreeMap-value → PLT_CN lookup as ``{int value: str PLT_CN}``."""
    df = pd.read_csv(path, dtype=str)
    if value_col not in df.columns or plt_col not in df.columns:
        raise ValueError(
            f"Lookup {path} must have '{value_col}' and '{plt_col}' columns; got {list(df.columns)}"
        )
    out: dict[int, str] = {}
    for value, plt in zip(df[value_col], df[plt_col]):
        if pd.notna(value) and pd.notna(plt):
            out[int(float(value))] = str(plt).strip()
    return out


def build_weighted_plt_cn(
    mu_arr: np.ndarray,
    tm_arr: np.ndarray,
    tm_to_plt: dict[int, str],
    nodata: int = NODATA_ID,
) -> pd.DataFrame:
    """
    Build the weighted MU × PLT_CN table from two aligned rasters.

    ``mu_arr`` carries MU_ID per pixel, ``tm_arr`` the TreeMap value per pixel (both with
    ``nodata`` elsewhere). Returns columns ``MU_ID, TM_VALUE, CELL_COUNT, PLT_CN,
    TOTAL_CELLS, WEIGHT`` where WEIGHT sums to 1 within each MU_ID.
    """
    valid = (mu_arr != nodata) & (tm_arr != nodata)
    if not valid.any():
        raise ValueError("No overlapping MU / TreeMap pixels — check alignment and nodata.")

    cells = pd.DataFrame({
        "MU_ID": mu_arr[valid].astype("int64"),
        "TM_VALUE": tm_arr[valid].astype("int64"),
    })
    counts = cells.groupby(["MU_ID", "TM_VALUE"]).size().reset_index(name="CELL_COUNT")
    counts["PLT_CN"] = counts["TM_VALUE"].map(tm_to_plt)
    counts = counts.dropna(subset=["PLT_CN"]).copy()
    if counts.empty:
        raise ValueError("No TreeMap values mapped to a PLT_CN — check the lookup table.")

    counts["TOTAL_CELLS"] = counts.groupby("MU_ID")["CELL_COUNT"].transform("sum")
    counts["WEIGHT"] = counts["CELL_COUNT"] / counts["TOTAL_CELLS"]
    counts["MU_ID"] = counts["MU_ID"].astype(str)
    counts["PLT_CN"] = counts["PLT_CN"].astype(str)
    return counts.reset_index(drop=True)


def majority_plt_cn(weights: pd.DataFrame) -> pd.DataFrame:
    """Highest-weight PLT_CN per unit (``MU_ID, PLT_CN, TM_VALUE``) for QA/mapping."""
    return (
        weights.sort_values(["MU_ID", "CELL_COUNT"], ascending=[True, False])
        .drop_duplicates(subset=["MU_ID"])[["MU_ID", "PLT_CN", "TM_VALUE"]]
        .reset_index(drop=True)
    )


def rasterize_and_weight(
    units_path: Path,
    treemap_path: Path,
    tmid_plt_path: Path,
    id_field: str = "MU_ID",
    nodata: int = NODATA_ID,
) -> pd.DataFrame:
    """Raster wrapper: rasterize units to the TreeMap grid and build the weighted table."""
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize

    units = gpd.read_file(units_path)
    if id_field not in units.columns:
        # Fall back to a stable integer id derived from row order.
        units[id_field] = range(1, len(units) + 1)
    units[id_field] = pd.to_numeric(units[id_field], errors="coerce")

    with rasterio.open(treemap_path) as src:
        units = units.to_crs(src.crs)
        tm_arr = src.read(1)
        tm_nodata = src.nodata if src.nodata is not None else nodata
        tm_arr = np.where(tm_arr == tm_nodata, nodata, tm_arr).astype("int64")
        shapes = ((geom, int(val)) for geom, val in zip(units.geometry, units[id_field]) if pd.notna(val))
        mu_arr = rasterize(
            shapes, out_shape=src.shape, transform=src.transform,
            fill=nodata, dtype="int64", merge_alg=rasterio.enums.MergeAlg.replace,
        )

    tm_to_plt = load_tmid_plt_lookup(tmid_plt_path)
    return build_weighted_plt_cn(mu_arr, tm_arr, tm_to_plt, nodata=nodata)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="Assign weighted FIA plot CNs to management units")
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--treemap", type=Path, required=True)
    parser.add_argument("--tmid-plt", type=Path, required=True)
    parser.add_argument("--id-field", type=str, default="MU_ID")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    weights = rasterize_and_weight(args.units, args.treemap, args.tmid_plt, args.id_field)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    weights.to_csv(args.out, index=False)
    logger.info("Wrote %d weighted rows (%d units, %d PLT_CNs) to %s",
                len(weights), weights["MU_ID"].nunique(), weights["PLT_CN"].nunique(), args.out)


if __name__ == "__main__":
    main()
