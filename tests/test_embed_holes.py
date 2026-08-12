"""The exported Stage-A surface must compute the same quantity as the threshold.

``classify_holes`` fits the Stage-A cut-off on a true cosine (``unit_rows`` is
applied to the sampled rows), so ``embed_holes.similarity_image`` — which builds
the raster that cut-off is applied to — has to be a cosine as well. Dotting the
raw AlphaEarth image against a unit-norm exemplar would compute ``|x| * cos``,
which agrees only where the bands are exactly unit-norm. They are not (measured
1.0001 +/- 0.002), so this pins both paths to one definition with a fake
Earth Engine backed by numpy.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.s1_initial_state.classify_holes import max_exemplar_similarity, unit_rows

_MOD_PATH = Path(__file__).resolve().parents[1] / "pipeline/s1_initial_state/embed_holes.py"
_spec = importlib.util.spec_from_file_location("embed_holes", _MOD_PATH)
embed_holes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(embed_holes)


class _Reducer:
    def __init__(self, how):
        self.how = how


class _Image:
    """A stack of per-pixel band values; rows are pixels, columns are bands."""

    def __init__(self, values):
        self.values = np.atleast_2d(np.asarray(values, dtype=float))

    def select(self, _bands):
        return self

    def rename(self, _name):
        return self

    def multiply(self, other):
        return _Image(self.values * other.values)

    def divide(self, other):
        return _Image(self.values / other.values)

    def pow(self, k):
        return _Image(self.values**k)

    def sqrt(self):
        return _Image(np.sqrt(self.values))

    def reduce(self, reducer):
        how = np.sum if reducer.how == "sum" else np.max
        return _Image(how(self.values, axis=1, keepdims=True))


class _ImageFactory:
    @staticmethod
    def constant(vec):
        return _Image(vec)

    @staticmethod
    def cat(images):
        return _Image(np.hstack([im.values for im in images]))


class _FakeEE:
    Image = _ImageFactory
    Reducer = type("R", (), {"sum": staticmethod(lambda: _Reducer("sum")),
                             "max": staticmethod(lambda: _Reducer("max"))})


def test_exported_similarity_matches_the_thresholded_quantity(monkeypatch):
    rng = np.random.default_rng(0)
    bands = [f"A{i:02d}" for i in range(8)]

    # Near-unit-norm rows, like the real AlphaEarth bands (1.0001 +/- 0.002).
    pixels = unit_rows(rng.normal(size=(24, len(bands))))
    pixels *= rng.normal(1.0001, 0.002, size=(len(pixels), 1))
    exemplars = unit_rows(rng.normal(size=(3, len(bands))))

    model = {"feature_year": 2022, "bands": bands, "anchor_exemplars": exemplars.tolist()}
    monkeypatch.setattr(embed_holes, "annual_embedding", lambda ee, year: _Image(pixels))

    exported = embed_holes.similarity_image(_FakeEE, model).values.ravel()
    fitted = max_exemplar_similarity(pd.DataFrame(pixels, columns=bands), bands, exemplars)

    np.testing.assert_allclose(exported, fitted.to_numpy(), atol=1e-12)


def test_raw_dot_product_would_drift_past_the_quantisation_band():
    """Guards the reason for the fix: |x|*cos is not within export precision of cos."""
    rng = np.random.default_rng(1)
    pixels = unit_rows(rng.normal(size=(500, 8)))
    pixels *= rng.normal(1.0001, 0.002, size=(len(pixels), 1))
    exemplar = unit_rows(rng.normal(size=(1, 8)))

    cosine = (unit_rows(pixels) @ exemplar.T).ravel()
    raw = (pixels @ exemplar.T).ravel()

    # SCORE_SCALE bounds export quantisation at 5e-5; the drift is far larger.
    assert np.abs(raw - cosine).max() > 5e-5
