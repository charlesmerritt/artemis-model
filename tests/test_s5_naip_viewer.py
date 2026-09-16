"""
Tests for the pure (network-free, widget-free) helpers in pipeline/s5_imagery/naip_viewer.py.

The ``NaipYearSlider`` class itself needs Earth Engine and a live kernel, so it is
not exercised here. What is exercised is everything the slider's correctness rests
on: which years a ±N window can actually resolve to, and the hatched-border
geometry that draws the AOI over the imagery.
"""

import sys
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s5_imagery import naip_viewer as viewer
from pipeline.s5_imagery import vectors

TODAY = 2026

# A ~900 m square in north Florida, expressed in WGS84 the way an AOI arrives.
AOI = vectors.to_wgs84(box(1_211_025.0, 936_705.0, 1_211_925.0, 937_605.0))


# ──────────────────────────────────────────────────────────────────────────────
# resolve_year_window
# ──────────────────────────────────────────────────────────────────────────────


def test_window_is_symmetric_when_every_year_exists():
    window = viewer.resolve_year_window(2015, back_years=2, forward_years=2, today_year=TODAY)
    assert window.years == [2013, 2014, 2015, 2016, 2017]
    assert window.before == [2013, 2014]
    assert window.after == [2016, 2017]
    assert window.warnings == []


def test_future_half_of_the_window_is_trimmed_with_a_warning():
    window = viewer.resolve_year_window(2022, back_years=10, forward_years=10, today_year=TODAY)

    assert window.requested == list(range(2012, 2033))
    assert window.years == list(range(2012, 2027))
    assert max(window.years) == TODAY
    assert any("in the future" in message for message in window.warnings)
    # The soft warning the workflow depends on: four years after 2022, not ten.
    assert any("Only 4 year(s) available after 2022" in message for message in window.warnings)


def test_years_before_naip_are_dropped_with_a_warning():
    window = viewer.resolve_year_window(2005, back_years=10, forward_years=2, today_year=TODAY)
    assert min(window.years) == 2003
    assert any("predate NAIP" in message for message in window.warnings)


def test_available_years_filter_the_window_and_are_reported():
    window = viewer.resolve_year_window(
        2022, back_years=4, forward_years=4, available=[2019, 2021, 2023], today_year=TODAY
    )
    assert window.years == [2019, 2021, 2023]
    assert window.missing == [2018, 2020, 2022, 2024, 2025, 2026]
    assert any("No NAIP over this extent" in message for message in window.warnings)


def test_a_target_year_with_no_imagery_is_called_out():
    window = viewer.resolve_year_window(
        2022, back_years=2, forward_years=2, available=[2021, 2023], today_year=TODAY
    )
    assert 2022 not in window.years
    assert any("target year 2022 itself has no NAIP" in message for message in window.warnings)


def test_a_window_with_nothing_in_it_raises():
    with pytest.raises(ValueError, match="No usable NAIP years"):
        viewer.resolve_year_window(2022, back_years=1, forward_years=1, available=[1999], today_year=TODAY)


def test_negative_window_is_rejected():
    with pytest.raises(ValueError, match=">= 0"):
        viewer.resolve_year_window(2022, back_years=-1, today_year=TODAY)


# ──────────────────────────────────────────────────────────────────────────────
# hatch_ticks
# ──────────────────────────────────────────────────────────────────────────────


def test_hatch_ticks_follow_the_boundary_at_the_requested_spacing():
    ticks = viewer.hatch_ticks(AOI, spacing_m=100.0, length_m=40.0)
    # A 900 m square has a 3,600 m perimeter: 36 ticks at 100 m spacing.
    assert len(ticks.geoms) == 36


def test_every_tick_starts_on_the_boundary_and_has_the_requested_length():
    ticks = viewer.hatch_ticks(AOI, spacing_m=150.0, length_m=45.0)
    projected_aoi = vectors.to_equal_area(AOI)
    projected_ticks = vectors.to_equal_area(ticks)

    for tick in projected_ticks.geoms:
        assert tick.length == pytest.approx(45.0, abs=0.5)
        start = tick.interpolate(0.0)
        assert projected_aoi.exterior.distance(start) < 0.5


def test_ticks_point_into_the_polygon():
    """The hatching marks the inside of the boundary, the way a cut line is drawn."""
    ticks = viewer.hatch_ticks(AOI, spacing_m=150.0, length_m=45.0)
    projected_aoi = vectors.to_equal_area(AOI)

    for tick in vectors.to_equal_area(ticks).geoms:
        midpoint = tick.interpolate(0.5, normalized=True)
        assert projected_aoi.contains(midpoint)


def test_hatch_spacing_defaults_to_the_polygon_size():
    small = viewer.hatch_ticks(AOI)
    big = viewer.hatch_ticks(vectors.to_wgs84(vectors.to_equal_area(AOI).buffer(5_000)))
    # Both get a comparable tick count rather than one being unreadably dense.
    assert 0.5 < len(small.geoms) / len(big.geoms) < 2.0


def test_hatching_covers_every_ring_of_a_multipolygon():
    other = vectors.to_wgs84(box(1_215_000.0, 936_705.0, 1_215_900.0, 937_605.0))
    both = MultiPolygon([AOI, other])
    ticks = viewer.hatch_ticks(both, spacing_m=100.0, length_m=40.0)
    assert len(ticks.geoms) == 72


def test_hatch_ticks_rejects_a_boundary_parallel_angle():
    with pytest.raises(ValueError, match="multiple of 180"):
        viewer.hatch_ticks(AOI, spacing_m=100.0, angle_deg=180.0)


def test_hatch_ticks_guards_against_an_absurd_tick_count():
    with pytest.raises(ValueError, match="over the .* cap"):
        viewer.hatch_ticks(AOI, spacing_m=0.1)


def test_hatch_ticks_rejects_nonpositive_lengths():
    with pytest.raises(ValueError, match="length_m must be > 0"):
        viewer.hatch_ticks(AOI, spacing_m=100.0, length_m=0.0)


# ──────────────────────────────────────────────────────────────────────────────
# hatched_aoi_layers
# ──────────────────────────────────────────────────────────────────────────────


def test_layers_are_a_transparent_fill_plus_hatching():
    layers = viewer.hatched_aoi_layers(AOI, spacing_m=100.0, length_m=40.0)

    assert set(layers) == {"aoi", "hatch"}
    assert layers["aoi"]["style"]["fillOpacity"] < 0.15
    assert layers["hatch"]["style"]["fillOpacity"] == 0.0

    for entry in layers.values():
        assert entry["geojson"]["type"] == "FeatureCollection"
        assert len(entry["geojson"]["features"]) == 1

    assert layers["aoi"]["geojson"]["features"][0]["geometry"]["type"] == "Polygon"
    assert layers["hatch"]["geojson"]["features"][0]["geometry"]["type"] == "MultiLineString"


# ──────────────────────────────────────────────────────────────────────────────
# fit_zoom
# ──────────────────────────────────────────────────────────────────────────────


def test_fit_zoom_is_tighter_for_a_smaller_aoi():
    stand = viewer.fit_zoom((-82.62, 30.10, -82.60, 30.12))
    county = viewer.fit_zoom((-83.0, 29.8, -82.2, 30.6))
    state = viewer.fit_zoom((-87.6, 24.4, -80.0, 31.0))
    assert stand > county > state


def test_fit_zoom_stays_within_bounds():
    assert viewer.fit_zoom((-180, -85, 180, 85)) >= 3
    # A degenerate, near-zero-extent AOI must not ask for zoom 40.
    assert viewer.fit_zoom((-82.6, 30.1, -82.6, 30.1)) <= 18


def test_fit_zoom_needs_no_event_loop():
    """ipyleaflet's fit_bounds schedules an asyncio task; this must not."""
    import asyncio

    with pytest.raises(RuntimeError):
        asyncio.get_event_loop()
    assert isinstance(viewer.fit_zoom(AOI.bounds), int)


def test_layer_styles_are_copies_callers_can_edit():
    layers = viewer.hatched_aoi_layers(AOI, spacing_m=200.0)
    layers["aoi"]["style"]["color"] = "#000000"
    assert viewer.AOI_STYLE["color"] != "#000000"
