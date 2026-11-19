"""#171 demo: what LANDFIRE disturbance changes when it joins Obata and Hansen as a cut-year detector.

Reads a run folder's add-back pixels and dates them twice, with Obata and Hansen only and
with LANDFIRE as well (:func:`add_back_stand_age.cut_year`). Writes nothing into the run.

    uv run python experiments/2026-11-09_landfire-cut-year/demo.py <run> <label> \
        --obata-tif <lastDist.tif> --hansen-tif <hansen.tif> --landfire-dist-dir <landfire/>

Writes ``outputs/<label>.json``: the add-back acres whose stand age changes, those Obata
and Hansen leave undated that LANDFIRE dates, and the acres by age source before and after.
The baseline is recomputed rather than read from a run's ``add_back_cut_year.tif``, which
may predate the 2026-10-12 amendment (any year from 2001 dates a pixel).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from pipeline.s1_initial_state.add_back_ids import AGE_SOURCE_BY_DETECTOR
from pipeline.s1_initial_state.add_back_stand_age import CutYearDetector, read_cut_year, stand_age
from pipeline.s1_initial_state.county_improvement import Grid, TreeMapProvenance
from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL

STRIP_ROWS = 2048
OUT = Path(__file__).parent / "outputs"


def _sources(detector: np.ndarray) -> Counter:
    bits, pixels = np.unique(detector, return_counts=True)
    return Counter({str(AGE_SOURCE_BY_DETECTOR[CutYearDetector(int(b))]): int(n)
                    for b, n in zip(bits, pixels)})


def demo(run: Path, obata: Path, hansen: Path, landfire: Path) -> dict:
    with rasterio.open(run / "treemap2022_provenance.tif") as src:
        transform, (height, width) = src.transform, src.shape
    pixels = changed = newly_dated = 0
    before, after = Counter(), Counter()
    age_shift = Counter()
    for row in range(0, height, STRIP_ROWS):
        rows = min(STRIP_ROWS, height - row)
        window = Window(0, row, width, rows)
        grid = Grid(rasterio.windows.transform(window, transform), (rows, width))
        with rasterio.open(run / "treemap2022_provenance.tif") as src:
            added = TreeMapProvenance.added_back(src.read(1, window=window))
        if not added.any():
            continue
        year0, det0 = read_cut_year(grid, obata=obata, hansen=hansen, landfire=None, where=added)
        year1, det1 = read_cut_year(grid, obata=obata, hansen=hansen, landfire=landfire, where=added)
        age0, age1 = stand_age(year0), stand_age(year1)
        pixels += int(added.sum())
        changed += int((age0 != age1).sum())
        newly_dated += int(((year0 == 0) & (year1 > 0)).sum())
        moved = (age0 != age1) & (year0 > 0)
        age_shift.update((age1[moved] - age0[moved]).tolist())
        before.update(_sources(det0))
        after.update(_sources(det1))

    def acres(n: int) -> float:
        return round(n * ACRES_PER_PIXEL, 1)

    return {
        "run": str(run),
        "add_back_acres": acres(pixels),
        "acres_whose_age_changes": acres(changed),
        "undated_acres_landfire_dates": acres(newly_dated),
        "dated_acres_whose_age_changes_by_years": {str(k): acres(v) for k, v in sorted(age_shift.items())},
        "acres_by_age_source_obata_hansen": {k: acres(v) for k, v in sorted(before.items())},
        "acres_by_age_source_with_landfire": {k: acres(v) for k, v in sorted(after.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("label")
    parser.add_argument("--obata-tif", type=Path, required=True)
    parser.add_argument("--hansen-tif", type=Path, required=True)
    parser.add_argument("--landfire-dist-dir", type=Path, required=True)
    args = parser.parse_args()
    report = demo(args.run, args.obata_tif, args.hansen_tif, args.landfire_dist_dir)
    OUT.mkdir(exist_ok=True)
    (OUT / f"{args.label}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
