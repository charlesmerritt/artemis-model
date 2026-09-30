"""Stand age of add-back pixels from the Landsat cut year, and its age classes (ADR 0003)."""

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import Affine

from pipeline.s1_initial_state.add_back_methods import ConsensusRule
from pipeline.s1_initial_state.add_back_stand_age import (
    UNDATED,
    CutYearDetector as D,
    age_class_labels,
    age_classes,
    cut_year,
    stand_age,
    stand_age_table,
    write_stand_age,
)
from pipeline.s1_initial_state.county_improvement import TreeMapProvenance as TP

PX = 0.2224  # acres per pixel
def test_the_cut_year_is_the_latest_year_either_detector_dates():
    obata = np.array([2014, 0, 2012, 2016])
    lossyear = np.array([0, 15, 18, 16])        # Hansen stores years since 2000
    year, detector = cut_year(obata, lossyear)
    assert year.tolist() == [2014, 2015, 2018, 2016]
    assert detector.tolist() == [D.OBATA, D.HANSEN, D.HANSEN, D.OBATA | D.HANSEN]


def test_a_detection_after_2022_or_before_its_detectors_window_does_not_date_the_pixel():
    obata = np.array([2009, 2023, 0])
    lossyear = np.array([0, 0, 23])
    year, detector = cut_year(obata, lossyear)
    assert year.tolist() == [0, 0, 0]
    assert detector.tolist() == [0, 0, 0]


def test_a_detector_that_has_not_run_dates_nothing():
    year, detector = cut_year(None, np.array([14]))
    assert year.tolist() == [2014]
    assert detector.tolist() == [D.HANSEN]
    with pytest.raises(ValueError, match="no detector"):
        cut_year(None, None)


def test_stand_age_counts_from_the_year_after_the_cut_and_a_2022_cut_is_age_0():
    assert stand_age(np.array([2021, 2022, 2014, 2001, 0])).tolist() == [0, 0, 7, 20, UNDATED]


def test_age_class_labels_run_from_each_start_to_the_next_and_the_last_is_open():
    assert age_class_labels((0, 6, 11, 21, 41)) == ["0-5", "6-10", "11-20", "21-40", "41+"]


@pytest.mark.parametrize("starts", [(1, 6), (0, 6, 6), (0, 11, 6), ()])
def test_age_class_starts_must_begin_at_0_and_rise(starts):
    with pytest.raises(ValueError):
        age_class_labels(starts)


def test_each_age_falls_in_the_class_whose_start_it_has_reached_and_undated_is_its_own():
    ages = np.array([0, 5, 6, 10, 11, 20, 21, 40, 41, 99, UNDATED])
    assert age_classes(ages, (0, 6, 11, 21, 41)).tolist() == [
        "0-5", "0-5", "6-10", "6-10", "11-20", "11-20", "21-40", "21-40", "41+", "41+", "undated"]


def test_the_table_counts_add_back_acres_by_stand_age_for_all_accepted_and_the_two_method_subset():
    provenance = np.array([TP.PUBLISHED, TP.ADDED_BACK_YOUNG, TP.ADDED_BACK_YOUNG,
                           TP.ADDED_BACK, TP.UNMAPPED_LAND, TP.ADDED_BACK_YOUNG])
    bits = np.array([0, 1 | 2, 2, 1, 1 | 2 | 4, 2 | 4])
    years = np.array([2014, 2014, 2020, 0, 2016, 2014])
    t = stand_age_table(years, provenance, bits, (0, 6, 11, 21, 41))

    union = t[t["agreement"] == ConsensusRule.UNION]    # the published and unmapped pixels are not add-back
    assert union["stand_age"].iloc[:2].tolist() == [1, 7]
    assert pd.isna(union["stand_age"].iloc[2])            # undated sorts last
    assert union["age_class"].tolist() == ["0-5", "6-10", "undated"]
    assert union["pixels"].tolist() == [1, 2, 1]
    assert union["acres"].tolist() == pytest.approx([PX, 2 * PX, PX])

    two = t[t["agreement"] == ConsensusRule.AT_LEAST_TWO]
    assert two["stand_age"].tolist() == [7]
    assert two["pixels"].tolist() == [2]


def _write(path, bands, transform, nodata):
    bands = np.asarray(bands)
    bands = bands[None] if bands.ndim == 2 else bands
    with rasterio.open(path, "w", driver="GTiff", height=bands.shape[1], width=bands.shape[2],
                       count=bands.shape[0], dtype=bands.dtype, crs="EPSG:5070",
                       transform=transform, nodata=nodata) as dst:
        dst.write(bands)


def test_write_stand_age_reads_a_run_and_writes_the_cut_year_raster_and_the_table(tmp_path):
    t = Affine(30, 0, 0, 0, -30, 60)
    run = tmp_path / "aoi_5county"
    run.mkdir()
    _write(run / "treemap2022_provenance.tif",
           np.array([[TP.PUBLISHED, TP.ADDED_BACK_YOUNG, TP.ADDED_BACK]], dtype=np.uint8), t, 255)
    _write(run / "add_back_method_bits.tif", np.array([[0, 3, 1]], dtype=np.uint8), t, 255)
    # Obata one pixel wider than the run, shifted a whole pixel west: read onto the run's grid.
    obata = tmp_path / "obata.tif"
    _write(obata, np.array([[0, 2014, 2014, 0]], dtype=np.uint16), Affine(30, 0, -30, 0, -30, 60), 0)
    hansen = tmp_path / "hansen.tif"
    _write(hansen, np.array([[[16, 16, 0]], [[90, 90, 90]]], dtype=np.uint8), t, None)

    table = write_stand_age(run, obata=obata, hansen=hansen)

    with rasterio.open(run / "add_back_cut_year.tif") as src:
        assert src.read(1).tolist() == [[0, 2016, 0]]     # published pixel is not dated
        assert src.nodata == 0
    with rasterio.open(run / "add_back_cut_year_detector.tif") as src:
        assert src.read(1).tolist() == [[0, D.HANSEN, 0]]
    saved = pd.read_csv(run / "add_back_stand_age.csv")
    assert saved["pixels"].sum() == 2 + 1                  # two union rows, one two-method row
    assert list(table.columns) == ["agreement", "stand_age", "age_class", "pixels", "acres"]
