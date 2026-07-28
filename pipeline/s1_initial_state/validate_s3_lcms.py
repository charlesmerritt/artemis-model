"""Independent validation of the S3 add-back decision against USFS LCMS.

S3 (cut 2016-22, still non-tree in LANDFIRE 2024) is the stratum with no
regrowth confirmation, so its true positive rate is the weakest link in the
whole method. Everything else in the pipeline is derived from LANDFIRE, which
means it cannot validate itself.

LCMS (USFS Geospatial Technology and Applications Center, Landsat/Sentinel
time-series, annual 1985-2025) is produced by a different group with a different
algorithm from LANDFIRE, so it is a genuine external check. Its **Land_Use**
band is the pointed one: a stand clearcut in 2021 is still *Forest land use* in
2022 even though its *land cover* is grass. That is precisely the distinction
TreeMap loses and this method tries to recover.

Design
------
Four groups, two of them reference bookends whose answer is already known:

    S1                reference POSITIVE - LANDFIRE-proven cut-and-regrown
    S3_accepted       the decision under test
    S3_rejected       the decision under test (complement)
    S5                reference NEGATIVE - stable non-forest

If the classifier is working, S3_accepted should behave like S1 and
S3_rejected like S5 on metrics LCMS derives independently. If S3_accepted and
S3_rejected are indistinguishable, the S3 decision is noise and should be
reported as such.

All rates describe a restricted sampling frame: one-pixel-eroded interiors of
connected patches at least 5 acres. They must not be extrapolated to patch
boundaries, smaller components, or the full accepted/rejected S3 populations.

Note the prior: earlier project work found LCMS tree-removal almost never fires
on statewide confused-class points (``notes/clearcut-vs-agriculture-embeddings.md``).
Land-use and land-cover metrics are therefore reported alongside the removal
metric rather than relying on it alone.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform as warp_transform
from scipy import ndimage

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data/interim/treemap_holes"

LCMS_ASSET = "projects/gtac-data-publish/assets/LCMS/Product_Version/2025-11"
LC_TREES = 1  # Land_Cover: Trees
LU_FOREST = 3  # Land_Use: Forest
CH_TREE_REMOVAL = 9  # Change: Tree Removal
ACRES_PER_PIXEL = 0.2224
CHUNK = 400

PRE_YEARS = (2015, 2016, 2017)  # before / at the start of the S3 cut window
CUT_YEARS = tuple(range(2016, 2023))  # the window S3 is defined over
LATE_YEARS = (2023, 2024)  # after the feature cap, so unusable for training


def build_groups(strata: np.ndarray, add_back: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "S1_reference_positive": (strata == 1) & add_back,
        "S3_accepted": (strata == 3) & add_back,
        "S3_rejected": (strata == 3) & ~add_back,
        "S5_reference_negative": (strata == 5),
    }


def eligible_patch_interiors(mask: np.ndarray, min_acres: float = 5.0) -> np.ndarray:
    """Sampling frame: eroded interiors of connected patches above ``min_acres``."""
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3)))
    sizes = np.bincount(labels.ravel())
    keep = np.nonzero(sizes * ACRES_PER_PIXEL >= min_acres)[0]
    big = np.isin(labels, keep[keep != 0])
    return ndimage.binary_erosion(big, np.ones((3, 3)))


def sample_points(mask: np.ndarray, n: int, transform, crs, rng, min_acres=5.0) -> pd.DataFrame:
    """Interior pixels of patches above the MMU, so labels are not edge-contaminated."""
    interior = eligible_patch_interiors(mask, min_acres)
    rows, cols = np.nonzero(interior)
    if rows.size == 0:
        return pd.DataFrame()
    pick = rng.choice(rows.size, size=min(n, rows.size), replace=False)
    rows, cols = rows[pick], cols[pick]
    xs, ys = rasterio.transform.xy(transform, rows, cols)
    lon, lat = warp_transform(crs, "EPSG:4326", xs, ys)
    return pd.DataFrame({"row": rows, "col": cols,
                         "lon": np.round(lon, 6), "lat": np.round(lat, 6)})


def lcms_stack(ee, years):
    """One multi-band image: Land_Cover / Land_Use / Change for each year."""
    collection = ee.ImageCollection(LCMS_ASSET).filter(ee.Filter.eq("study_area", "CONUS"))
    bands = []
    for year in years:
        img = ee.Image(collection.filter(ee.Filter.eq("year", year)).first())
        bands.append(img.select(["Land_Cover", "Land_Use", "Change"],
                                [f"LC_{year}", f"LU_{year}", f"CH_{year}"]))
    return ee.Image.cat(bands)


def sample_lcms(ee, points: pd.DataFrame, years) -> pd.DataFrame:
    image = lcms_stack(ee, years)
    out = []
    for start in range(0, len(points), CHUNK):
        block = points.iloc[start:start + CHUNK]
        fc = ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([lon, lat]), {"idx": int(i)})
            for i, lon, lat in zip(block.index, block.lon, block.lat)
        ])
        rows = image.sampleRegions(collection=fc, scale=30, geometries=False,
                                   tileScale=4).getInfo()["features"]
        out.extend({"idx": r["properties"]["idx"],
                    **{k: v for k, v in r["properties"].items() if k != "idx"}} for r in rows)
        print(f"    {min(start + CHUNK, len(points)):,}/{len(points):,}")
    return pd.DataFrame(out).set_index("idx").sort_index()


def metrics(frame: pd.DataFrame) -> dict[str, float]:
    """LCMS-derived indicators of 'this is managed forest land'."""
    lu = frame[[c for c in frame if c.startswith("LU_")]]
    lc = frame[[c for c in frame if c.startswith("LC_")]]
    ch = frame[[c for c in frame if c.startswith("CH_")]]

    pre_lc = lc[[f"LC_{y}" for y in PRE_YEARS if f"LC_{y}" in lc]]
    cut_ch = ch[[f"CH_{y}" for y in CUT_YEARS if f"CH_{y}" in ch]]
    late_lc = lc[[f"LC_{y}" for y in LATE_YEARS if f"LC_{y}" in lc]]

    return {
        "n": len(frame),
        "LU_forest_2022": float((lu["LU_2022"] == LU_FOREST).mean()),
        "LU_forest_all_years": float((lu == LU_FOREST).all(axis=1).mean()),
        "LC_trees_pre_cut": float((pre_lc == LC_TREES).any(axis=1).mean()),
        "LC_trees_2022": float((lc["LC_2022"] == LC_TREES).mean()),
        "LC_trees_2024": float((late_lc == LC_TREES).any(axis=1).mean()),
        "tree_removal_2016_2022": float((cut_ch == CH_TREE_REMOVAL).any(axis=1).mean()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-group", type=int, default=600)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out-csv", type=Path, default=DATA / "s3_validation_lcms.csv")
    args = parser.parse_args()

    import ee

    ee.Initialize()

    with rasterio.open(DATA / "treemap_hole_strata.tif") as src:
        strata = src.read(1)
        transform, crs = src.transform, src.crs
    with rasterio.open(DATA / "treemap_add_back_mask.tif") as src:
        add_back = src.read(1) == 1

    years = sorted(set(PRE_YEARS) | set(CUT_YEARS) | set(LATE_YEARS))
    rng = np.random.default_rng(args.seed)
    frames = []
    for name, mask in build_groups(strata, add_back).items():
        points = sample_points(mask, args.per_group, transform, crs, rng)
        print(f"  {name}: {len(points):,} points")
        sampled = sample_lcms(ee, points, years)
        frames.append(points.join(sampled).assign(group=name))

    table = pd.concat(frames, ignore_index=True)
    band_cols = [c for c in table if c[:3] in ("LC_", "LU_", "CH_")]
    table = table.dropna(subset=band_cols)

    summary = pd.DataFrame({g: metrics(d) for g, d in table.groupby("group")}).T
    order = ["S1_reference_positive", "S3_accepted", "S3_rejected", "S5_reference_negative"]
    summary = summary.reindex([o for o in order if o in summary.index])
    print("\n=== LCMS indicators by group ===")
    print(summary.to_string(float_format=lambda v: f"{v:,.3f}"))

    if {"S3_accepted", "S3_rejected"} <= set(summary.index):
        print("\n=== separation on the decision under test ===")
        for metric in ["LU_forest_2022", "LC_trees_pre_cut", "LC_trees_2024", "tree_removal_2016_2022"]:
            a, r = summary.loc["S3_accepted", metric], summary.loc["S3_rejected", metric]
            print(f"  {metric:<24} accepted {a:.3f}  rejected {r:.3f}  lift {a - r:+.3f}")

    table.to_csv(args.out_csv, index=False)
    summary.to_csv(args.out_csv.with_name("s3_validation_summary.csv"))
    print(f"\nwrote {args.out_csv} and s3_validation_summary.csv")


if __name__ == "__main__":
    main()
