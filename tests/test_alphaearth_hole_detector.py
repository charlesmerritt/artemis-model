"""The AlphaEarth hole detector: TreeMap forest vs TreeMap holes, its sample and its fitted threshold."""

import json

import numpy as np
import pytest
from rasterio.transform import Affine

from pipeline.s1_initial_state.alphaearth_hole_detector import (
    BLOCK_M,
    HoleDetector,
    SampleClass,
    fit_hole_detector,
    fit_threshold,
    hole_probability,
    sample_pixels,
    treemap_like,
)
from pipeline.s1_initial_state.county_improvement import TreeMapProvenance as TP

T = Affine(30, 0, 1_000_000, 0, -30, 900_000)


def provenance():
    p = np.full((40, 50), TP.UNMAPPED_LAND, dtype=np.uint8)
    p[:20] = TP.PUBLISHED
    p[20:, :5] = TP.WATER
    p[20:, 5:10] = TP.ADDED_BACK_YOUNG
    p[-1, -1] = 255       # outside the AOI
    return p


def test_the_sample_draws_n_published_pixels_and_n_land_holes():
    s = sample_pixels(provenance(), T, 100, seed=1)
    assert s["sample_class"].value_counts().to_dict() == {SampleClass.TREEMAP_FOREST: 100,
                                                          SampleClass.HOLE: 100}
    p = provenance()
    picked = p[s["row"], s["col"]]
    forest = (s["sample_class"] == SampleClass.TREEMAP_FOREST).to_numpy()
    assert (picked[forest] == TP.PUBLISHED).all()
    # A hole is land TreeMap published no plot on, whatever the add-back later did with it.
    assert np.isin(picked[~forest], (TP.UNMAPPED_LAND, TP.ADDED_BACK, TP.ADDED_BACK_YOUNG)).all()


def test_the_sample_is_reproducible_and_without_replacement():
    a, b = sample_pixels(provenance(), T, 100, seed=1), sample_pixels(provenance(), T, 100, seed=1)
    assert a.equals(b)
    assert not a.duplicated(["row", "col"]).any()
    assert not a.equals(sample_pixels(provenance(), T, 100, seed=2))


def test_the_sample_refuses_more_pixels_than_a_class_has():
    with pytest.raises(ValueError, match="treemap_forest"):
        sample_pixels(provenance(), T, 20 * 50 + 1, seed=1)


def test_sample_coordinates_are_pixel_centres_and_blocks_are_10_km_cells():
    s = sample_pixels(provenance(), T, 50, seed=3)
    assert np.allclose(s["x"], T.c + (s["col"] + 0.5) * 30)
    assert np.allclose(s["y"], T.f - (s["row"] + 0.5) * 30)
    assert BLOCK_M == 10_000
    expected = (np.floor(s["x"] / BLOCK_M).astype(int).astype(str) + "_"
                + np.floor(s["y"] / BLOCK_M).astype(int).astype(str))
    assert (s["block"] == expected).all()
    assert s["lon"].between(-180, 180).all() and s["lat"].between(-90, 90).all()


def test_the_threshold_maximizes_balanced_accuracy():
    # A pixel is called a hole at prob >= threshold. 0.6 calls 3/3 holes and 3/4 forest right.
    y = np.array([0, 0, 0, 0, 1, 1, 1])
    prob = np.array([0.1, 0.2, 0.3, 0.7, 0.6, 0.8, 0.9])
    assert fit_threshold(y, prob) == pytest.approx(0.6)


def test_a_tie_takes_the_lower_threshold_so_fewer_holes_pass_as_forest():
    # 0.5 (holes 3/3, forest 2/3) and 0.8 (holes 2/3, forest 3/3) tie at 0.833.
    y = np.array([0, 0, 0, 1, 1, 1])
    prob = np.array([0.1, 0.2, 0.55, 0.5, 0.8, 0.9])
    assert fit_threshold(y, prob) == pytest.approx(0.5)


def test_treemap_like_is_a_hole_score_below_the_threshold():
    assert treemap_like(np.array([0.1, 0.5, 0.9]), 0.5).tolist() == [True, False, False]


def separable(n=400, seed=0):
    """Synthetic unit-norm 64-band embeddings: forest and holes differ along one axis."""
    rng = np.random.default_rng(seed)
    y = np.repeat([0, 1], n // 2)
    x = rng.normal(0, 1, (n, 64))
    x[:, 0] += np.where(y == 1, 3.0, -3.0)
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    groups = np.tile(np.arange(10), n // 10)
    return x, y, groups


def test_the_detector_separates_holes_and_beats_its_shuffled_baseline():
    x, y, groups = separable()
    d = fit_hole_detector(x, y, groups, seed=0)
    assert d.metrics["auc_block_cv"] > 0.95
    assert abs(d.metrics["auc_label_shuffle"] - 0.5) < 0.15
    assert 0.0 < d.threshold < 1.0
    assert d.metrics["n_treemap_forest"] == 200 and d.metrics["n_hole"] == 200


def test_the_numpy_score_is_the_fitted_logistic_and_round_trips_through_json(tmp_path):
    x, y, groups = separable()
    d = fit_hole_detector(x, y, groups, seed=0)
    z = x @ np.asarray(d.coef) + d.intercept
    assert np.allclose(hole_probability(d, x), 1 / (1 + np.exp(-z)))
    path = tmp_path / "model.json"
    d.save(path)
    back = HoleDetector.load(path)
    assert back == d
    assert json.loads(path.read_text())["feature_year"] == 2022
