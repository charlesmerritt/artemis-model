"""
Correct a classification raster inside a vector polygon, using spectral embeddings.

The situation this addresses: a categorical raster — LANDFIRE EVT is the case in
hand — assigns some classes that are known to be wrong over a particular area. A
clearcut stand carries "Southeastern Ruderal Grassland" long after it is pine
again, because the classifier saw bare ground in its source imagery. The polygon
says *where* the classification is suspect; a list of class values says *which*
labels there are eligible to be overwritten; everything else is evidence.

The method, in one paragraph
---------------------------
Every pixel whose class is **not** on the eligible list is a trusted label,
whether it lies inside the polygon or outside it. Those pixels train a multiclass
classifier on satellite embeddings, so the model learns what each surrounding
class actually looks like — including the classes present inside the polygon that
nobody disputes. The eligible pixels inside the polygon are the apply set: the
model predicts a class for each, and any prediction it is confident enough about
replaces the original value. Pixels the model is unsure about keep the label they
came with, because a wrong correction is worse than an uncorrected pixel that is
already flagged as suspect.

Why the training set includes the polygon's own pixels: an AOI is usually a
managed unit sitting in a landscape it does not resemble. Training only on the
outside makes the model extrapolate across that boundary. The undisputed pixels
inside are the closest thing available to in-situ reference data.

Honesty machinery
-----------------
- **Spatially blocked cross-validation.** Neighbouring 30 m pixels are not
  independent samples; random folds let the model memorise geography and report
  an accuracy that will not survive contact with new ground. Folds are grouped
  into square blocks (``block_m``), following ``s1_initial_state/classify_holes``.
- **A label-shuffle baseline** runs beside the real fit. If the two scores are
  close, the embeddings carry no signal for this landscape and the headline
  number is an artifact of class imbalance.
- **Feature vintage is the caller's choice and is recorded.** Sampling a year
  after the raster's own vintage lets the model see change the raster could not
  have known about; that is sometimes what you want and always something a reader
  of the manifest should be told.
- **Nothing is corrected silently.** The manifest records every from→to class
  transition with its count, and the diagnostics raster carries the original
  value and the per-pixel confidence beside the correction.

Outputs
-------
``<slug>_corrected.tif``    One band, the source dtype and nodata: a drop-in
                            replacement for the clipped input raster.
``<slug>_diagnostics.tif``  Three float32 bands — original class, confidence,
                            changed flag — because a GeoTIFF cannot mix dtypes
                            across bands and a categorical raster should not be
                            float just to carry a probability.
``<slug>_manifest.json``    Parameters, sample counts, CV report, transitions.

Feature vectors arrive through the :class:`~pipeline.s5_imagery.feature_sources.FeatureSource`
protocol, so nothing here imports Earth Engine and all of it is testable offline.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio import features as rio_features
from rasterio import windows as rio_windows
from rasterio.warp import transform as warp_transform
from shapely.geometry.base import BaseGeometry
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from pipeline.raster_windows import round_outward
from pipeline.s5_imagery import vectors
from pipeline.s5_imagery.feature_sources import FeatureSource
from pipeline.spatial_ref import assert_projected_metres

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA = "artemis.raster_correction.manifest/1"

# Context ring pulled in around the polygon. The outside is where most of the
# training evidence comes from, so it has to be wide enough to contain the
# classes the polygon might really be — 500 m is ~17 pixels of a 30 m raster.
DEFAULT_PAD_M = 500.0

# Read guard. 4e6 pixels is a 60 km square at 30 m: past this the point-sampling
# feature sources stop being the right tool and an exported raster is.
DEFAULT_MAX_WINDOW_PIXELS = 4_000_000

# Per-class training cap. Beyond a few hundred samples a class contributes
# little but round-trip time, and the caps also stop one dominant class from
# swamping the multinomial fit.
DEFAULT_MAX_PER_CLASS = 400

# A class with fewer samples than this cannot be learned or cross-validated;
# it is dropped from the label vocabulary and reported rather than fitted.
DEFAULT_MIN_PER_CLASS = 30

# Every apply pixel costs a point in a feature-source request. This guard turns
# "notebook hangs for an hour" into an error naming the two ways out.
DEFAULT_MAX_APPLY_PIXELS = 20_000

# Spatial CV block edge. Ten 30 m pixels: large enough to break the local
# autocorrelation a random split would ride on, small enough that a stand-sized
# AOI still yields enough blocks to fold.
DEFAULT_BLOCK_M = 300.0

DEFAULT_N_SPLITS = 5

# Below this the prediction is not trusted enough to overwrite a real label.
DEFAULT_MIN_CONFIDENCE = 0.60

DIAGNOSTIC_BANDS = ("original_class", "confidence", "changed")


# ──────────────────────────────────────────────────────────────────────────────
# Raster window
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RasterWindow:
    """A padded read of the classification raster around the AOI.

    Carries everything needed to interpret the array and to write a co-registered
    output: no downstream step reopens the source raster.
    """

    values: np.ndarray
    transform: Any
    crs: Any
    nodata: float | None
    source: str
    window: Any

    @property
    def shape(self) -> tuple[int, int]:
        return self.values.shape  # type: ignore[return-value]

    @property
    def pixel_area_m2(self) -> float:
        return abs(float(self.transform.a) * float(self.transform.e))

    def profile(self, count: int, dtype: str, nodata: float | None) -> dict[str, Any]:
        """A rasterio profile for an output co-registered with this window."""
        height, width = self.shape
        return {
            "driver": "GTiff",
            "height": height,
            "width": width,
            "count": count,
            "dtype": dtype,
            "crs": self.crs,
            "transform": self.transform,
            "nodata": nodata,
            "compress": "deflate",
            "tiled": True,
        }


def read_window(
    raster_path: str | Path,
    aoi_wgs84: BaseGeometry,
    pad_m: float = DEFAULT_PAD_M,
    band: int = 1,
    max_pixels: int = DEFAULT_MAX_WINDOW_PIXELS,
) -> RasterWindow:
    """
    Read the classification raster over the AOI plus a padding ring.

    The pad is what supplies outside-polygon training evidence, so a pad of zero
    is refused: with no context ring the only trusted labels would be the
    undisputed pixels inside the polygon, which for a homogeneous AOI can be none
    at all.

    Raises ValueError when the AOI misses the raster entirely, or when the padded
    window exceeds ``max_pixels``.
    """
    if pad_m <= 0:
        raise ValueError(
            "pad_m must be > 0: the ring outside the polygon is where most of the "
            "trusted training labels come from."
        )

    raster_path = Path(raster_path)
    with rasterio.open(raster_path) as dataset:
        assert_projected_metres(dataset.crs, f"classification raster {raster_path.name}")
        aoi_local = gpd.GeoSeries([aoi_wgs84], crs=vectors.WGS84).to_crs(dataset.crs).iloc[0]

        minx, miny, maxx, maxy = aoi_local.bounds
        requested = rio_windows.from_bounds(
            minx - pad_m, miny - pad_m, maxx + pad_m, maxy + pad_m, dataset.transform
        )
        requested = round_outward(requested)
        full = rio_windows.Window(0, 0, dataset.width, dataset.height)
        try:
            window = rio_windows.intersection(requested, full)
        except rio_windows.WindowError as err:
            raise ValueError(
                f"The AOI does not overlap {raster_path.name}. Check that both refer to "
                "the same place — a CRS mix-up puts an AOI in the wrong hemisphere "
                "without any error."
            ) from err

        pixels = int(window.height) * int(window.width)
        if pixels > max_pixels:
            raise ValueError(
                f"The padded AOI covers {pixels:,} pixels, over the {max_pixels:,} guard. "
                "Shrink the AOI or pad_m, or raise max_pixels if you have the patience "
                "and the feature budget for it."
            )

        values = dataset.read(band, window=window)
        return RasterWindow(
            values=values,
            transform=dataset.window_transform(window),
            crs=dataset.crs,
            nodata=dataset.nodata,
            source=str(raster_path),
            window=window,
        )


def pixel_table(
    window: RasterWindow, aoi_wgs84: BaseGeometry, invalid_values: Sequence[int] = ()
) -> pd.DataFrame:
    """
    One row per pixel of the window: position, projected centre, class, inside flag.

    ``inside`` is a rasterization of the AOI at the window's own grid, so a pixel
    counts as inside exactly when its centre falls in the polygon — the same rule
    that decides which pixels the correction may touch.

    Nodata pixels are dropped: they are neither evidence nor correctable.

    ``invalid_values`` drops further class values that are not real classes. This
    is not redundant with nodata — a raster can carry its own in-band fill that the
    GeoTIFF header knows nothing about. LANDFIRE EVT is exactly that case: the
    header declares nodata 32767, while ocean and out-of-CONUS ground are coded
    -9999 *inside* the valid range. Left in, that fill becomes a trusted class, and
    a coastal AOI trains a classifier on what the ocean looks like.
    """
    aoi_local = gpd.GeoSeries([aoi_wgs84], crs=vectors.WGS84).to_crs(window.crs).iloc[0]
    height, width = window.shape

    inside = rio_features.rasterize(
        [(aoi_local, 1)],
        out_shape=(height, width),
        transform=window.transform,
        fill=0,
        dtype="uint8",
    ).astype(bool)

    rows, cols = np.indices((height, width))
    affine = window.transform
    # Centre coordinates via the general affine, vectorized. rasterio.transform.xy
    # would loop in Python over every pixel of the window.
    x = affine.c + (cols + 0.5) * affine.a + (rows + 0.5) * affine.b
    y = affine.f + (cols + 0.5) * affine.d + (rows + 0.5) * affine.e

    table = pd.DataFrame(
        {
            "row": rows.ravel(),
            "col": cols.ravel(),
            "x": x.ravel(),
            "y": y.ravel(),
            "class_value": window.values.ravel(),
            "inside": inside.ravel(),
        }
    )

    if window.nodata is not None:
        # NaN is a legal nodata sentinel for float rasters, and `NaN != NaN`,
        # so an equality test would keep every nodata pixel.
        if math.isnan(window.nodata):
            table = table[~table["class_value"].isna()]
        else:
            table = table[table["class_value"] != window.nodata]
    if invalid_values:
        table = table[~table["class_value"].isin(list(invalid_values))]
    return table.reset_index(drop=True)


def attach_lonlat(table: pd.DataFrame, crs: Any) -> pd.DataFrame:
    """Add WGS84 ``lon``/``lat`` columns for the rows a feature source will be asked about.

    Applied to the sampled subsets rather than to the whole window, because
    reprojecting a million pixel centres to answer for a few thousand is waste.
    """
    if table.empty:
        return table.assign(lon=pd.Series(dtype=float), lat=pd.Series(dtype=float))
    lon, lat = warp_transform(crs, vectors.WGS84, table["x"].to_numpy(), table["y"].to_numpy())
    return table.assign(lon=np.asarray(lon), lat=np.asarray(lat))


# ──────────────────────────────────────────────────────────────────────────────
# Sampling
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SampleSplit:
    """Trusted training pixels and the eligible pixels awaiting a verdict."""

    training: pd.DataFrame
    apply: pd.DataFrame
    class_counts: dict[int, int]
    dropped_classes: dict[int, int]
    absent_eligible: list[int]

    @property
    def label_values(self) -> list[int]:
        return sorted(self.class_counts)


def split_samples(
    table: pd.DataFrame,
    eligible_classes: Iterable[int],
    max_per_class: int = DEFAULT_MAX_PER_CLASS,
    min_per_class: int = DEFAULT_MIN_PER_CLASS,
    max_apply_pixels: int = DEFAULT_MAX_APPLY_PIXELS,
    seed: int = 0,
) -> SampleSplit:
    """
    Split the window into trusted training labels and the apply set.

    Training: every pixel whose class is not eligible, inside the polygon and out,
    capped per class and with too-rare classes dropped from the vocabulary
    entirely — a class with a handful of pixels cannot be learned, and leaving it
    in makes the cross-validation folds unstable.

    Apply: eligible pixels inside the polygon. Eligible pixels *outside* are left
    alone; the polygon is the claim about where the classification is wrong.
    """
    eligible = sorted({int(value) for value in eligible_classes})
    if not eligible:
        raise ValueError(
            "No eligible classes given. The correction needs to know which class "
            "values inside the polygon are allowed to be overwritten."
        )
    if min_per_class < 2:
        raise ValueError("min_per_class must be >= 2 for cross-validation to be possible")
    if max_per_class < min_per_class:
        raise ValueError("max_per_class must be >= min_per_class")

    is_eligible = table["class_value"].isin(eligible)
    present = set(table["class_value"].astype(int))
    absent_eligible = [value for value in eligible if value not in present]

    apply_set = table[is_eligible & table["inside"]].reset_index(drop=True)
    if apply_set.empty:
        raise ValueError(
            f"No pixels inside the polygon carry any of the eligible classes {eligible}. "
            "Either the polygon is in the wrong place or the class list is wrong; "
            f"classes actually present inside: {sorted(set(table.loc[table['inside'], 'class_value'].astype(int)))[:12]}"
        )
    if len(apply_set) > max_apply_pixels:
        raise ValueError(
            f"{len(apply_set):,} eligible pixels inside the polygon, over the "
            f"{max_apply_pixels:,} guard. Every one of them costs a point in a feature "
            "request. Shrink the polygon, narrow the eligible class list, or raise "
            "max_apply_pixels deliberately."
        )

    trusted = table[~is_eligible].copy()
    counts = trusted["class_value"].astype(int).value_counts().to_dict()
    dropped = {value: count for value, count in counts.items() if count < min_per_class}
    kept = {value: count for value, count in counts.items() if count >= min_per_class}

    if len(kept) < 2:
        raise ValueError(
            "Fewer than two trusted classes have at least "
            f"{min_per_class} pixels ({kept or 'none'}). Widen pad_m to pull in more "
            "context, or lower min_per_class if the landscape really is that uniform."
        )

    rng = np.random.default_rng(seed)
    trusted = trusted[trusted["class_value"].astype(int).isin(kept)]
    capped = []
    for value in sorted(kept):
        group = trusted[trusted["class_value"].astype(int) == value]
        if len(group) > max_per_class:
            picked = np.sort(rng.choice(len(group), size=max_per_class, replace=False))
            group = group.iloc[picked]
        capped.append(group)
    training = pd.concat(capped).reset_index(drop=True)

    if absent_eligible:
        logger.warning(
            "Eligible classes not present anywhere in the window: %s", absent_eligible
        )
    if dropped:
        logger.warning(
            "Classes dropped for having fewer than %d pixels: %s", min_per_class, dropped
        )

    return SampleSplit(
        training=training,
        apply=apply_set,
        class_counts={int(k): int(v) for k, v in sorted(kept.items())},
        dropped_classes={int(k): int(v) for k, v in sorted(dropped.items())},
        absent_eligible=absent_eligible,
    )


def block_groups(x: np.ndarray, y: np.ndarray, block_m: float = DEFAULT_BLOCK_M) -> np.ndarray:
    """
    Square spatial blocks as cross-validation group ids.

    Coordinates are in the raster's projected CRS, so ``block_m`` is metres on the
    ground rather than a pixel count that changes meaning with resolution.
    """
    if block_m <= 0:
        raise ValueError("block_m must be > 0")
    bx = np.floor(np.asarray(x, dtype=float) / block_m).astype(np.int64)
    by = np.floor(np.asarray(y, dtype=float) / block_m).astype(np.int64)
    _, groups = np.unique(np.stack([bx, by], axis=1), axis=0, return_inverse=True)
    return groups.astype(np.int64)


def attach_features(
    table: pd.DataFrame, source: FeatureSource
) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Ask a feature source about every row, returning the rows it could answer for.

    Rows with any missing feature are dropped here rather than imputed: a partially
    observed embedding is not a weaker observation of the same thing, it is a
    different vector. The returned frame keeps the original index so callers can
    tell which rows fell out.
    """
    if "lon" not in table.columns or "lat" not in table.columns:
        raise ValueError("attach_features needs lon/lat columns — call attach_lonlat first")

    matrix = source.features_at(list(zip(table["lon"], table["lat"], strict=True)))
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape[0] != len(table):
        raise ValueError(
            f"Feature source returned {matrix.shape[0]} rows for {len(table)} points; "
            "a FeatureSource must be row-aligned to its input."
        )

    complete = ~np.isnan(matrix).any(axis=1)
    return table.loc[complete].copy(), matrix[complete]


# ──────────────────────────────────────────────────────────────────────────────
# Model
# ──────────────────────────────────────────────────────────────────────────────


def build_model(seed: int = 0) -> Pipeline:
    """
    The baseline classifier: standardized features into multinomial logistic regression.

    Linear and interpretable on purpose. The question the notebook asks first is
    whether the embeddings separate these classes at all; a gradient-boosted answer
    to that question is harder to disbelieve when it is wrong.
    """
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=2000, random_state=seed),
    )


@dataclass(frozen=True)
class CrossValidationReport:
    """Spatially blocked CV scores beside a label-shuffle baseline."""

    n_samples: int
    n_classes: int
    n_blocks: int
    n_folds: int
    accuracy: float | None = None
    macro_f1: float | None = None
    shuffled_accuracy: float | None = None
    shuffled_macro_f1: float | None = None
    per_class: dict[int, dict[str, float]] = field(default_factory=dict)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_samples": self.n_samples,
            "n_classes": self.n_classes,
            "n_blocks": self.n_blocks,
            "n_folds": self.n_folds,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "shuffled_accuracy": self.shuffled_accuracy,
            "shuffled_macro_f1": self.shuffled_macro_f1,
            "per_class": self.per_class,
            "skipped_reason": self.skipped_reason,
        }


def spatial_cross_validate(
    features: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    n_splits: int = DEFAULT_N_SPLITS,
    seed: int = 0,
) -> CrossValidationReport:
    """
    Score the model on spatially held-out blocks, against a shuffled-label baseline.

    Returns a report with ``skipped_reason`` set rather than raising when the
    sample cannot support blocked CV — too few blocks is a normal outcome for a
    small AOI, and it should not stop the correction from being produced. It
    should, however, be impossible to miss in the manifest.
    """
    labels = np.asarray(labels)
    groups = np.asarray(groups)
    n_blocks = int(len(np.unique(groups)))
    n_classes = int(len(np.unique(labels)))
    base = {
        "n_samples": int(len(labels)),
        "n_classes": n_classes,
        "n_blocks": n_blocks,
    }

    folds = min(n_splits, n_blocks)
    if folds < 2:
        return CrossValidationReport(
            **base,
            n_folds=0,
            skipped_reason=(
                f"only {n_blocks} spatial block(s) in the training sample — blocked CV "
                "needs at least 2. Widen pad_m or shrink block_m."
            ),
        )

    cv = GroupKFold(n_splits=folds)
    splits = list(cv.split(features, labels, groups=groups))

    rng = np.random.default_rng(seed)
    shuffled_labels = labels[rng.permutation(len(labels))]

    # A class concentrated in one spatial block vanishes from the training side
    # of the fold that holds the block out; the solver then refuses to fit and
    # the whole correction dies mid-`cross_val_predict`. That layout is a fact
    # about the landscape, so it is reported as a skip the manifest shows.
    starved = [
        fold
        for fold, (train_index, _) in enumerate(splits)
        if len(np.unique(labels[train_index])) < 2
        or len(np.unique(shuffled_labels[train_index])) < 2
    ]
    if starved:
        return CrossValidationReport(
            **base,
            n_folds=folds,
            skipped_reason=(
                f"fold(s) {starved} would train on fewer than 2 classes — a class "
                "sits entirely inside the block(s) those folds hold out. Widen "
                "pad_m to spread classes across more blocks, or shrink block_m."
            ),
        )

    predicted = cross_val_predict(build_model(seed), features, labels, cv=splits)
    shuffled = cross_val_predict(build_model(seed), features, shuffled_labels, cv=splits)

    per_class: dict[int, dict[str, float]] = {}
    class_f1 = f1_score(labels, predicted, average=None, labels=np.unique(labels), zero_division=0)
    for value, score in zip(np.unique(labels), class_f1, strict=True):
        per_class[int(value)] = {
            "support": int((labels == value).sum()),
            "f1": float(score),
        }

    return CrossValidationReport(
        **base,
        n_folds=folds,
        accuracy=float(accuracy_score(labels, predicted)),
        macro_f1=float(f1_score(labels, predicted, average="macro", zero_division=0)),
        shuffled_accuracy=float(accuracy_score(shuffled_labels, shuffled)),
        shuffled_macro_f1=float(
            f1_score(shuffled_labels, shuffled, average="macro", zero_division=0)
        ),
        per_class=per_class,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Correction
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CorrectionOutcome:
    """Per-pixel verdicts plus the tallies that go into the manifest."""

    applied: pd.DataFrame
    n_eligible: int
    n_featureless: int
    n_low_confidence: int
    n_changed: int
    transitions: dict[str, int]


def predict_corrections(
    model: Pipeline,
    apply_table: pd.DataFrame,
    apply_features: np.ndarray,
    n_eligible: int,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> CorrectionOutcome:
    """
    Predict a class for every apply pixel and decide which predictions get used.

    A prediction replaces the original value only when it differs from it *and*
    clears ``min_confidence``. Everything else keeps the label it came with:
    leaving a suspect pixel suspect is recoverable, overwriting it with a
    confident-looking guess is not.

    ``n_eligible`` is the count before feature-less pixels were dropped, so the
    outcome can report how many pixels were never scored at all.
    """
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be in [0, 1]")

    result = apply_table.copy()
    if result.empty:
        return CorrectionOutcome(
            applied=result.assign(
                predicted_class=pd.Series(dtype=int),
                confidence=pd.Series(dtype=float),
                corrected_class=pd.Series(dtype=int),
                changed=pd.Series(dtype=bool),
            ),
            n_eligible=n_eligible,
            n_featureless=n_eligible,
            n_low_confidence=0,
            n_changed=0,
            transitions={},
        )

    probabilities = model.predict_proba(apply_features)
    classes = np.asarray(model.classes_)
    best = probabilities.argmax(axis=1)

    result["predicted_class"] = classes[best].astype(int)
    result["confidence"] = probabilities.max(axis=1)

    confident = result["confidence"] >= min_confidence
    differs = result["predicted_class"] != result["class_value"].astype(int)
    result["changed"] = confident & differs
    result["corrected_class"] = np.where(
        result["changed"], result["predicted_class"], result["class_value"].astype(int)
    ).astype(int)

    transitions = (
        result[result["changed"]]
        .groupby(["class_value", "predicted_class"], sort=True)
        .size()
        .to_dict()
    )

    return CorrectionOutcome(
        applied=result,
        n_eligible=n_eligible,
        n_featureless=n_eligible - len(result),
        n_low_confidence=int((~confident).sum()),
        n_changed=int(result["changed"].sum()),
        transitions={f"{int(src)}->{int(dst)}": int(count) for (src, dst), count in transitions.items()},
    )


def apply_to_window(window: RasterWindow, outcome: CorrectionOutcome) -> np.ndarray:
    """The corrected array: the window's values with confident corrections written in."""
    corrected = window.values.copy()
    changed = outcome.applied[outcome.applied["changed"]]
    if not changed.empty:
        corrected[changed["row"].to_numpy(), changed["col"].to_numpy()] = changed[
            "corrected_class"
        ].to_numpy()
    return corrected


def diagnostics_stack(window: RasterWindow, outcome: CorrectionOutcome) -> np.ndarray:
    """Three float32 bands: original class, per-pixel confidence, changed flag.

    Confidence is NaN wherever no prediction was made — outside the apply set, or
    for an apply pixel the feature source could not answer for. Zero would be a
    lie: it reads as "scored, and hopeless".
    """
    height, width = window.shape
    original = window.values.astype(np.float32)
    confidence = np.full((height, width), np.nan, dtype=np.float32)
    changed = np.zeros((height, width), dtype=np.float32)

    scored = outcome.applied
    if not scored.empty:
        rows = scored["row"].to_numpy()
        cols = scored["col"].to_numpy()
        confidence[rows, cols] = scored["confidence"].to_numpy(dtype=np.float32)
        changed[rows, cols] = scored["changed"].to_numpy().astype(np.float32)

    return np.stack([original, confidence, changed])


def write_corrected(path: Path, window: RasterWindow, corrected: np.ndarray) -> Path:
    """Write the corrected classification: one band, source dtype, source nodata."""
    profile = window.profile(count=1, dtype=str(window.values.dtype), nodata=window.nodata)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(corrected.astype(window.values.dtype), 1)
        dataset.set_band_description(1, "corrected_class")
    return path


def write_diagnostics(path: Path, window: RasterWindow, stack: np.ndarray) -> Path:
    """Write the three-band float32 diagnostics raster beside the correction."""
    profile = window.profile(count=len(DIAGNOSTIC_BANDS), dtype="float32", nodata=float("nan"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(stack.astype("float32"))
        for index, name in enumerate(DIAGNOSTIC_BANDS, start=1):
            dataset.set_band_description(index, name)
    return path


def transition_table(
    outcome: CorrectionOutcome,
    pixel_area_m2: float,
    class_names: dict[int, str] | None = None,
) -> pd.DataFrame:
    """From→to transitions with pixel counts and hectares, for reading in a notebook."""
    names = class_names or {}
    rows = []
    for key, count in sorted(outcome.transitions.items(), key=lambda item: -item[1]):
        source, target = (int(part) for part in key.split("->"))
        rows.append(
            {
                "from_value": source,
                "from_name": names.get(source, ""),
                "to_value": target,
                "to_name": names.get(target, ""),
                "pixels": count,
                "hectares": round(count * pixel_area_m2 / 10_000.0, 3),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["from_value", "from_name", "to_value", "to_name", "pixels", "hectares"],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CorrectionRequest:
    """Everything :func:`correct_raster` needs that is not the feature source."""

    aoi: BaseGeometry
    raster_path: str | Path
    eligible_classes: Sequence[int]
    out_dir: str | Path
    slug: str = "correction"
    aoi_source: str = "(in-memory geometry)"
    invalid_values: Sequence[int] = ()
    pad_m: float = DEFAULT_PAD_M
    band: int = 1
    max_window_pixels: int = DEFAULT_MAX_WINDOW_PIXELS
    max_per_class: int = DEFAULT_MAX_PER_CLASS
    min_per_class: int = DEFAULT_MIN_PER_CLASS
    max_apply_pixels: int = DEFAULT_MAX_APPLY_PIXELS
    block_m: float = DEFAULT_BLOCK_M
    n_splits: int = DEFAULT_N_SPLITS
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    seed: int = 0
    class_names: dict[int, str] | None = None


@dataclass(frozen=True)
class CorrectionRun:
    """The whole run: intermediates for inspection, outputs on disk."""

    window: RasterWindow
    split: SampleSplit
    cv_report: CrossValidationReport
    outcome: CorrectionOutcome
    model: Pipeline
    manifest: dict[str, Any]
    corrected_path: Path
    diagnostics_path: Path
    manifest_path: Path


def correct_raster(request: CorrectionRequest, source: FeatureSource) -> CorrectionRun:
    """
    Run the whole correction: read, sample, fit, score, predict, write.

    The notebook drives these steps individually so each intermediate can be
    looked at; this is the same sequence in one call, for tests and for reruns
    where nothing needs inspecting.
    """
    window = read_window(
        request.raster_path,
        request.aoi,
        pad_m=request.pad_m,
        band=request.band,
        max_pixels=request.max_window_pixels,
    )
    table = pixel_table(window, request.aoi, request.invalid_values)
    split = split_samples(
        table,
        request.eligible_classes,
        max_per_class=request.max_per_class,
        min_per_class=request.min_per_class,
        max_apply_pixels=request.max_apply_pixels,
        seed=request.seed,
    )

    training, training_features = attach_features(
        attach_lonlat(split.training, window.crs), source
    )
    if len(training) < request.min_per_class * 2:
        raise ValueError(
            f"Only {len(training)} training pixels survived feature attachment "
            f"(from {len(split.training)}). The feature source has little coverage here."
        )

    labels = training["class_value"].astype(int).to_numpy()
    groups = block_groups(training["x"].to_numpy(), training["y"].to_numpy(), request.block_m)
    cv_report = spatial_cross_validate(
        training_features, labels, groups, n_splits=request.n_splits, seed=request.seed
    )

    model = build_model(request.seed)
    model.fit(training_features, labels)

    apply_rows, apply_features = attach_features(
        attach_lonlat(split.apply, window.crs), source
    )
    outcome = predict_corrections(
        model,
        apply_rows,
        apply_features,
        n_eligible=len(split.apply),
        min_confidence=request.min_confidence,
    )

    out_dir = Path(request.out_dir)
    corrected_path = write_corrected(
        out_dir / f"{request.slug}_corrected.tif", window, apply_to_window(window, outcome)
    )
    diagnostics_path = write_diagnostics(
        out_dir / f"{request.slug}_diagnostics.tif", window, diagnostics_stack(window, outcome)
    )

    manifest = build_manifest(request, source, window, split, cv_report, outcome, len(training))
    manifest["outputs"] = {
        "corrected": corrected_path.name,
        "diagnostics": diagnostics_path.name,
    }
    manifest_path = out_dir / f"{request.slug}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    return CorrectionRun(
        window=window,
        split=split,
        cv_report=cv_report,
        outcome=outcome,
        model=model,
        manifest=manifest,
        corrected_path=corrected_path,
        diagnostics_path=diagnostics_path,
        manifest_path=manifest_path,
    )


def build_manifest(
    request: CorrectionRequest,
    source: FeatureSource,
    window: RasterWindow,
    split: SampleSplit,
    cv_report: CrossValidationReport,
    outcome: CorrectionOutcome,
    n_training_used: int,
) -> dict[str, Any]:
    """Assemble the run record. Pure: every value is already resolved."""
    height, width = window.shape
    pixel_ha = window.pixel_area_m2 / 10_000.0
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "slug": request.slug,
        "inputs": {
            "raster": str(request.raster_path),
            "band": request.band,
            "aoi": request.aoi_source,
            "aoi_area_ha": round(vectors.area_ha(request.aoi), 3),
            "features": source.description,
        },
        "window": {
            "crs": str(window.crs),
            "shape": [height, width],
            "pad_m": request.pad_m,
            "pixel_area_m2": window.pixel_area_m2,
            "nodata": None if window.nodata is None else float(window.nodata),
        },
        "parameters": {
            "eligible_classes": [int(value) for value in request.eligible_classes],
            "invalid_values": [int(value) for value in request.invalid_values],
            "max_per_class": request.max_per_class,
            "min_per_class": request.min_per_class,
            "block_m": request.block_m,
            "n_splits": request.n_splits,
            "min_confidence": request.min_confidence,
            "seed": request.seed,
        },
        "samples": {
            "training_pixels": len(split.training),
            "training_pixels_with_features": n_training_used,
            "class_counts": split.class_counts,
            "dropped_classes": split.dropped_classes,
            "eligible_classes_absent": split.absent_eligible,
            "eligible_pixels_inside": outcome.n_eligible,
            "eligible_pixels_without_features": outcome.n_featureless,
        },
        "cross_validation": cv_report.as_dict(),
        "corrections": {
            "changed_pixels": outcome.n_changed,
            "changed_hectares": round(outcome.n_changed * pixel_ha, 3),
            "below_confidence": outcome.n_low_confidence,
            "transitions": outcome.transitions,
        },
    }
