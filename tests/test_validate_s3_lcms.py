"""Regression tests for the LCMS S3 validation sampling frame."""

import numpy as np

from pipeline.s1_initial_state.validate_s3_lcms import eligible_patch_interiors, sample_points


def test_eligible_patch_interiors_excludes_small_patches_and_boundaries():
    mask = np.zeros((8, 15), dtype=bool)
    mask[1:6, 1:6] = True  # 25 pixels = 5.56 acres, leaving a 3x3 interior
    mask[1:3, 10:12] = True  # too small for the 5-acre sampling minimum

    eligible = eligible_patch_interiors(mask)

    expected = np.zeros_like(mask)
    expected[2:5, 2:5] = True
    np.testing.assert_array_equal(eligible, expected)


def test_sample_points_returns_empty_when_no_patch_clears_the_mmu():
    """The precondition main() now skips on: an empty frame has no lon/lat to
    hand Earth Engine, so sampling it would fail on a missing-column error."""
    import rasterio

    mask = np.zeros((8, 15), dtype=bool)
    mask[1:3, 10:12] = True  # 4 px = 0.89 acres, below the 5-acre minimum
    transform = rasterio.transform.from_origin(0, 0, 30, 30)

    points = sample_points(mask, 10, transform, "EPSG:5070", np.random.default_rng(0))

    assert points.empty
