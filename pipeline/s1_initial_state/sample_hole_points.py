"""Draw labelled point samples from the TreeMap hole strata for embedding analysis.

Stage 1 of the hole-rectification workflow. Turns
``data/interim/treemap_holes/treemap_hole_strata.tif`` into a point table that
Earth Engine can sample AlphaEarth embeddings at.

Roles
-----
``anchor_clearcut``  S1 + S2 — definitively managed forest that was clearcut.
                     LANDFIRE says forest (or ``Recently Logged``) in 2016, hole
                     in 2022, forest again in 2024. Regrowth *proves* it was
                     never converted, so these are the positive anchors.
``anchor_nonforest`` S5 restricted to herb / agriculture / shrub lifeforms —
                     stable non-forest that is a *plausible confuser* (pasture,
                     row crop, ruderal grassland). Water, developed and barren
                     are excluded: they make the discrimination trivially easy
                     and inflate accuracy.
``apply``            S3 + S4 — the ambiguous holes the classifier must decide.

Sampling discipline
-------------------
- **Minimum mapping unit.** Patches below ``--min-acres`` are dropped. The strata
  are 31 k connected components with a median of one pixel; single-pixel specks
  are stand-edge and road/riparian noise, not stands.
- **Interior erosion.** Sample points are pulled back one pixel from the patch
  boundary so a 30 m label is not paired with a 10 m embedding that straddles an
  edge.
- **Patch-level, not pixel-level, sampling.** Points are drawn from distinct
  patches where possible, so one large clearcut cannot dominate the training set.
- **Spatial blocks.** Every point carries a 0.25-degree ``block`` id for
  GroupKFold cross-validation. 0.5 degrees, the convention in
  ``notebooks/clearcut_ag_common.py``, yields only 8 groups across this AOI —
  too few for a stable GroupKFold — so ``BLOCK_DEGREES`` halves it to give 19.
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
STRATA_TIF = REPO / "data/interim/treemap_holes/treemap_hole_strata.tif"
OUT_DIR = REPO / "data/interim/treemap_holes"

ACRES_PER_PIXEL = 0.2224
# 0.25 deg blocks give ~24 groups across the AOI; 0.5 deg gave only 8, too few
# for a stable GroupKFold.
BLOCK_DEGREES = 0.25

ANCHOR_CLEARCUT_STRATA = (1, 2)  # S1 cut_pre2016_regrown, S2 cut_2016_2022_regrown
ANCHOR_NONFOREST_STRATA = (5,)  # S5 no_evidence
APPLY_STRATA = (3, 4)  # S3 cut_2016_2022_open, S4 regrown_only

# Only these LF2022 lifeforms make credible "truly leave it out" negatives.
CONFUSER_LIFEFORMS = ("Herb", "Agriculture", "Shrub")


def patch_filter(mask: np.ndarray, min_acres: float, erode: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Return (interior mask, patch-id label array) after dropping sub-MMU patches."""
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3)))
    sizes = np.bincount(labels.ravel())
    keep_ids = np.nonzero(sizes * ACRES_PER_PIXEL >= min_acres)[0]
    keep_ids = keep_ids[keep_ids != 0]
    big = np.isin(labels, keep_ids)
    interior = ndimage.binary_erosion(big, np.ones((3, 3))) if erode else big
    return interior, np.where(interior, labels, 0)


def draw_points(interior: np.ndarray, labels: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """Pick up to `n` pixel indices, spreading across patches before repeating any."""
    rows, cols = np.nonzero(interior)
    if rows.size == 0:
        return np.empty((0, 2), dtype=int)
    patch = labels[rows, cols]
    order = rng.permutation(rows.size)
    rows, cols, patch = rows[order], cols[order], patch[order]

    # Round-robin over patches: rank each pixel within its patch, then sort by rank.
    sort = np.argsort(patch, kind="stable")
    ranks = np.empty(patch.size, dtype=np.int64)
    _, starts = np.unique(patch[sort], return_index=True)
    ranks[sort] = np.arange(patch.size) - np.repeat(starts, np.diff(np.append(starts, patch.size)))
    pick = np.argsort(ranks, kind="stable")[:n]
    return np.column_stack([rows[pick], cols[pick]])


def evt2022_lifeform(shape, bounds, transform) -> np.ndarray:
    """LF2022 EVT lifeform per pixel, for picking hard negatives."""
    from pipeline.s1_initial_state.stratify_treemap_holes import read_evt_window

    _, lifeforms = read_evt_window(2022, bounds, shape, transform)
    return lifeforms


def build_table(strata_tif: Path, per_role: int, min_acres: float, seed: int) -> pd.DataFrame:
    with rasterio.open(strata_tif) as src:
        strata = src.read(1)
        transform, crs, bounds = src.transform, src.crs, src.bounds

    lifeform = evt2022_lifeform(strata.shape, bounds, transform)
    confuser = np.isin(lifeform, CONFUSER_LIFEFORMS)

    roles = {
        "anchor_clearcut": np.isin(strata, ANCHOR_CLEARCUT_STRATA),
        "anchor_nonforest": np.isin(strata, ANCHOR_NONFOREST_STRATA) & confuser,
        "apply": np.isin(strata, APPLY_STRATA),
    }

    rng = np.random.default_rng(seed)
    frames = []
    for role, mask in roles.items():
        interior, labels = patch_filter(mask, min_acres)
        idx = draw_points(interior, labels, per_role, rng)
        # Interior fraction is a shape diagnostic: a blocky clearcut survives
        # erosion, a one-pixel vintage-misregistration sliver does not. S2 sits
        # near 2% (edge artifact) while S1 is near 28% (real stands).
        print(f"{role:<17} eligible px={int(mask.sum()):>9,}  "
              f"after MMU+erosion={int(interior.sum()):>8,} "
              f"({interior.sum() / max(mask.sum(), 1):>5.1%} interior)  "
              f"patches={len(np.unique(labels)) - 1:>5,}  sampled={len(idx):>5,}")
        if len(idx) == 0:
            continue
        rows, cols = idx[:, 0], idx[:, 1]
        xs, ys = rasterio.transform.xy(transform, rows, cols)
        lon, lat = warp_transform(crs, "EPSG:4326", xs, ys)
        frames.append(pd.DataFrame({
            "role": role,
            "stratum": strata[rows, cols],
            "patch_id": labels[rows, cols],
            "row": rows,
            "col": cols,
            "lon": np.round(lon, 6),
            "lat": np.round(lat, 6),
            "evt2022_lifeform": lifeform[rows, cols],
        }))

    table = pd.concat(frames, ignore_index=True)
    table["block"] = (
        np.floor(table.lon / BLOCK_DEGREES).astype(int).astype(str)
        + "_"
        + np.floor(table.lat / BLOCK_DEGREES).astype(int).astype(str)
    )
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strata-tif", type=Path, default=STRATA_TIF)
    parser.add_argument("--per-role", type=int, default=1500, help="points per role")
    parser.add_argument("--min-acres", type=float, default=5.0, help="minimum mapping unit")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    table = build_table(args.strata_tif, args.per_role, args.min_acres, args.seed)
    print("\n" + table.groupby(["role", "stratum"]).size().to_string())
    print(f"\nspatial blocks: {table.block.nunique()}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "hole_sample_points.csv"
    table.to_csv(out, index=False)
    print(f"wrote {out} ({len(table):,} points)")


if __name__ == "__main__":
    main()
