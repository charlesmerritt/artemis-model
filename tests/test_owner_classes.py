"""Ownership is the Harris et al. (2025) RDS-2025-0045 raster value, and nothing else.

Legend from the dataset metadata: 0 unknown, 1 non-forest, 2 water, 3 family,
4 corporate/other private, 5 tribal, 6 federal, 7 state, 8 local.
"""

import pytest

from pipeline.s3_management.owner_classes import classify_owner


def test_corporate_harris_value_is_one_corporate_class():
    assignment = classify_owner({"OWN_CODE": 4})
    assert (assignment.owner_class, assignment.tpo_group) == ("corporate", "Private")


def test_row_whose_label_is_not_the_harris_class_for_its_code_is_rejected():
    # Harris 3 is family; a table that labels code 3 "Federal" is not coded from the raster.
    with pytest.raises(ValueError, match="Harris"):
        classify_owner({"OWN_CODE": 3, "OWN_TYPE": "Federal"})


def test_leto_v1_row_labelled_with_its_harris_class_is_accepted():
    assert classify_owner({"OWN_CODE": 3, "OWN_TYPE": "Family"}).owner_class == "family"


@pytest.mark.parametrize("code", [9, 15, -1])
def test_value_outside_the_harris_legend_is_rejected(code):
    # 15 is the raster's nodata; it is not unknown forest.
    with pytest.raises(ValueError, match="Harris"):
        classify_owner({"OWN_CODE": code})
