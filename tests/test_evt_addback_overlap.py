"""The add-back x LANDFIRE EVT 2022 overlap tables (research/evt_addback_overlap)."""

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from research.evt_addback_overlap.overlap import (
    OUTSIDE,
    by_evt_class,
    by_evt_phys,
    read_on_grid,
)

LEGEND = pd.DataFrame({
    "VALUE": [9823, 7997, 7349, 7299],
    "EVT_NAME": ["Southeastern Ruderal Grassland", "Eastern Warm Temperate Pasture and Hayland",
                 "East Gulf Coastal Plain Interior Upland Longleaf Pine Woodland", "Developed-Roads"],
    "EVT_PHYS": ["Exotic Herbaceous", "Agricultural", "Conifer", "Developed-Roads"],
})

# Provenance: 0 water, 1 published, 2 added back, 3 unmapped land, 4 added back young.
EVT = np.array([[9823, 9823, 9823, 7997],
                [7997, 7997, 7349, 7299],
                [9823, 7299, 7349, 9823]])
PROV = np.array([[2, 4, 3, 2],
                 [3, 3, 1, 3],
                 [OUTSIDE, 3, 1, 3]])
BITS = np.array([[1, 4, 2, 6],
                 [0, 4, 0, 4],
                 [OUTSIDE, 0, 0, 0]])


def test_each_class_splits_into_published_added_back_and_left_out():
    table = by_evt_class(EVT, PROV, BITS, LEGEND, acres_per_pixel=1.0).set_index("VALUE")

    ruderal = table.loc[9823]
    assert ruderal.aoi_ac == 4          # the OUTSIDE pixel is not in the five counties
    assert ruderal.published_ac == 0
    assert ruderal.proposed_ac == 3     # the unmapped pixel with bit 2 was proposed, then dropped
    assert ruderal.added_back_ac == 2   # provenance 2 and 4 both count
    assert ruderal.left_out_ac == 2     # holes of this class not added back
    assert ruderal.left_out_share_of_holes == pytest.approx(0.5)

    pasture = table.loc[7997]
    assert (pasture.proposed_ac, pasture.added_back_ac, pasture.left_out_ac) == (2, 1, 2)

    pine = table.loc[7349]
    assert (pine.published_ac, pine.added_back_ac, pine.left_out_ac) == (2, 0, 0)
    assert np.isnan(pine.left_out_share_of_holes)  # no holes, no share


def test_shares_are_of_the_add_back_total_and_rows_sort_by_added_back():
    table = by_evt_class(EVT, PROV, BITS, LEGEND, acres_per_pixel=1.0)

    assert table.added_back_ac.sum() == 3
    assert table.added_back_share.sum() == pytest.approx(1.0)
    assert table.proposed_share.sum() == pytest.approx(1.0)
    assert table.VALUE.tolist()[:2] == [9823, 7997]
    assert table.set_index("VALUE").loc[9823, "EVT_NAME"] == "Southeastern Ruderal Grassland"


def test_unknown_evt_code_is_an_error_not_a_blank_row():
    evt = EVT.copy()
    evt[0, 0] = 1234
    with pytest.raises(ValueError, match="1234"):
        by_evt_class(evt, PROV, BITS, LEGEND, acres_per_pixel=1.0)


def test_a_proposal_on_published_forest_means_mismatched_rasters():
    bits = BITS.copy()
    bits[1, 2] = 1  # provenance 1: published TreeMap is never a hole
    with pytest.raises(ValueError, match="not one run"):
        by_evt_class(EVT, PROV, bits, LEGEND, acres_per_pixel=1.0)


def test_physiognomy_rollup_sums_its_classes():
    table = by_evt_class(EVT, PROV, BITS, LEGEND, acres_per_pixel=1.0)
    phys = by_evt_phys(table).set_index("EVT_PHYS")

    assert phys.loc["Developed-Roads", "aoi_ac"] == 2
    assert phys.loc["Developed-Roads", "proposed_ac"] == 1
    assert phys.aoi_ac.sum() == table.aoi_ac.sum()


def _write(path, values, transform, nodata=None):
    with rasterio.open(path, "w", driver="GTiff", height=values.shape[0], width=values.shape[1],
                       count=1, dtype=values.dtype, crs="EPSG:5070", transform=transform,
                       nodata=nodata) as dst:
        dst.write(values, 1)


def test_read_on_grid_takes_the_window_under_the_target_grid(tmp_path):
    source = np.arange(100, dtype=np.int16).reshape(10, 10)
    _write(tmp_path / "evt.tif", source, from_origin(0, 300, 30, 30))
    target = rasterio.open(_target(tmp_path, from_origin(60, 240, 30, 30), (3, 4)))

    out = read_on_grid(tmp_path / "evt.tif", target)

    np.testing.assert_array_equal(out, source[2:5, 2:6])


def test_read_on_grid_refuses_a_half_pixel_shift(tmp_path):
    _write(tmp_path / "evt.tif", np.zeros((10, 10), dtype=np.int16), from_origin(0, 300, 30, 30))
    target = rasterio.open(_target(tmp_path, from_origin(75, 240, 30, 30), (3, 4)))

    with pytest.raises(ValueError, match="not aligned"):
        read_on_grid(tmp_path / "evt.tif", target)


def _target(tmp_path, transform, shape):
    path = tmp_path / "target.tif"
    _write(path, np.zeros(shape, dtype=np.uint8), transform, nodata=OUTSIDE)
    return path
