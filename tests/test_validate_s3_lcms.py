"""Regression tests for the LCMS S3 validation sampling frame."""

import numpy as np

from pipeline.s1_initial_state.validate_s3_lcms import eligible_patch_interiors


def test_eligible_patch_interiors_excludes_small_patches_and_boundaries():
    mask = np.zeros((8, 15), dtype=bool)
    mask[1:6, 1:6] = True  # 25 pixels = 5.56 acres, leaving a 3x3 interior
    mask[1:3, 10:12] = True  # too small for the 5-acre sampling minimum

    eligible = eligible_patch_interiors(mask)

    expected = np.zeros_like(mask)
    expected[2:5, 2:5] = True
    np.testing.assert_array_equal(eligible, expected)
