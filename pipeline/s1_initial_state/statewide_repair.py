"""Statewide (Florida) repair of TreeMap 2022, with establishment imputation.

This is the 5-county hole-rectification method of ``pipeline/s1_initial_state``
scaled to the whole state, minus the Earth-Engine stages (no GEE credentials
here). What runs, and why that is still a defensible repair:

- The hole universe is derived, not imported: pixels where TreeMap 2022 carries
  no ``TM_ID`` (its nodata) inside Florida and on land per LANDFIRE EVT 2022
  (neither ``Open Water`` nor fill). This generalises the ArcGIS-exported
  ``Masked_Change_FL_AOI_16_22`` footprint.
- Holes are stratified by 2016/2024 LANDFIRE forest evidence exactly as
  ``stratify_treemap_holes.py`` does (tile-by-tile, integer code sets instead
  of in-memory string arrays — the CONUS vintages are mutually pixel-
  registered 30 m EPSG:5070, so windowed reads stay exact).
- **S1/S2 are added back unconditionally** — their label comes from LANDFIRE
  bracketing the hole with forest at both vintages, not from the model
  (``finalize_add_back.py``). S3/S4 stay holes here: their decision needs the
  AlphaEarth similarity mask and classifier, which require Earth Engine
  (`earthengine authenticate`); rerun with ``--scored-tif`` once that exists.
- Every accepted patch is given an establishment tree list by nearest-neighbour
  donor imputation (:mod:`pipeline.s1_initial_state.impute_establishment`): the
  modal TreeMap ``TM_ID`` in a 7 x 7 ring, the donor's own tree rows as the
  age-0 planting prescription.

Outputs (``data/processed/statewide_repair/``):

- ``treemap2022_fl_repaired.tif``   uint32 ``TM_ID``, recovered pixels carrying
  their donor's ``TM_ID``
- ``treemap_provenance_fl.tif``     0 measured, 1 establishment-imputed
- ``treemap_strata_fl.tif``         S1–S5 over the hole universe
- ``treemap_add_back_fl.tif``       accepted add-back (after the 5 ac MMU)
- ``establishment_patches.csv``     patch -> donor, acreage, stratum, cut window
- ``establishment_tree_lists.csv``  the donors' verbatim tree rows
- ``repair_summary.json``

Inputs default to this machine's ``data/r2_cache`` mirror of the drive; on the
workstation the same files sit at the ``/mnt/d`` paths declared in
``config/data_paths.yaml``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio import features
from rasterio.transform import Affine
from rasterio.windows import from_bounds

from pipeline.ids import read_id_csv
from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL, apply_mmu
from pipeline.s1_initial_state.impute_establishment import (
    donor_assignments,
    establishment_tree_lists,
    label_patches,
)
from pipeline.s1_initial_state.stratify_treemap_holes import (
    NON_FIA_TREE_PREFIXES,
    STRATA,
)

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "data/r2_cache"
OUT_DIR = REPO / "data/processed/statewide_repair"

TREE_MAP_TIF = CACHE / "TreeMap-2022/Data/TreeMap2022_CONUS.tif"
TREE_TABLE_CSV = CACHE / "TreeMap-2022/Data/TreeMap2022_CONUS_Tree_Table.csv"
FL_BOUNDARY = REPO / "data/interim/florida_boundary_5070.gpkg"


def evt_paths(year: int) -> tuple[Path, Path]:
    base = CACHE / f"LF{year}_EVT_CONUS" / f"LF{year}_EVT_CONUS"
    return base / "Tif" / f"LF{year}_EVT_CONUS.tif", base / "CSV_Data" / f"LF{year}_EVT.csv"


@dataclass(frozen=True)
class FLGrid:
    """The shared 30 m EPSG:5070 grid, anchored at its own top-left origin."""

    left: float
    top: float
    pixel: float = 30.0

    def window(self, bounds: tuple[float, float, float, float]):
        """Smallest whole-pixel window covering `bounds`, plus its transform."""
        window = from_bounds(*bounds, Affine(self.pixel, 0, self.left, 0, -self.pixel, self.top))
        col_off = int(np.floor(window.col_off))
        row_off = int(np.floor(window.row_off))
        width = int(np.ceil(window.col_off + window.width)) - col_off
        height = int(np.ceil(window.row_off + window.height)) - row_off
        snapped = rasterio.windows.Window(col_off, row_off, width, height)
        return snapped, Affine(self.pixel, 0, self.left + col_off * self.pixel,
                               0, -self.pixel, self.top - row_off * self.pixel)


@dataclasses.dataclass(frozen=True)
class LegendCodes:
    tree: np.ndarray    # EVT codes that count as FIA-forest tree cover
    logged: np.ndarray  # the three "Recently Logged" classes
    water: int          # the Open Water class code


def legend_code_sets(legend: pd.DataFrame) -> LegendCodes:
    """Integer code sets for the string-based rules in ``stratify_treemap_holes``."""
    names = legend["EVT_NAME"].astype(str)
    life = legend["EVT_LF"].astype(str)
    excluded = np.zeros(len(legend), dtype=bool)
    for prefix in NON_FIA_TREE_PREFIXES:
        excluded |= names.str.startswith(prefix).to_numpy()
    tree = legend.loc[(life == "Tree").to_numpy() & ~excluded, "VALUE"].to_numpy(dtype=np.int64)
    logged = legend.loc[names.str.startswith("Recently Logged").to_numpy(), "VALUE"].to_numpy(
        dtype=np.int64
    )
    water_rows = legend.loc[names == "Open Water", "VALUE"].to_numpy(dtype=np.int64)
    if water_rows.size != 1:
        raise ValueError(f"expected exactly one Open Water class, found {water_rows.tolist()}")
    return LegendCodes(tree, logged, int(water_rows[0]))


def stratify_block(
    evt16: np.ndarray, evt24: np.ndarray,
    codes16: LegendCodes, codes24: LegendCodes,
) -> np.ndarray:
    """S1–S5 for one tile; identical semantics to ``stratify_treemap_holes.stratify``.

    Each vintage is read against its own legend — 2016 supplies the cut
    evidence (tree lifeform + the three ``Recently Logged`` classes), 2024 the
    regrowth test.
    """
    logged16 = np.isin(evt16, codes16.logged)
    tree16 = np.isin(evt16, codes16.tree)
    tree24 = np.isin(evt24, codes24.tree)
    evidence16 = tree16 | logged16

    out = np.zeros(evt16.shape, dtype=np.uint8)
    out[logged16 & tree24] = 1
    out[tree16 & ~logged16 & tree24] = 2
    out[evidence16 & ~tree24] = 3
    out[~evidence16 & tree24] = 4
    out[~evidence16 & ~tree24] = 5
    return out


def unconditional_add_back(strata: np.ndarray) -> np.ndarray:
    """S1/S2 only — proven by LANDFIRE regrowth at both vintages, not by a model."""
    return np.isin(strata, (1, 2))


def _read_aligned(path: Path, bounds, shape, transform) -> np.ndarray:
    """Window read that fails loudly on grid misalignment (never resample).

    A raster whose footprint stops short of the requested window is padded with
    its own nodata to the requested shape — the LF vintages differ slightly in
    extent, and an off-grid pixel must look like absent data, not a shift.
    """
    with rasterio.open(path) as src:
        window = from_bounds(*bounds, src.transform).round_offsets().round_lengths()
        full = rasterio.windows.Window(0, 0, src.width, src.height)
        try:
            window = rasterio.windows.intersection(window, full)
        except rasterio.windows.WindowError:
            # Entirely outside the raster: pure nodata.
            return np.full(shape, src.nodata, dtype=src.dtypes[0])
        values = src.read(1, window=window)
        wt = src.window_transform(window)
        if values.shape != (window.height, window.width):
            raise ValueError(f"{path.name} read {values.shape} != window {window.height, window.width}")
    row0 = int(round((wt.f - transform.f) / -transform.e))
    col0 = int(round((transform.c - wt.c) / transform.a))
    if abs(wt.c - (transform.c + col0 * transform.a)) > 1e-6 or \
       abs(wt.f - (transform.f - row0 * transform.e)) > 1e-6:
        raise ValueError(
            f"{path.name} window origin {(wt.c, wt.f)} is not on the expected grid"
        )
    if values.shape == tuple(shape):
        return values
    padded = np.full(shape, src.nodata, dtype=src.dtypes[0])
    padded[row0:row0 + values.shape[0], col0:col0 + values.shape[1]] = values
    return padded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--min-acres", type=float, default=5.0)
    parser.add_argument("--tile", type=int, default=2048, help="tile edge in pixels")
    parser.add_argument("--tree-table", type=Path, default=TREE_TABLE_CSV)
    args = parser.parse_args()

    import geopandas as gpd

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    boundary = gpd.read_file(FL_BOUNDARY)
    geom = boundary.geometry.union_all()

    with rasterio.open(TREE_MAP_TIF) as src:
        tm_profile = src.profile
        if src.crs.to_epsg() != 5070:
            raise ValueError(f"TreeMap CRS {src.crs} is not EPSG:5070")
        bounds = (geom.bounds[0], geom.bounds[1], geom.bounds[2], geom.bounds[3])
        window, win_transform = FLGrid(src.transform.c, src.transform.f).window(bounds)
        print(f"Florida window: {window.width:,} x {window.height:,} px "
              f"({window.width * window.height * ACRES_PER_PIXEL:,.0f} ac of grid)")

        # The donor band: the full Florida window of TM_ID, holes (nodata) -> 0.
        # The TreeMap footprint can stop short of the boundary's southern edge
        # (the Keys), so the read is padded and its true extent recorded —
        # repair only applies where the product exists at all.
        donor = src.read(1, window=window)
        actual = donor.shape
        rows, cols = int(window.height), int(window.width)
        if actual != (rows, cols):
            padded = np.full((rows, cols), tm_profile["nodata"], dtype=donor.dtype)
            padded[: actual[0], : actual[1]] = donor
            donor = padded
        extent = np.zeros((rows, cols), dtype=bool)
        extent[: actual[0], : actual[1]] = True
        print(f"TreeMap footprint in window: {actual[0]:,} of {rows:,} rows")
        donor[donor == tm_profile["nodata"]] = 0
    nodata_value = tm_profile["nodata"]


    rows, cols = int(window.height), int(window.width)
    fl_mask = features.rasterize(
        [(geom, 1)], out_shape=(rows, cols), transform=win_transform, fill=0, dtype="uint8"
    ).astype(bool)

    # Legend code sets: 2016 and 2024 drive stratification, 2022 the land mask.
    codes16 = legend_code_sets(pd.read_csv(evt_paths(2016)[1]))
    codes24 = legend_code_sets(pd.read_csv(evt_paths(2024)[1]))
    water = legend_code_sets(pd.read_csv(evt_paths(2022)[1])).water

    strata = np.zeros((rows, cols), dtype=np.uint8)
    land = np.zeros((rows, cols), dtype=bool)
    tile = args.tile
    for row0 in range(0, rows, tile):
        for col0 in range(0, cols, tile):
            r1, c1 = min(row0 + tile, rows), min(col0 + tile, cols)
            shape = (r1 - row0, c1 - col0)
            t = Affine(30.0, 0, win_transform.c + col0 * 30.0, 0, -30.0, win_transform.f - row0 * 30.0)
            west = t.c
            north = t.f
            east = t.c + shape[1] * 30.0
            south = t.f - shape[0] * 30.0
            e22 = _read_aligned(evt_paths(2022)[0], (west, south, east, north), shape, t)
            e16 = _read_aligned(evt_paths(2016)[0], (west, south, east, north), shape, t)
            e24 = _read_aligned(evt_paths(2024)[0], (west, south, east, north), shape, t)
            land[row0:r1, col0:c1] = (e22 != water) & (e22 >= 0) & (e22 != 32767)
            strata[row0:r1, col0:c1] = stratify_block(e16, e24, codes16, codes24)

    hole = (donor == 0) & extent & fl_mask & land
    strata[~hole] = 0
    del land, fl_mask, extent

    hole_pixels = int(hole.sum())
    add_back = unconditional_add_back(strata) & hole
    add_back = apply_mmu(add_back, args.min_acres) & hole
    add_back_pixels = int(add_back.sum())

    labels, n_patches = label_patches(add_back)
    assignments, unresolved = donor_assignments(donor, labels, strata=strata)
    print(f"add-back: {add_back_pixels:,} px in {n_patches:,} patches; "
          f"{len(unresolved):,} patch(es) without a reachable donor")

    # Stamp the donors: recovered pixels carry the donor's TM_ID.
    donor_per_patch = np.zeros(n_patches + 1, dtype=np.uint32)
    for patch_id, value in assignments["donor_tm_id"].items():
        if pd.notna(value):
            donor_per_patch[patch_id] = int(value)
    donor_pixels = donor_per_patch[labels]
    del labels, donor_per_patch
    donor[add_back] = donor_pixels[add_back]
    del donor_pixels
    provenance = np.zeros((rows, cols), dtype=np.uint8)
    provenance[add_back] = 1

    def write(name: str, values: np.ndarray, dtype: str, nodata) -> Path:
        profile = tm_profile.copy()
        profile.update(
            height=rows, width=cols, transform=win_transform, dtype=dtype,
            count=1, nodata=nodata, compress="lzw", tiled=True, blockxsize=512, blockysize=512,
        )
        path = out_dir / name
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(values.astype(dtype), 1)
        return path

    # Ocean / non-FL / still-hole pixels keep the TreeMap nodata; no measured
    # plot has TM_ID 0, so `donor == 0` outside the add-back is exactly nodata.
    donor[(donor == 0) & (~add_back)] = nodata_value
    paths = {
        "repaired": write("treemap2022_fl_repaired.tif", donor, "uint32", nodata_value),
        "provenance": write("treemap_provenance_fl.tif", provenance, "uint8", 255),
        "strata": write("treemap_strata_fl.tif", strata, "uint8", 0),
        "add_back": write("treemap_add_back_fl.tif", add_back.astype(np.uint8), "uint8", 0),
    }
    del donor, provenance

    assignments.reset_index().to_csv(out_dir / "establishment_patches.csv", index=False)

    tree_table = read_id_csv(
        args.tree_table,
        usecols=["TM_ID", "PLT_CN", "STATUSCD", "TPA_UNADJ", "SPCD"],
    )
    lists = establishment_tree_lists(assignments, tree_table)
    lists.to_csv(out_dir / "establishment_tree_lists.csv", index=False)

    stratum_table = pd.DataFrame([
        {
            "stratum": code,
            "stratum_name": STRATA[code],
            "hole_pixels": int((strata == code).sum()),
            "hole_acres": int((strata == code).sum()) * ACRES_PER_PIXEL,
            "added_back_acres": int((add_back & (strata == code)).sum()) * ACRES_PER_PIXEL,
        }
        for code in STRATA
    ])
    stratum_table.to_csv(out_dir / "repair_summary.csv", index=False)

    summary = {
        "window_pixels": int(rows * cols),
        "hole_pixels": hole_pixels,
        "hole_acres": float(hole_pixels * ACRES_PER_PIXEL),
        "added_back_pixels": add_back_pixels,
        "added_back_acres": float(add_back_pixels * ACRES_PER_PIXEL),
        "patches": n_patches,
        "unresolved_patches": len(unresolved),
        "unique_donors": int(assignments["donor_tm_id"].dropna().nunique()),
        "model_gated_strata_pending_ee": [3, 4],
        "outputs": {k: str(v) for k, v in paths.items()},
    }
    (out_dir / "repair_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
