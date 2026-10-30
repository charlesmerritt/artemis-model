"""Step 8: compare the minimum patch area variants against the 5 ac build.

The add-back decision is per pixel; ``county_improvement`` then drops 8-connected patches under
``min_acres`` (``finalize_add_back.apply_mmu``). LETO and the speckle cleanup build stands
downstream, so this asks what the raster minimum still buys; the answer made 0 ac Maine's
default (``common.BASE_MIN_ACRES``). Each variant is the same run with
``ARTEMIS_MAINE_MIN_ACRES`` set (``run_mmu_variants.sh``). Per variant:

- **Raster:** acres the rule accepted, added back and dropped; added-back acres by stratum and
  patch size (8-connected, statewide).
- **Extra pixels** (added back here, a hole in the 5 ac build): their stratum and LANDFIRE
  2022 lifeform, overall and per stratum.
- **Fate in LETO:** each added-back pixel's LETO stand is 5 ac or more (``in_stand``), under
  5 ac and absorbed by the speckle cleanup (``absorbed``), or under 5 ac with no neighbour
  (``island``). Read from ``<fips>_seg_leto.tif`` and ``<fips>_seg.tif``.
- **Stands and young plots:** step 6's QA and step 3's young-plot match levels.

    uv run python experiments/2026-10-19_maine-v4-leto/08_compare.py [--min-acres 5 1 0]

Writes work/08_compare.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

sys.path.insert(0, str(c.REPO))
from pipeline.s1_initial_state import county_improvement as ci  # noqa: E402

ACRES = c.PIXEL ** 2 / 4046.8564224
ADDED_BACK = (2, 4)            # young_plots provenance: donor as is, established young
MIN_STAND_PIXELS = 23          # LETO's 5 ac at 30 m (05_leto.MIN_STAND_PIXELS)
PATCH_BINS = (0, 1, 5, 20, np.inf)
STRATA = (1, 2, 3, 4)
REFERENCE_MIN_ACRES = 5.0      # county_improvement's default, Maine's first build
OUT_JSON = c.WORK / "08_compare.json"


def acres(n) -> float:
    return round(float(n) * ACRES, 1)


def added_mask(run: c.RunPaths) -> tuple[np.ndarray, ci.Grid]:
    with rasterio.open(run.dir / "young_plots/treemap2022_provenance.tif") as src:
        prov = src.read(1)
        grid = ci.Grid(src.transform, prov.shape)
    return np.isin(prov, ADDED_BACK), grid


def patch_sizes(added: np.ndarray) -> dict[str, float]:
    labels, _ = ndimage.label(added, structure=np.ones((3, 3)))
    pixels = np.bincount(labels.ravel())[1:]
    pixels = pixels[pixels > 0]
    size = pixels * ACRES
    out = {}
    for lo, hi in zip(PATCH_BINS[:-1], PATCH_BINS[1:]):
        inside = (size >= lo) & (size < hi)
        name = f"{lo:g}-{hi:g} ac" if np.isfinite(hi) else f">={lo:g} ac"
        out[name] = {"patches": int(inside.sum()), "acres": acres(pixels[inside].sum())}
    return out


def evt2022(grid: ci.Grid) -> tuple[np.ndarray, pd.Series]:
    """LANDFIRE EVT 2022 on ``grid`` and its ``VALUE -> EVT_LF`` legend."""
    tif, csv = c.improvement_inputs().evt(2022)
    with rasterio.open(tif) as src:
        evt = ci.read_on_grid(tif, grid, fill=src.nodata)
    return evt, pd.read_csv(csv).set_index("VALUE")["EVT_LF"]


def lifeforms(evt: np.ndarray, legend: pd.Series, mask: np.ndarray) -> dict[str, float]:
    if not mask.any():
        return {}
    counts = pd.Series(evt[mask]).value_counts()
    by_lf = counts.groupby(legend.reindex(counts.index).fillna("unknown").to_numpy()).sum()
    return {k: acres(v) for k, v in by_lf.sort_values(ascending=False).items()}


def leto_fate(run: c.RunPaths, base_added_tif: Path) -> dict[str, dict[str, float]]:
    """Added-back acres by their LETO stand's fate, for all and for the extra pixels."""
    fate = {"all": dict.fromkeys(("in_stand", "absorbed", "island"), 0),
            "extra": dict.fromkeys(("in_stand", "absorbed", "island"), 0)}
    for seg_path in sorted((run.work / "counties").glob("*_seg_leto.tif")):
        fips = seg_path.name.split("_")[0]
        with rasterio.open(seg_path) as src:
            leto = src.read(1)
            grid = ci.Grid(src.transform, leto.shape)
        with rasterio.open(seg_path.with_name(f"{fips}_seg.tif")) as src:
            clean = src.read(1)
        prov = ci.read_on_grid(run.dir / "young_plots/treemap2022_provenance.tif", grid, fill=0)
        base = ci.read_on_grid(base_added_tif, grid, fill=0)
        added = np.isin(prov, ADDED_BACK) & (leto > 0)
        small_leto = (np.bincount(leto.ravel()) < MIN_STAND_PIXELS)[leto]
        small_clean = (np.bincount(clean.ravel()) < MIN_STAND_PIXELS)[clean]
        classes = {"in_stand": ~small_leto, "absorbed": small_leto & ~small_clean,
                   "island": small_leto & small_clean}
        extra = added & ~np.isin(base, ADDED_BACK)
        for name, cls in classes.items():
            fate["all"][name] += int((added & cls).sum())
            fate["extra"][name] += int((extra & cls).sum())
    return {k: {n: acres(v) for n, v in d.items()} for k, d in fate.items()}


def compare(min_acres: float, base_added: np.ndarray, base_tif: Path) -> dict:
    run = c.run_paths(min_acres)
    improve = json.loads((run.work / "03_improve.json").read_text())
    young = json.loads((run.work / "03_young.json").read_text())
    qa = json.loads((run.outputs / "me_statewide_qa.json").read_text())
    added, grid = added_mask(run)
    with rasterio.open(run.dir / "bookend_strata.tif") as src:
        strata = src.read(1)
    evt, legend = evt2022(grid)
    extra = added & ~base_added
    return {
        "min_acres": min_acres,
        "raster": {
            "rule_accepted_before_mmu_acres": improve["rule_accepted_before_mmu_acres"],
            "added_back_acres": improve["added_back_acres"],
            "dropped_by_mmu_or_no_donor_acres": improve["dropped_by_mmu_or_no_donor_acres"],
            "patches": improve["patches"],
            "patch_rejected_acres": improve["evt_gate"]["patch_rejected_acres"],
            "added_back_acres_by_mode": improve["added_back_acres_by_mode"],
            "added_back_acres_by_stratum": {f"S{k}": acres((added & (strata == k)).sum())
                                            for k in STRATA},
            "patch_sizes": patch_sizes(added),
        },
        "extra_vs_5ac": {
            "acres": acres(extra.sum()),
            "missing_acres": acres((base_added & ~added).sum()),
            "by_stratum": {f"S{k}": acres((extra & (strata == k)).sum()) for k in STRATA},
            "evt2022_lifeform": lifeforms(evt, legend, extra),
            "evt2022_lifeform_by_stratum": {f"S{k}": lifeforms(evt, legend, extra & (strata == k))
                                            for k in STRATA},
        },
        "leto_fate": leto_fate(run, base_tif),
        "young_plot_match_acres": young["acres_by_match_level"],
        "stands": {k: qa[k] for k in (
            "parent_stands", "parent_acre_median", "leto_parent_stands", "speckle_absorbed_stands",
            "speckle_absorbed_acres", "isolated_small_stands", "parent_stands_under_5_ac",
            "parent_acres_under_5_ac", "management_units", "riparian_units", "added_back_acres",
            "neighbour_kept_acres", "forest_acres_cells")},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-acres", type=float, nargs="+", default=[5.0, 1.0, 0.0])
    args = parser.parse_args()
    base = c.run_paths(REFERENCE_MIN_ACRES)
    base_added, _ = added_mask(base)
    base_tif = base.dir / "young_plots/treemap2022_provenance.tif"
    report = {f"{m:g}": compare(m, base_added, base_tif) for m in args.min_acres}
    c.write_json(OUT_JSON, report)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
