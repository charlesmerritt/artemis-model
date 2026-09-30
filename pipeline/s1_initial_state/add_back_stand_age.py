"""Stand age of every add-back pixel, from its Landsat cut year, and the acres in each age class.

ADR 0003 (``docs/adr/0003-dated-stand-age-and-young-plots.md``) dates each add-back pixel
instead of starting it at a fixed age. This module is the first step of that decision:
the cut year, the stand age it implies, and the add-back acres in each age class. It runs
over a :mod:`county_improvement` run folder's stitched rasters, so an existing run can be
aged without being rebuilt:

    uv run python -m pipeline.s1_initial_state.add_back_stand_age <run>/aoi_5county --obata-tif <lastDist.tif> --hansen-tif <hansen_aoi.tif>

- **Cut year.** The latest year Obata (``lastDist``, 2010-2022) or Hansen (``lossyear``,
  2001-2022) detects. Hansen's 30% canopy-in-2000 gate decides whether Hansen *proposes*
  a pixel, not whether its loss *dates* one: ground that was sparse in 2000 and lost
  canopy later was still cut then (2,185 ac of the gated union run). Where the two
  detectors disagree by more than 2 years a spectral tie-break is owed (#90); until it
  lands the latest year stands.
- **Stand age.** Years since establishment, as of 2022. Establishment is the year after
  the cut year, so a 2022 cut would be -1; it is 0. A pixel neither detector dates is
  ``UNDATED``: under the union rule, the pixels only the bookends propose.
- **Age classes.** Named by their first and last year; the last class is open-ended. The
  default is #93's proposal, to be matched to FIA's reporting classes for the acceptance
  check (``--age-class-starts``).

Writes into the run folder:

- ``add_back_cut_year.tif``: the cut year of each dated add-back pixel (uint16, 0 elsewhere).
- ``add_back_cut_year_detector.tif``: the :class:`CutYearDetector` bits that date that year.
- ``add_back_stand_age.csv``: ``agreement, stand_age, age_class, pixels, acres``. The
  ``union`` rows count every accepted pixel; the ``at_least_two`` rows count the accepted
  pixels two methods propose. From a union run that subset is not an any-two run, whose
  5 ac minimum would apply to the any-two mask itself (#86).

>>> stand_age(np.array([2014, 2022, 0])).tolist()
[7, 0, -1]
>>> age_classes(np.array([7, 0, UNDATED]), DEFAULT_AGE_CLASS_STARTS).tolist()
['6-10', '0-5', 'undated']
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from enum import IntFlag
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

from pipeline.s1_initial_state.add_back_methods import METHOD_BIT, ConsensusRule
from pipeline.s1_initial_state.county_improvement import (
    Grid,
    TreeMapProvenance,
    _write,
    HANSEN_LOSSYEAR,
    obata_rule,
    read_on_grid,
)
from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL

VINTAGE = 2022                # TreeMap 2022: stand age is as of this year
ESTABLISHMENT_LAG = 1         # planted the year after the harvest
HANSEN_BASE_YEAR = 2000       # Hansen stores lossyear as years since 2000
UNDATED = -1                  # the stand age of a pixel neither detector dates
UNDATED_CLASS = "undated"
DEFAULT_AGE_CLASS_STARTS = (0, 6, 11, 21, 41)   # #93: 0-5, 6-10, 11-20, 21-40, 41+
MIN_VOTES = {ConsensusRule.UNION: 1, ConsensusRule.AT_LEAST_TWO: 2}


class CutYearDetector(IntFlag):
    """The detectors whose year is the cut year: both bits when they agree. 0 is undated."""

    OBATA = 1
    HANSEN = 2


def cut_year(obata: np.ndarray | None,
             hansen_lossyear: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """The latest year either detector dates (0 where neither does), and which dates it.

    ``obata`` is geepipe ``lastDist``; ``hansen_lossyear`` counts years since 2000. A
    detector that has not run is ``None``.
    """
    years: dict[CutYearDetector, np.ndarray] = {}
    if obata is not None:
        years[CutYearDetector.OBATA] = np.where(obata_rule(obata), obata, 0)
    if hansen_lossyear is not None:
        dated = (hansen_lossyear >= HANSEN_LOSSYEAR[0]) & (hansen_lossyear <= HANSEN_LOSSYEAR[1])
        years[CutYearDetector.HANSEN] = np.where(
            dated, hansen_lossyear.astype(np.uint16) + HANSEN_BASE_YEAR, 0)
    if not years:
        raise ValueError("no detector has run: supply Obata, Hansen or both")
    latest = np.maximum.reduce(list(years.values())).astype(np.uint16)
    detector = np.zeros(latest.shape, dtype=np.uint8)
    for flag, year in years.items():
        detector[(year == latest) & (latest > 0)] |= np.uint8(flag)
    return latest, detector


def stand_age(years: np.ndarray) -> np.ndarray:
    """Years since establishment as of ``VINTAGE``, never below 0; ``UNDATED`` where ``years`` is 0."""
    years = np.asarray(years, dtype=np.int32)
    age = np.maximum(VINTAGE - (years + ESTABLISHMENT_LAG), 0)
    return np.where(years > 0, age, UNDATED).astype(np.int16)


def age_class_labels(starts: Sequence[int]) -> list[str]:
    """Class names from each class's first year; the last class is open-ended."""
    starts = list(starts)
    if not starts or starts[0] != 0 or any(b <= a for a, b in zip(starts, starts[1:])):
        raise ValueError(f"age class starts must begin at 0 and rise strictly: {starts}")
    return [f"{a}-{b - 1}" for a, b in zip(starts, starts[1:])] + [f"{starts[-1]}+"]


def age_classes(ages: np.ndarray, starts: Sequence[int]) -> np.ndarray:
    """Each stand age's class label; an ``UNDATED`` age is ``"undated"``."""
    labels = np.array(age_class_labels(starts) + [UNDATED_CLASS], dtype=object)
    ages = np.asarray(ages)
    index = np.searchsorted(starts, ages, side="right") - 1
    return labels[np.where(ages == UNDATED, len(starts), index)]


def stand_age_table(years: np.ndarray, provenance: np.ndarray, method_bits: np.ndarray,
                    starts: Sequence[int] = DEFAULT_AGE_CLASS_STARTS) -> pd.DataFrame:
    """Add-back pixels and acres by stand age: every accepted pixel, then the two-method subset.

    Rows run by age within each ``agreement``, undated last; ``stand_age`` is ``<NA>`` there.
    """
    added = TreeMapProvenance.added_back(provenance)
    votes = sum(((method_bits & bit) != 0).astype(np.uint8) for bit in METHOD_BIT.values())
    ages = stand_age(years)
    rows = []
    for rule, min_votes in MIN_VOTES.items():
        age, pixels = np.unique(ages[added & (votes >= min_votes)], return_counts=True)
        order = np.lexsort((age, age == UNDATED))
        rows += [(str(rule), int(a), int(n)) for a, n in zip(age[order], pixels[order])]
    table = pd.DataFrame(rows, columns=["agreement", "stand_age", "pixels"])
    table.insert(2, "age_class", age_classes(table["stand_age"].to_numpy(), starts))
    table["acres"] = table["pixels"] * ACRES_PER_PIXEL
    table["stand_age"] = table["stand_age"].astype("Int64").mask(table["stand_age"] == UNDATED)
    return table


def write_stand_age(run_dir: Path, *, obata: Path | None = None, hansen: Path | None = None,
                    starts: Sequence[int] = DEFAULT_AGE_CLASS_STARTS) -> pd.DataFrame:
    """Date and age a run folder's add-back pixels; write the rasters and the table into it."""
    with rasterio.open(run_dir / "treemap2022_provenance.tif") as src:
        provenance = src.read(1)
        grid = Grid(src.transform, provenance.shape)
    with rasterio.open(run_dir / "add_back_method_bits.tif") as src:
        bits = src.read(1)
    years, detector = cut_year(
        None if obata is None else read_on_grid(obata, grid, fill=0),
        None if hansen is None else read_on_grid(hansen, grid, fill=0))       # band 1 lossyear
    not_added = ~TreeMapProvenance.added_back(provenance)
    years[not_added] = 0
    detector[not_added] = 0
    _write(run_dir / "add_back_cut_year.tif", years, grid.transform, 0,
           "latest Obata/Hansen cut year of each add-back pixel; 0 undated or not add-back")
    _write(run_dir / "add_back_cut_year_detector.tif", detector, grid.transform, 0,
           "detectors dating the cut year: bit 1 obata, 2 hansen; 0 undated or not add-back")
    table = stand_age_table(years, provenance, bits, starts)
    table.to_csv(run_dir / "add_back_stand_age.csv", index=False, float_format="%.1f")
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path,
                        help="a stitched run folder holding treemap2022_provenance.tif and "
                             "add_back_method_bits.tif, e.g. <out-root>/aoi_5county")
    parser.add_argument("--obata-tif", type=Path, default=None,
                        help="geepipe lastDist raster on the TreeMap grid")
    parser.add_argument("--hansen-tif", type=Path, default=None,
                        help="Hansen GFC raster on the TreeMap grid, band 1 lossyear")
    parser.add_argument("--age-class-starts", type=int, nargs="+",
                        default=list(DEFAULT_AGE_CLASS_STARTS),
                        help="first year of each age class; the last is open-ended")
    args = parser.parse_args()
    table = write_stand_age(args.run_dir, obata=args.obata_tif, hansen=args.hansen_tif,
                            starts=args.age_class_starts)
    by_class = table.pivot_table(index="age_class", columns="agreement", values="acres",
                                 aggfunc="sum", sort=False)
    print(by_class.round(0).to_string())


if __name__ == "__main__":
    main()
