"""LANDFIRE annual disturbance as a cut-year detector (ADR 0003, amendment 2026-11-09; #171).

LANDFIRE Annual Disturbance (``Dist``, not ``HDist``), CONUS, 1999-2025: one product per
year, each ``LF<version>_Dist<yy>_CONUS/LF<version>_Dist<yy>_CONUS/`` with ``Tif/`` and
``CSV_Data/``, declared as ``raw.landfire.dist_dir`` in ``config/data_paths.yaml``.
:func:`landfire_cut_year` stacks them onto a run's grid as the latest year a stand-replacing
harvest dates each pixel.

- **Stand-replacing only.** A code dates a cut when its legend's ``DIST_TYPE`` is in
  :data:`STAND_REPLACING`. Thinning, fire and the other types never do. Codes come from
  each product's own ``CSV_Data`` legend: they are not stable across versions.
- **Event year.** ``CALENDAR_YR`` where the legend has it (LF2024+, a fiscal-year product
  spanning two calendar years), else the product's own year: ``YEAR`` and ``DIST_YEAR``
  are not read. They equal the product's year everywhere except LF2008, whose legend labels
  most codes 2009. ``Dist08`` is the 2008 product (``Dist09`` is its own), so 2008 wins.
- **Grids differ by version** (LF2016 is smaller; LF2022 and LF2023 one pixel narrower and
  30 m east). Each product is read onto the grid by
  :func:`~pipeline.s1_initial_state.county_improvement.read_on_grid`, which refuses an
  off-lattice shift. Nodata (32767, -32768 in LF2022_Dist22), background 0, -9999 and the
  -1111 "not mapped" fill are not harvest codes, so they date nothing.

>>> _product_year("99"), _product_year("08")
(1999, 2008)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.s1_initial_state.county_improvement import Grid, read_on_grid



class DisturbanceType(StrEnum):
    """The legend ``DIST_TYPE`` values that date a cut; the other types are not modelled."""

    CLEARCUT = "Clearcut"
    HARVEST = "Harvest"


STAND_REPLACING = frozenset(DisturbanceType)
_PRODUCT = re.compile(r"^LF(\d{4})_Dist(\d{2})_CONUS$")


@dataclass(frozen=True)
class DisturbanceProduct:
    """One year's LANDFIRE disturbance raster and its legend."""

    folder: Path      # the inner ``LF<version>_Dist<yy>_CONUS`` folder
    version: int
    year: int

    @property
    def name(self) -> str:
        return self.folder.name.removesuffix("_CONUS")

    @property
    def tif(self) -> Path:
        return self.folder / "Tif" / f"{self.folder.name}.tif"

    @property
    def legend_csv(self) -> Path:
        return self.folder / "CSV_Data" / f"{self.name}.csv"

    @cached_property
    def legend(self) -> pd.DataFrame:
        return pd.read_csv(self.legend_csv, encoding="utf-8-sig")   # LF2024+ start with a BOM

    @classmethod
    def at(cls, folder: Path) -> DisturbanceProduct | None:
        """The product whose inner folder is ``folder``; ``None`` if it is not named like one."""
        match = _PRODUCT.match(Path(folder).name)
        return cls(Path(folder), int(match[1]), _product_year(match[2])) if match else None


def _product_year(yy: str) -> int:
    return 1900 + int(yy) if int(yy) >= 99 else 2000 + int(yy)


def find_products(root: Path) -> list[DisturbanceProduct]:
    """Every ``LF<version>_Dist<yy>_CONUS`` product under ``root``, by year."""
    found = (DisturbanceProduct.at(outer / outer.name) for outer in Path(root).iterdir()
             if (outer / outer.name).is_dir())
    return sorted((p for p in found if p is not None), key=lambda p: p.year)


def harvest_years(product: DisturbanceProduct) -> pd.Series:
    """The product's stand-replacing codes and the year each dates, indexed by code.

    A row repeated word for word counts once (LF2001_Dist99 repeats some). A code whose
    rows disagree on its type or year is an error.
    """
    legend = product.legend
    year_column = "CALENDAR_YR" if "CALENDAR_YR" in legend.columns else None
    meaning = ["VALUE", "DIST_TYPE"] + ([year_column] if year_column else [])
    distinct = pd.DataFrame(legend.loc[:, meaning]).drop_duplicates()
    repeated = distinct["VALUE"].to_numpy()[distinct["VALUE"].duplicated().to_numpy()]
    if repeated.size:
        raise ValueError(f"{product.name}: code {int(repeated[0])} has more than one meaning")
    harvests = distinct.loc[distinct["DIST_TYPE"].isin(sorted(STAND_REPLACING))]
    codes = harvests["VALUE"].to_numpy(dtype=np.int64)
    years = (harvests[year_column].to_numpy(dtype=np.int64) if year_column
             else np.full(len(codes), product.year, dtype=np.int64))
    return pd.Series(years, index=pd.Index(codes, name="code"), name="year")


def landfire_cut_year(root: Path, grid: Grid, *, window: tuple[int, int]) -> np.ndarray:
    """The latest year in ``window`` (inclusive) a stand-replacing harvest dates each pixel; 0 if none.

    A harvest outside ``window`` is ignored, so a later one past the window never hides one
    inside it. Products with no harvest code in the window are not read.
    """
    products = find_products(root)
    if not products:
        raise FileNotFoundError(f"no LANDFIRE disturbance products under {root}")
    first, last = window
    latest = np.zeros(grid.shape, dtype=np.int16)    # a year fits; half an int32 statewide
    for product in products:
        dated = harvest_years(product)
        in_window = dated.between(first, last).to_numpy()
        if not in_window.any():
            continue
        codes = read_on_grid(product.tif, grid, fill=0)
        if codes.dtype != np.int16:
            raise ValueError(f"{product.name} is {codes.dtype}, not LANDFIRE's int16")
        lookup = np.zeros(1 << 16, dtype=np.int16)      # every int16 code, as its uint16 bits
        lookup[dated.index.to_numpy(np.int16)[in_window].view(np.uint16)] = dated.to_numpy(np.int16)[in_window]
        np.maximum(latest, lookup[codes.view(np.uint16)], out=latest)
    return latest
