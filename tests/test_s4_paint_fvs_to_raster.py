"""Regression tests for the FVS-to-raster value mapping.

Covers ``reclassify_by_key``, the core that swaps each TreeMap TM_ID pixel for
its stand's FVS value. The risk it guards against: an off-by-one / mismatched
``searchsorted`` lookup silently assigning the wrong stand's value to a pixel.
"""

import importlib.util
from pathlib import Path

import numpy as np

_MOD_PATH = Path(__file__).resolve().parents[1] / "pipeline/s4_fvs/paint_fvs_to_raster.py"
_spec = importlib.util.spec_from_file_location("paint_fvs_to_raster", _MOD_PATH)
paint_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paint_mod)
reclassify_by_key = paint_mod.reclassify_by_key

NODATA = -9999.0


def test_maps_keys_to_values_and_nodata_for_unmatched():
    band = np.array([[10, 20], [30, 99]], dtype="int64")  # 99 is not a key
    keys = np.array([10, 20, 30], dtype="int64")
    vals = np.array([1.5, 2.5, 3.5], dtype="float32")
    out = reclassify_by_key(band, keys, vals, NODATA)
    expected = np.array([[1.5, 2.5], [3.5, NODATA]], dtype="float32")
    np.testing.assert_array_equal(out, expected)


def test_no_value_leaks_to_keys_below_or_above_range():
    # Pixels outside the key range must be nodata, not clamped to an end value.
    band = np.array([[5, 100]], dtype="int64")
    keys = np.array([10, 20, 30], dtype="int64")
    vals = np.array([1.0, 2.0, 3.0], dtype="float32")
    out = reclassify_by_key(band, keys, vals, NODATA)
    np.testing.assert_array_equal(out, np.array([[NODATA, NODATA]], dtype="float32"))


def test_empty_keys_returns_all_nodata():
    band = np.array([[1, 2, 3]], dtype="int64")
    out = reclassify_by_key(band, np.array([], dtype="int64"),
                            np.array([], dtype="float32"), NODATA)
    assert np.all(out == NODATA)


def test_many_pixels_share_one_key():
    # Many TM_ID pixels imputed to the same plot all get the same value.
    band = np.full((100, 100), 42, dtype="int64")
    band[0, 0] = 7
    keys = np.array([7, 42], dtype="int64")
    vals = np.array([9.0, 100.0], dtype="float32")
    out = reclassify_by_key(band, keys, vals, NODATA)
    assert out[0, 0] == 9.0
    assert (out == 100.0).sum() == 100 * 100 - 1
