"""AlphaEarth hole detector: tell a TreeMap hole from TreeMap's own forest, from the 2022 embedding.

The bookends gate (:mod:`classify_holes`) scores holes against clearcut exemplars. This is a
separate source of evidence. It learns what TreeMap's holes look like next to the forest
TreeMap does map, and it is fitted on the run area itself:

1. **Sample.** A random draw of pixels from a :mod:`county_improvement` run: published
   TreeMap forest (label 0) and TreeMap holes on land (label 1), where TreeMap published no
   plot, whatever the add-back later did with them. Water and the area outside are never drawn.
2. **Embed.** The AlphaEarth annual embedding for 2022, the TreeMap vintage, at each pixel
   centre (``embed_holes`` caps feature years at 2022).
3. **Fit.** A logistic regression on the raw 64 bands, so Earth Engine evaluates it exactly
   (:func:`embed_holes.probability_image`). Its threshold is fitted, not chosen: the cut on
   out-of-fold probabilities (GroupKFold over 10 km blocks) that maximizes balanced
   accuracy. A label-shuffle AUC is reported beside the real one.
4. **Score.** The model over the run's grid: ``alphaearth_hole_prob.tif``, band 1 the hole
   probability times ``SCORE_SCALE``.

Holes include the forest TreeMap missed, so a hole the detector scores **below** the
threshold looks like TreeMap's forest (:func:`treemap_like`): that is the evidence. A hole
scored well above it looks like the rest of the holes (fields, towns, mines): that is what
a screen removes.

    uv run python -m pipeline.s1_initial_state.alphaearth_hole_detector <run>/aoi_5county --out-dir <dir>
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import Affine

from pipeline.s1_initial_state.county_improvement import TreeMapProvenance as TP
from pipeline.s1_initial_state.embed_holes import (
    CHUNK,
    EMBEDDING_BANDS,
    EMBEDDING_SCALE_M,
    MAX_FEATURE_YEAR,
    SCORE_SCALE,
    annual_embedding,
    check_feature_years,
)

BLOCK_M = 10_000            # spatial CV block edge, metres
FEATURE_YEAR = MAX_FEATURE_YEAR
HOLE_CODES = (TP.UNMAPPED_LAND, TP.ADDED_BACK, TP.ADDED_BACK_YOUNG)
LABEL = {"treemap_forest": 0, "hole": 1}


class SampleClass(StrEnum):
    TREEMAP_FOREST = "treemap_forest"   # published TreeMap plot
    HOLE = "hole"                       # land TreeMap published no plot on


# ── the sample ──────────────────────────────────────────────────────────────────────────


def sample_pixels(provenance: np.ndarray, transform: Affine, n_per_class: int,
                  seed: int) -> pd.DataFrame:
    """``n_per_class`` random pixels of each :class:`SampleClass`, without replacement.

    Columns: ``sample_class, row, col, x, y`` (EPSG:5070 pixel centre), ``lon, lat`` and
    ``block``, the 10 km cell used to group the cross-validation.
    """
    rng = np.random.default_rng(seed)
    masks = {SampleClass.TREEMAP_FOREST: provenance == TP.PUBLISHED,
             SampleClass.HOLE: np.isin(provenance, HOLE_CODES)}
    frames = []
    for cls, mask in masks.items():
        flat = np.flatnonzero(mask)
        if flat.size < n_per_class:
            raise ValueError(f"{cls} has {flat.size} pixels, fewer than the {n_per_class} asked for")
        rows, cols = np.unravel_index(np.sort(rng.choice(flat, n_per_class, replace=False)), mask.shape)
        frames.append(pd.DataFrame({"sample_class": str(cls), "row": rows, "col": cols}))
    table = pd.concat(frames, ignore_index=True)
    table["x"] = transform.c + (table["col"] + 0.5) * transform.a
    table["y"] = transform.f + (table["row"] + 0.5) * transform.e
    table["block"] = (np.floor(table["x"] / BLOCK_M).astype(int).astype(str) + "_"
                      + np.floor(table["y"] / BLOCK_M).astype(int).astype(str))
    from pyproj import Transformer

    from pipeline.spatial_ref import project_crs

    to_wgs84 = Transformer.from_crs(project_crs(), "EPSG:4326", always_xy=True)
    table["lon"], table["lat"] = to_wgs84.transform(table["x"].to_numpy(), table["y"].to_numpy())
    return table


def sample_embeddings(ee, table: pd.DataFrame, year: int = FEATURE_YEAR) -> pd.DataFrame:
    """``table`` with the 64 AlphaEarth bands of ``year`` at each pixel centre; unsampled rows dropped."""
    check_feature_years([year])
    region = ee.Geometry.Rectangle([table.lon.min() - 0.01, table.lat.min() - 0.01,
                                    table.lon.max() + 0.01, table.lat.max() + 0.01])
    image = annual_embedding(ee, year, region)
    rows = []
    for start in range(0, len(table), CHUNK):
        block = table.iloc[start:start + CHUNK]
        fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([lon, lat]), {"idx": int(i)})
                                   for i, lon, lat in zip(block.index, block.lon, block.lat)])
        sampled = image.sampleRegions(collection=fc, scale=EMBEDDING_SCALE_M, geometries=False,
                                      tileScale=4).getInfo()["features"]
        rows += [{"idx": f["properties"]["idx"], **{b: f["properties"].get(b) for b in EMBEDDING_BANDS}}
                 for f in sampled]
        print(f"  embeddings {min(start + CHUNK, len(table)):,}/{len(table):,}")
    bands = pd.DataFrame(rows).set_index("idx")
    return table.join(bands, how="inner").dropna(subset=list(EMBEDDING_BANDS))


# ── the fit ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HoleDetector:
    coef: tuple[float, ...]
    intercept: float
    threshold: float                     # a hole probability below it looks like TreeMap forest
    feature_year: int = FEATURE_YEAR
    bands: tuple[str, ...] = EMBEDDING_BANDS
    metrics: dict = field(default_factory=dict, compare=False)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> HoleDetector:
        d = json.loads(Path(path).read_text())
        return cls(tuple(d["coef"]), d["intercept"], d["threshold"], d["feature_year"],
                   tuple(d["bands"]), d.get("metrics", {}))

    def as_embed_model(self) -> dict:
        """The keys :func:`embed_holes.probability_image` reads."""
        return {"feature_year": self.feature_year, "bands": list(self.bands),
                "coef": list(self.coef), "intercept": self.intercept}


def fit_threshold(y: np.ndarray, prob: np.ndarray) -> float:
    """The cut (a pixel is a hole at ``prob >= cut``) maximizing balanced accuracy; ties take the lowest."""
    y = np.asarray(y)
    best, best_ba = None, -1.0
    for cut in np.unique(prob):
        hole = prob >= cut
        ba = (hole[y == 1].mean() + (~hole[y == 0]).mean()) / 2
        if ba > best_ba + 1e-12:
            best, best_ba = float(cut), ba
    return best


def _logistic(seed: int):
    from sklearn.linear_model import LogisticRegression

    return LogisticRegression(max_iter=2000, random_state=seed)


def fit_hole_detector(x: np.ndarray, y: np.ndarray, groups: np.ndarray, seed: int) -> HoleDetector:
    """Fit on all rows; fit the threshold and report metrics on block-grouped out-of-fold scores."""
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import GroupKFold, cross_val_predict

    cv = GroupKFold(n_splits=min(5, len(np.unique(groups))))
    prob = cross_val_predict(_logistic(seed), x, y, groups=groups, cv=cv, method="predict_proba")[:, 1]
    shuffled = np.random.default_rng(seed).permutation(y)
    prob_shuffled = cross_val_predict(_logistic(seed), x, shuffled, groups=groups, cv=cv,
                                      method="predict_proba")[:, 1]
    threshold = fit_threshold(y, prob)
    hole = prob >= threshold
    model = _logistic(seed).fit(x, y)
    metrics = {
        "auc_block_cv": float(roc_auc_score(y, prob)),
        "auc_label_shuffle": float(roc_auc_score(shuffled, prob_shuffled)),
        "balanced_accuracy_block_cv": float((hole[y == 1].mean() + (~hole[y == 0]).mean()) / 2),
        "treemap_forest_called_forest": float((~hole[y == 0]).mean()),
        "holes_called_forest": float((~hole[y == 1]).mean()),
        "n_treemap_forest": int((y == 0).sum()),
        "n_hole": int((y == 1).sum()),
        "n_blocks": int(len(np.unique(groups))),
    }
    return HoleDetector(tuple(float(c) for c in model.coef_[0]), float(model.intercept_[0]),
                        threshold, metrics=metrics)


def hole_probability(detector: HoleDetector, x: np.ndarray) -> np.ndarray:
    """The detector's hole probability, as Earth Engine evaluates it."""
    z = np.asarray(x, dtype=float) @ np.asarray(detector.coef) + detector.intercept
    return 1.0 / (1.0 + np.exp(-z))


def treemap_like(prob: np.ndarray, threshold: float) -> np.ndarray:
    """Hole pixels the detector cannot tell from TreeMap forest: the AlphaEarth evidence."""
    return np.asarray(prob) < threshold


# ── the raster ──────────────────────────────────────────────────────────────────────────


def write_score_raster(ee, detector: HoleDetector, like: Path, out: Path) -> None:
    """Evaluate ``detector`` on ``like``'s 30 m grid; band 1 = hole probability * SCORE_SCALE (uint16)."""
    import tempfile

    from rasterio import windows

    from pipeline.s1_initial_state.embed_holes import canvas_offsets, probability_image, tile_download_params
    from pipeline.s1_initial_state.score_holes_statewide import _fetch, grid_tiles

    with rasterio.open(like) as src:
        transform, height, width, crs = src.transform, src.height, src.width, src.crs
    left, top = transform.c, transform.f
    region = ee.Geometry.Rectangle([left, top - height * 30, left + width * 30, top],
                                   proj="EPSG:5070", geodesic=False)
    image = (probability_image(ee, detector.as_embed_model(), region)
             .multiply(SCORE_SCALE).round().toUint16())
    canvas = np.zeros((height, width), dtype=np.uint16)
    tiles = grid_tiles(transform, height, width)
    with tempfile.TemporaryDirectory() as tmp:
        for i, (window, bounds) in enumerate(tiles, start=1):
            path = Path(tmp) / f"tile_{i}.tif"
            _fetch(image.getDownloadURL(tile_download_params(bounds)), path)
            with rasterio.open(path) as t:
                row0, col0 = canvas_offsets(t.transform, (left, top))
                if (row0, col0) != (int(window.row_off), int(window.col_off)):
                    raise ValueError(f"tile {i} landed at {(row0, col0)}, expected {window}")
                canvas[windows.Window(col0, row0, t.width, t.height).toslices()] = t.read(1)
            print(f"  score tile {i}/{len(tiles)}")
    profile = dict(driver="GTiff", height=height, width=width, count=1, dtype="uint16", crs=crs,
                   transform=transform, nodata=0, compress="deflate", tiled=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(canvas, 1)
        dst.update_tags(description=f"AlphaEarth {detector.feature_year} hole probability * {SCORE_SCALE}; "
                                    f"below {detector.threshold:.4f} looks like TreeMap forest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help="a stitched run folder holding treemap2022_provenance.tif")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--n-per-class", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=20261005)
    parser.add_argument("--skip-raster", action="store_true", help="fit only; do not score the grid")
    args = parser.parse_args()

    from pipeline.s1_initial_state.embed_holes import init_ee

    args.out_dir.mkdir(parents=True, exist_ok=True)
    provenance_tif = args.run_dir / "treemap2022_provenance.tif"
    with rasterio.open(provenance_tif) as src:
        provenance, transform = src.read(1), src.transform
    ee = init_ee()
    table = sample_embeddings(ee, sample_pixels(provenance, transform, args.n_per_class, args.seed))
    table.to_csv(args.out_dir / "alphaearth_hole_sample.csv", index=False)
    x = table[list(EMBEDDING_BANDS)].to_numpy(float)
    y = table["sample_class"].map(LABEL).to_numpy()
    detector = fit_hole_detector(x, y, table["block"].to_numpy(), args.seed)
    detector.save(args.out_dir / "alphaearth_hole_model.json")
    print(json.dumps({"threshold": detector.threshold, **detector.metrics}, indent=2))
    if not args.skip_raster:
        write_score_raster(ee, detector, provenance_tif, args.out_dir / "alphaearth_hole_prob.tif")


if __name__ == "__main__":
    main()
