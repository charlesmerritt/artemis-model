"""Stratify TreeMap 2022 holes by LANDFIRE EVT forest evidence (2016 / 2022 / 2024).

TreeMap only assigns a ``TM_ID`` where LANDFIRE EVT calls the pixel forest. A
stand that was clearcut before the LANDFIRE vintage is mapped as ruderal
grassland / pasture, so it becomes a *hole* in TreeMap even though it is managed
forest land. This module labels every hole pixel with the evidence that it is
really forest:

    S1 cut_pre2016_regrown  LF2016 "Recently Logged"  -> LF2024 tree
    S2 cut_2016_2022_regrown LF2016 natural tree      -> LF2024 tree
    S3 cut_2016_2022_open    LF2016 forest evidence   -> LF2024 still non-tree
    S4 regrown_only          no LF2016 evidence       -> LF2024 tree
    S5 no_evidence           neither endpoint is tree (genuine non-forest)

"Forest evidence" in 2016 = Tree lifeform (excluding urban/developed/orchard
tree classes, which TreeMap excludes by design) OR one of the three
``Recently Logged-*`` classes.

The three LANDFIRE vintages used here are all on the **Remap legend** — the
local ``LF2016_EVT_CONUS`` download is LF 2.0.0, whose class codes match LF2022
and LF2024 exactly (verified: pasture 7997, ruderal grassland 9823, plantation
9322 in all three). Cross-vintage comparison is therefore code-consistent, which
is *not* true of the 2016 EVT vintage published on Earth Engine.

Inputs are windowed reads off the CONUS EVT tifs, so no full-raster read ever
happens. All rasters share the 30 m NAD83 / Conus Albers grid; alignment is
asserted, not assumed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds

REPO = Path(__file__).resolve().parents[2]
DRIVE = Path("/mnt/d")

# AOI mask: the ArcGIS-exported change raster is already TreeMap-holes clipped to
# the 5-county polygon, so its valid-data footprint *is* the hole universe.
AOI_HOLES_RASTER = DRIVE / "Masked_Change_FL_AOI_16_22"
OUT_DIR = REPO / "data/interim/treemap_holes"

ACRES_PER_PIXEL = 0.2224  # 30 m pixel = 900 m²

# EVT tree classes TreeMap deliberately excludes (not FIA forest land).
NON_FIA_TREE_PREFIXES = (
    "Eastern Warm Temperate Urban",
    "Eastern Warm Temperate Developed",
    "Developed",
    "Eastern Warm Temperate Orchard",
)

STRATA = {
    1: "S1_cut_pre2016_regrown",
    2: "S2_cut_2016_2022_regrown",
    3: "S3_cut_2016_2022_open",
    4: "S4_regrown_only",
    5: "S5_no_evidence",
}


def evt_paths(year: int) -> tuple[Path, Path]:
    base = DRIVE / f"LF{year}_EVT_CONUS" / f"LF{year}_EVT_CONUS"
    return base / "Tif" / f"LF{year}_EVT_CONUS.tif", base / "CSV_Data" / f"LF{year}_EVT.csv"


def read_evt_window(year: int, bounds, shape, transform) -> tuple[np.ndarray, np.ndarray]:
    """Return (EVT_NAME, EVT_LF) string arrays for `year` over the AOI window."""
    tif, csv = evt_paths(year)
    with rasterio.open(tif) as src:
        window = from_bounds(*bounds, src.transform).round_offsets().round_lengths()
        values = src.read(1, window=window)
        wt = src.window_transform(window)
    if values.shape != shape:
        raise ValueError(f"LF{year} window {values.shape} != AOI {shape}")
    if abs(wt.c - transform.c) > 1e-6 or abs(wt.f - transform.f) > 1e-6:
        raise ValueError(f"LF{year} window origin {(wt.c, wt.f)} != AOI {(transform.c, transform.f)}")

    legend = pd.read_csv(csv).set_index("VALUE")[["EVT_NAME", "EVT_LF"]]
    size = int(max(values.max(), legend.index.max())) + 1
    names = np.full(size, "NA", dtype=object)
    lifeforms = np.full(size, "NA", dtype=object)
    codes = legend.index.values
    keep = codes >= 0  # -9999 Fill-NoData cannot index a lookup array
    names[codes[keep]] = legend.EVT_NAME.values[keep]
    lifeforms[codes[keep]] = legend.EVT_LF.values[keep]

    idx = values.astype(np.int64)
    idx[idx < 0] = 0
    return names[idx], lifeforms[idx]


def fia_tree(names: np.ndarray, lifeforms: np.ndarray) -> np.ndarray:
    """Tree lifeform excluding the urban/developed/orchard classes TreeMap drops."""
    text = names.astype(str)
    excluded = np.zeros(text.shape, dtype=bool)
    for prefix in NON_FIA_TREE_PREFIXES:
        excluded |= np.char.startswith(text, prefix)
    return (lifeforms == "Tree") & ~excluded


def stratify(aoi: np.ndarray, bounds, transform) -> np.ndarray:
    """Return a uint8 stratum raster (0 outside the hole universe, 1-5 = STRATA)."""
    name16, lf16 = read_evt_window(2016, bounds, aoi.shape, transform)
    name24, lf24 = read_evt_window(2024, bounds, aoi.shape, transform)

    logged16 = np.char.startswith(name16.astype(str), "Recently Logged")
    tree16 = fia_tree(name16, lf16)
    tree24 = fia_tree(name24, lf24)
    evidence16 = tree16 | logged16

    out = np.zeros(aoi.shape, dtype=np.uint8)
    out[aoi & logged16 & tree24] = 1
    out[aoi & tree16 & ~logged16 & tree24] = 2
    out[aoi & evidence16 & ~tree24] = 3
    out[aoi & ~evidence16 & tree24] = 4
    out[aoi & ~evidence16 & ~tree24] = 5
    return out


def summarize(strata: np.ndarray) -> pd.DataFrame:
    total = int((strata > 0).sum())
    rows = [
        {
            "code": code,
            "stratum": label,
            "pixels": int((strata == code).sum()),
            "acres": int((strata == code).sum()) * ACRES_PER_PIXEL,
            "frac_of_holes": int((strata == code).sum()) / total,
        }
        for code, label in STRATA.items()
    ]
    return pd.DataFrame(rows)


def hole_universe(band: np.ndarray, nodata: float | int | None) -> np.ndarray:
    """Valid-data footprint of the hole raster.

    ``--aoi-raster`` accepts any raster, and a NoData-less source would make
    ``band != nodata`` compare against ``None`` element-wise — an all-True array,
    i.e. the whole rectangular bounding box instead of the in-AOI holes. That is
    the 3x overstatement documented for ``TreeMap_Holes_CopyRaster`` in
    ``docs/treemap_holes/README.md``, so fail closed as ``make_report_figures``
    already does rather than silently stratifying land outside the AOI.
    """
    if nodata is None or not np.isfinite(nodata):
        raise ValueError(
            "TreeMap hole source must declare a finite NoData value; its "
            "valid-data footprint defines the hole universe"
        )
    return band != nodata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aoi-raster", type=Path, default=AOI_HOLES_RASTER,
                        help="raster whose valid-data footprint defines the TreeMap hole universe")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    with rasterio.open(args.aoi_raster) as src:
        aoi = hole_universe(src.read(1), src.nodata)
        profile = src.profile
        bounds, transform = src.bounds, src.transform
    print(f"hole universe: {aoi.sum():,} px ({aoi.sum() * ACRES_PER_PIXEL:,.0f} ac)")

    strata = stratify(aoi, bounds, transform)
    table = summarize(strata)
    print(table.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    profile.update(dtype="uint8", nodata=0, count=1, compress="lzw")
    raster_path = args.out_dir / "treemap_hole_strata.tif"
    with rasterio.open(raster_path, "w", **profile) as dst:
        dst.write(strata, 1)
    table.to_csv(args.out_dir / "treemap_hole_strata_summary.csv", index=False)
    print(f"wrote {raster_path} and treemap_hole_strata_summary.csv")


if __name__ == "__main__":
    main()
