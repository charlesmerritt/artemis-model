"""Tests for the management-unit segmentation research helpers."""

from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.mgmt_units.segmentation_delineation import (
    felzenszwalb_segmentation,
    slic_segmentation,
    vectorize_segments,
)


def _synthetic_stack(height=64, width=64):
    """Two-block image plus an all-forest mask, so every segment is a forest segment."""
    evt = np.zeros((height, width))
    evt[: height // 2, :] = 0.2
    evt[height // 2 :, :] = 0.8
    forest_mask = np.ones((height, width), dtype=bool)
    stack = np.stack([evt, forest_mask.astype(float), np.full((height, width), 0.5)], axis=0)
    return stack, forest_mask


def test_felzenszwalb_labels_are_one_based_so_no_forest_segment_is_dropped():
    # skimage's felzenszwalb labels from 0, but 0 is the nodata sentinel that
    # vectorize_segments skips — a 0-labeled forest segment would vanish silently.
    stack, forest_mask = _synthetic_stack()

    segments = felzenszwalb_segmentation(stack, forest_mask, scale=100, sigma=0.5, min_size=50)

    assert segments[forest_mask].min() >= 1


def test_slic_labels_are_one_based():
    stack, forest_mask = _synthetic_stack()

    segments = slic_segmentation(stack, forest_mask, n_segments=10, compactness=10.0, sigma=1.0)

    assert segments[forest_mask].min() >= 1


def test_non_forest_pixels_are_masked_to_the_nodata_sentinel():
    stack, forest_mask = _synthetic_stack()
    forest_mask[:8, :8] = False

    segments = felzenszwalb_segmentation(stack, forest_mask, scale=100, sigma=0.5, min_size=50)

    assert (segments[~forest_mask] == 0).all()


def test_vectorize_segments_keeps_every_forest_pixel():
    import rasterio

    stack, forest_mask = _synthetic_stack()
    forest_mask[:8, :8] = False
    transform = rasterio.transform.from_origin(0, 64, 1, 1)

    segments = felzenszwalb_segmentation(stack, forest_mask, scale=100, sigma=0.5, min_size=50)
    gdf = vectorize_segments(segments, transform, "EPSG:5070")

    assert gdf.geometry.area.sum() == forest_mask.sum()
