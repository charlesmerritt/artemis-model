"""Tests for the pure (network-free) helpers in pipeline/s5_imagery/naip_acquire.py."""

import datetime as dt
import sys
from pathlib import Path

import pytest
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s5_imagery import naip_acquire as naip

AOI_BOX = box(-82.60, 30.10, -82.59, 30.11)
EXTENT_BOX = box(-82.62, 30.08, -82.57, 30.13)


# ---- resolve_years ----


def test_resolve_years_explicit_list_sorts_and_dedupes():
    assert naip.resolve_years(years="2023,2019,2021,2019") == [2019, 2021, 2023]


def test_resolve_years_tolerates_whitespace():
    assert naip.resolve_years(years=" 2019 , 2021 ") == [2019, 2021]


def test_resolve_years_range():
    assert naip.resolve_years(start_year=2018, end_year=2022) == [2018, 2019, 2020, 2021, 2022]


def test_resolve_years_range_with_step():
    assert naip.resolve_years(start_year=2015, end_year=2023, step=2) == [
        2015,
        2017,
        2019,
        2021,
        2023,
    ]


def test_resolve_years_rejects_both_forms():
    with pytest.raises(ValueError, match="not both"):
        naip.resolve_years(years="2021", start_year=2019, end_year=2021)


def test_resolve_years_requires_a_temporal_parameter():
    with pytest.raises(ValueError, match="No temporal parameter"):
        naip.resolve_years()


def test_resolve_years_rejects_reversed_range():
    with pytest.raises(ValueError, match="end-year"):
        naip.resolve_years(start_year=2022, end_year=2019)


def test_resolve_years_rejects_zero_step():
    with pytest.raises(ValueError, match="year-step"):
        naip.resolve_years(start_year=2019, end_year=2022, step=0)


def test_resolve_years_rejects_pre_naip_years():
    with pytest.raises(ValueError, match="NAIP starts"):
        naip.resolve_years(years="1999")


def test_resolve_years_rejects_future_years():
    future = dt.date.today().year + 1
    with pytest.raises(ValueError, match="future"):
        naip.resolve_years(years=str(future))


def test_resolve_years_rejects_non_numeric():
    with pytest.raises(ValueError, match="Not a year"):
        naip.resolve_years(years="2019,latest")


# ---- fill_candidate_years ----


def test_fill_candidate_years_orders_by_distance_then_newest():
    # Same distance: the newer year wins, since it sits closer to every later
    # year in the series than the older one does.
    assert naip.fill_candidate_years(2021, 2) == [2022, 2020, 2023, 2019]


def test_fill_candidate_years_filters_to_available():
    # 2020 is one year out; 2024 and 2018 are both three, so the newer one leads.
    assert naip.fill_candidate_years(2021, 3, available=[2018, 2020, 2024]) == [2020, 2024, 2018]


def test_fill_candidate_years_excludes_the_target():
    assert 2021 not in naip.fill_candidate_years(2021, 3)


def test_fill_candidate_years_clamps_below_naip_start():
    assert all(year >= naip.NAIP_FIRST_YEAR for year in naip.fill_candidate_years(2004, 4))


def test_fill_candidate_years_zero_window_is_empty():
    assert naip.fill_candidate_years(2021, 0) == []


def test_fill_candidate_years_rejects_negative_window():
    with pytest.raises(ValueError):
        naip.fill_candidate_years(2021, -1)


# ---- export_size_ok ----


def test_export_size_ok_counts_bands():
    assert naip.export_size_ok(300, band_count=3, max_pixels=1000)
    assert not naip.export_size_ok(400, band_count=3, max_pixels=1000)


def test_export_size_ok_treats_zero_bands_as_one():
    assert naip.export_size_ok(1000, band_count=0, max_pixels=1000)


# ---- build_manifest ----


def _year_entry(year, coverage=0.9999, complete=True, contributing=None):
    return {
        "year": year,
        "coverage": coverage,
        "complete": complete,
        "contributing_years": contributing or [year],
        "image_count": 12,
        "bounds": [-82.62, 30.08, -82.57, 30.13],
        "tile_url": "https://earthengine.googleapis.com/v1/x/tiles/{z}/{x}/{y}",
        "tile_url_generated_utc": "2026-08-10T12:00:00+00:00",
        "export": None,
    }


def _manifest(year_entries, aoi=AOI_BOX):
    return naip.build_manifest(
        name="Test Stands",
        slug="test_stands",
        extent_geom=EXTENT_BOX,
        aoi_geom=aoi,
        extent_source="config/extent.geojson",
        aoi_source="config/aoi.geojson",
        bands=["R", "G", "B"],
        scale_m=1.0,
        crs="EPSG:4326",
        coverage_settings={"mode": "fill", "min_coverage": 0.999, "fill_window": 2, "scale_m": 30},
        year_entries=year_entries,
    )


def test_build_manifest_core_fields():
    manifest = _manifest([_year_entry(2021)])

    assert manifest["schema"] == naip.MANIFEST_SCHEMA
    assert manifest["collection"] == naip.NAIP_COLLECTION
    assert manifest["bands"] == ["R", "G", "B"]
    assert manifest["extent"]["geojson"] == "extent.geojson"
    assert manifest["extent"]["area_ha"] > 0
    assert manifest["years"][0]["year"] == 2021


def test_build_manifest_records_aoi_containment():
    manifest = _manifest([_year_entry(2021)])
    assert manifest["aoi"]["containment_in_extent"] == pytest.approx(1.0, abs=1e-4)


def test_build_manifest_flags_aoi_outside_extent():
    # AOI pushed half outside the extent — the fraction must show it.
    straddling = box(-82.58, 30.12, -82.50, 30.20)
    manifest = _manifest([_year_entry(2021)], aoi=straddling)
    assert manifest["aoi"]["containment_in_extent"] < 0.5


def test_build_manifest_omits_aoi_when_absent():
    manifest = _manifest([_year_entry(2021)], aoi=None)
    assert "aoi" not in manifest


def test_build_manifest_collects_incomplete_years():
    manifest = _manifest(
        [
            _year_entry(2019, coverage=0.62, complete=False),
            _year_entry(2021),
            _year_entry(2023, coverage=0.0, complete=False),
        ]
    )
    assert manifest["incomplete_years"] == [2019, 2023]


def test_build_manifest_preserves_gap_fill_provenance():
    manifest = _manifest([_year_entry(2021, contributing=[2020, 2021])])
    assert manifest["years"][0]["contributing_years"] == [2020, 2021]
    assert manifest["incomplete_years"] == []
