"""LANDFIRE EVT 2022 classes under the TreeMap add-back, five-county AOI.

Question (2026-09-30): of the land the most permissive add-back returns to TreeMap 2022, how
much sits in each LF2022 EVT class — above all Southeastern Ruderal Grassland (9823) and
Eastern Warm Temperate Pasture and Hayland (7997) — and how much of each class is left out?

"Most permissive" is the ungated three-method run in the sibling ``raster-improvement`` repo
(``data/processed/improved-rasters-three-methods/aoi_5county``): bookends, Obata and Hansen
combined by union, with no EVT agriculture gate. Two add-back measures per class:

- ``proposed``: the union of method proposals (``add_back_method_bits`` > 0), before the
  5 ac minimum patch and the donor filter.
- ``added_back``: what the run added (``treemap2022_provenance`` 2 or 4), after them.

``left_out`` is a TreeMap hole of the class that was not added back (provenance 3), and
``left_out_share_of_holes`` is left_out / (added_back + left_out). Published TreeMap
(provenance 1) and water (0) are neither. ``aoi_ac`` is every pixel of the class inside the
five counties.

    uv run python -m research.evt_addback_overlap.overlap          # writes the CSVs beside this file
    uv run python -m research.evt_addback_overlap.overlap --help
"""

from __future__ import annotations

import argparse
from enum import IntEnum
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window, from_bounds

from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL
from pipeline.spatial_ref import assert_project_crs

HERE = Path(__file__).resolve().parent
RUN_DIR = Path("/home/chazm/projects/raster-improvement/data/processed/"
               "improved-rasters-three-methods/aoi_5county")
LANDFIRE = Path("/mnt/d/landfire/LF2022_EVT_CONUS/LF2022_EVT_CONUS")

OUTSIDE = 255  # the run's nodata: outside the five counties


class Provenance(IntEnum):
    """``treemap2022_provenance.tif`` codes (``county_improvement.TreeMapProvenance``, fl5 branch)."""

    WATER = 0
    PUBLISHED = 1
    ADDED_BACK = 2
    UNMAPPED_LAND = 3
    ADDED_BACK_YOUNG = 4


HOLES = (Provenance.ADDED_BACK, Provenance.UNMAPPED_LAND, Provenance.ADDED_BACK_YOUNG)
ADDED = (Provenance.ADDED_BACK, Provenance.ADDED_BACK_YOUNG)
AREAS = ["aoi_ac", "published_ac", "hole_ac", "proposed_ac", "added_back_ac", "left_out_ac"]
COLUMNS = ["VALUE", "EVT_NAME", "EVT_PHYS", *AREAS,
           "proposed_share", "added_back_share", "left_out_share_of_holes"]


def by_evt_class(evt: np.ndarray, provenance: np.ndarray, method_bits: np.ndarray,
                 legend: pd.DataFrame, acres_per_pixel: float = ACRES_PER_PIXEL) -> pd.DataFrame:
    """One row per EVT class inside the AOI, sorted by added-back area."""
    inside = provenance != OUTSIDE
    prov, bits = provenance[inside], method_bits[inside]
    if (bits[~np.isin(prov, HOLES)] > 0).any():
        raise ValueError("method proposals outside the hole universe: the rasters are not one run")
    codes, index = np.unique(evt[inside], return_inverse=True)
    names = legend.set_index("VALUE")[["EVT_NAME", "EVT_PHYS"]]
    unknown = sorted(set(codes.tolist()) - set(names.index))
    if unknown:
        raise ValueError(f"EVT codes missing from the legend: {unknown}")

    def acres(mask: np.ndarray) -> np.ndarray:
        return np.bincount(index[mask], minlength=codes.size) * acres_per_pixel

    table = pd.DataFrame({
        "VALUE": codes,
        "aoi_ac": acres(np.ones(prov.shape, dtype=bool)),
        "published_ac": acres(prov == Provenance.PUBLISHED),
        "hole_ac": acres(np.isin(prov, HOLES)),
        "proposed_ac": acres(bits > 0),
        "added_back_ac": acres(np.isin(prov, ADDED)),
        "left_out_ac": acres(prov == Provenance.UNMAPPED_LAND),
    }).join(names, on="VALUE")
    return _with_shares(table).sort_values(
        ["added_back_ac", "proposed_ac", "aoi_ac"], ascending=False, ignore_index=True)


def by_evt_phys(table: pd.DataFrame) -> pd.DataFrame:
    """The class table rolled up to LANDFIRE physiognomy (``EVT_PHYS``)."""
    phys = table.groupby("EVT_PHYS", as_index=False)[AREAS].sum()
    return _with_shares(phys).sort_values("added_back_ac", ascending=False, ignore_index=True)


def _with_shares(table: pd.DataFrame) -> pd.DataFrame:
    table = table.copy()
    table["proposed_share"] = table.proposed_ac / table.proposed_ac.sum()
    table["added_back_share"] = table.added_back_ac / table.added_back_ac.sum()
    table["left_out_share_of_holes"] = table.left_out_ac / table.hole_ac.where(table.hole_ac > 0)
    return table[[c for c in COLUMNS if c in table.columns]]


def read_on_grid(path: Path, grid) -> np.ndarray:
    """Band 1 of ``path`` under an open raster's grid; refuses anything not pixel-registered."""
    with rasterio.open(path) as src:
        assert_project_crs(src, str(path))
        assert_project_crs(grid, "target grid")
        window = from_bounds(*grid.bounds, transform=src.transform)
        offsets = np.array([window.col_off, window.row_off])
        if src.res != grid.res or np.abs(offsets - np.round(offsets)).max() > 1e-6:
            raise ValueError(f"{path} is not aligned with the target grid (window {window})")
        window = Window(int(round(window.col_off)), int(round(window.row_off)),
                        grid.width, grid.height)
        return src.read(1, window=window)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR,
                        help="ungated three-method run (treemap2022_provenance.tif, "
                             "add_back_method_bits.tif)")
    parser.add_argument("--landfire", type=Path, default=LANDFIRE,
                        help="LF2022_EVT_CONUS directory holding Tif/ and CSV_Data/")
    parser.add_argument("--out-dir", type=Path, default=HERE)
    args = parser.parse_args(argv)

    with rasterio.open(args.run_dir / "treemap2022_provenance.tif") as grid:
        provenance = grid.read(1)
        evt = read_on_grid(args.landfire / "Tif" / "LF2022_EVT_CONUS.tif", grid)
    with rasterio.open(args.run_dir / "add_back_method_bits.tif") as src:
        method_bits = src.read(1)
    legend = pd.read_csv(args.landfire / "CSV_Data" / "LF2022_EVT.csv")

    classes = by_evt_class(evt, provenance, method_bits, legend)
    phys = by_evt_phys(classes)
    classes.to_csv(args.out_dir / "addback_by_evt2022_class.csv", index=False, float_format="%.4f")
    phys.to_csv(args.out_dir / "addback_by_evt2022_phys.csv", index=False, float_format="%.4f")

    total = classes[AREAS].sum()
    print(f"AOI {total.aoi_ac:,.1f} ac; holes {total.hole_ac:,.1f}; "
          f"proposed {total.proposed_ac:,.1f}; added back {total.added_back_ac:,.1f}")
    with pd.option_context("display.width", 200, "display.max_colwidth", 48):
        print(classes.head(15)[["VALUE", "EVT_NAME", "proposed_ac", "added_back_ac",
                                "left_out_ac", "added_back_share"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
