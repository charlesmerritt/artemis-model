"""Tests for draft Florida management-unit helpers."""

from pathlib import Path
import sys

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.sketch_management_units import (
    BUFFER_CLASS_PRIORITY,
    build_buffer_polygons,
    check_partition,
    classify_stream_fcode,
    classify_unit_size,
    clean_geometries,
    feet_to_meters,
    partition_forest,
    split_large_geometry,
    target_grid_cell_size_m,
)

CRS = "EPSG:5070"


def test_feet_to_meters_converts_florida_bmp_width():
    assert feet_to_meters(50) == pytest.approx(15.24)


@pytest.mark.parametrize(
    ("fcode", "expected"),
    [
        (46000, "ephemeral_intermittent"),
        (46003, "ephemeral_intermittent"),
        (46007, "ephemeral_intermittent"),
        (46006, "perennial_small"),
        (55800, None),
        (None, None),
    ],
)
def test_classify_stream_fcode_uses_documented_florida_mapping(fcode, expected):
    assert classify_stream_fcode(fcode) == expected


@pytest.mark.parametrize(
    ("area_ha", "expected"),
    [(1.99, "sliver_lt_min"), (2.0, "candidate"), (40.0, "candidate"), (40.01, "large_gt_target")],
)
def test_classify_unit_size_uses_min_and_target_thresholds(area_ha, expected):
    assert classify_unit_size(area_ha, min_area_ha=2.0, target_max_area_ha=40.0) == expected


def test_target_grid_cell_size_matches_target_area():
    side_m = target_grid_cell_size_m(40.0)
    assert side_m == pytest.approx((40.0 * 10_000) ** 0.5)


def test_clean_geometries_preserves_line_features_for_buffer_inputs():
    gdf = gpd.GeoDataFrame({"name": ["road"]}, geometry=[LineString([(0, 0), (1, 1)])], crs="EPSG:5070")

    cleaned = clean_geometries(gdf)

    assert len(cleaned) == 1
    assert cleaned.geom_type.iloc[0] == "LineString"



def test_split_large_geometry_keeps_parts_at_or_below_target_area():
    # 1,000 m x 1,000 m = 100 ha, so a 40 ha target should split it.
    geometry = box(0, 0, 1_000, 1_000)

    parts = split_large_geometry(geometry, target_max_area_ha=40.0)

    assert len(parts) > 1
    assert sum(part.area for part in parts) == pytest.approx(geometry.area)
    assert max(part.area for part in parts) <= 40.0 * 10_000 + 1e-6


# ---- riparian buffers as retained units ------------------------------------------------

WIDTHS = {"ephemeral_intermittent": 10.0, "perennial_small": 20.0, "perennial_large": 30.0}


def _streams(classes_and_lines):
    return gpd.GeoDataFrame(
        {"buffer_class": [c for c, _ in classes_and_lines]},
        geometry=[line for _, line in classes_and_lines],
        crs=CRS,
    )


def test_buffer_polygons_carry_their_class():
    streams = _streams([("perennial_small", LineString([(0, 0), (100, 0)]))])
    buffers = build_buffer_polygons(streams, WIDTHS)
    assert list(buffers["buffer_class"]) == ["perennial_small"]
    assert buffers.geometry.area.sum() > 0


def test_overlapping_buffer_classes_do_not_double_count_area():
    """Buffers must partition, so overlap has to resolve to exactly one class."""
    streams = _streams([
        ("perennial_large", LineString([(0, 0), (100, 0)])),
        ("ephemeral_intermittent", LineString([(0, 5), (100, 5)])),   # overlaps the above
    ])
    buffers = build_buffer_polygons(streams, WIDTHS)
    summed = buffers.geometry.area.sum()
    unioned = buffers.geometry.union_all().area
    assert summed == pytest.approx(unioned, rel=1e-9)


def test_the_widest_class_wins_contested_ground():
    """The conservative direction: more protection, not less, where buffers disagree."""
    streams = _streams([
        ("perennial_large", LineString([(0, 0), (100, 0)])),
        ("ephemeral_intermittent", LineString([(0, 5), (100, 5)])),
    ])
    buffers = build_buffer_polygons(streams, WIDTHS)
    by_class = buffers.groupby("buffer_class").apply(
        lambda g: g.geometry.area.sum(), include_groups=False
    )
    alone = build_buffer_polygons(
        _streams([("perennial_large", LineString([(0, 0), (100, 0)]))]), WIDTHS
    ).geometry.area.sum()
    assert by_class["perennial_large"] == pytest.approx(alone)   # kept whole
    assert by_class.get("ephemeral_intermittent", 0.0) < alone   # yielded the overlap


def test_buffer_class_priority_is_widest_first():
    assert BUFFER_CLASS_PRIORITY.index("perennial_large") < \
           BUFFER_CLASS_PRIORITY.index("perennial_small") < \
           BUFFER_CLASS_PRIORITY.index("ephemeral_intermittent")


def test_waterbody_buffer_is_the_ring_not_the_water():
    """Open water is non-forest; the SMZ around it is forest under a no-entry rule."""
    water = gpd.GeoDataFrame(geometry=[Polygon([(0, 0), (50, 0), (50, 50), (0, 50)])], crs=CRS)
    streams = _streams([])
    buffers = build_buffer_polygons(streams, WIDTHS, waterbodies=water, waterbody_width_m=10.0)
    assert list(buffers["buffer_class"]) == ["waterbody"]
    assert not buffers.geometry.iloc[0].intersection(water.geometry.iloc[0]).area > 1e-9


def test_no_buffers_yields_an_empty_layer_with_the_right_schema():
    buffers = build_buffer_polygons(_streams([]), WIDTHS)
    assert len(buffers) == 0
    assert "buffer_class" in buffers.columns


# ---- the partition ---------------------------------------------------------------------

def _forest(width=200.0):
    return gpd.GeoDataFrame(
        {"PARCELID": ["p1"]},
        geometry=[box(0, -50, width, 50)],
        crs=CRS,
    )


def test_managed_and_riparian_partition_the_eligible_forest():
    buffers = build_buffer_polygons(
        _streams([("perennial_small", LineString([(0, 0), (200, 0)]))]), WIDTHS
    )
    managed, riparian, accounting = partition_forest(_forest(), buffers)
    assert accounting["partition_residual_ha"] == pytest.approx(0.0, abs=1e-9)
    assert managed.geometry.area.sum() > 0
    assert riparian.geometry.area.sum() > 0
    check_partition(accounting)      # does not raise


def test_buffer_acres_are_retained_rather_than_erased():
    """The bug this replaces: buffered acres were neither managed nor grown.

    They were unioned into the erase layer and differenced away, so they vanished from the
    projected landscape entirely — an under-count of standing volume and carbon, not a
    conservative choice.
    """
    buffers = build_buffer_polygons(
        _streams([("perennial_small", LineString([(0, 0), (200, 0)]))]), WIDTHS
    )
    forest = _forest()
    managed, riparian, accounting = partition_forest(forest, buffers)
    erased_only = managed.geometry.area.sum() / 10_000
    assert accounting["riparian_ha"] > 0
    assert accounting["eligible_ha"] == pytest.approx(erased_only + accounting["riparian_ha"])


def test_riparian_units_keep_their_buffer_class_and_smz_share():
    buffers = build_buffer_polygons(
        _streams([("perennial_small", LineString([(0, 0), (200, 0)]))]), WIDTHS
    )
    _, riparian, _ = partition_forest(_forest(), buffers)
    assert set(riparian["buffer_class"]) == {"perennial_small"}
    assert (riparian["unit_class"] == "riparian").all()
    assert (riparian["SMZ_Pct"] == 100.0).all()


def test_managed_units_report_no_smz_because_buffers_were_differenced_out():
    buffers = build_buffer_polygons(
        _streams([("perennial_small", LineString([(0, 0), (200, 0)]))]), WIDTHS
    )
    managed, _, _ = partition_forest(_forest(), buffers)
    assert (managed["unit_class"] == "managed").all()
    assert (managed["SMZ_Pct"] == 0.0).all()


def test_riparian_units_assign_no_management_through_the_existing_override():
    """The interlock: SMZ_Pct = 100 makes the tested riparian override fire, no new logic."""
    from pipeline.s3_management.regime_assignment import assign_prescription

    buffers = build_buffer_polygons(
        _streams([("perennial_small", LineString([(0, 0), (200, 0)]))]), WIDTHS
    )
    _, riparian, _ = partition_forest(_forest(), buffers)
    unit = riparian.iloc[0].to_dict()
    unit["OWN_CODE"] = 4          # corporate pine would otherwise be a plantation rotation
    unit["FORTYPCD"] = 161
    prescription = assign_prescription(unit)
    assert prescription.prescription_id == "no_management"
    assert prescription.template == "no_management"


def test_hard_exclusions_are_removed_from_both_classes_and_reported():
    """Water and the road buffer are not stands, and their acres must not vanish silently."""
    buffers = build_buffer_polygons(
        _streams([("perennial_small", LineString([(0, 0), (200, 0)]))]), WIDTHS
    )
    hard = gpd.GeoDataFrame(geometry=[box(150, -50, 200, 50)], crs=CRS)
    _, _, accounting = partition_forest(_forest(), buffers, hard)
    assert accounting["hard_excluded_ha"] > 0
    assert accounting["eligible_ha"] < accounting["forested_parcel_ha"]
    assert accounting["partition_residual_ha"] == pytest.approx(0.0, abs=1e-9)


def test_check_partition_raises_when_acres_go_missing():
    bad = {"eligible_ha": 100.0, "managed_ha": 60.0, "riparian_ha": 20.0,
           "partition_residual_ha": 20.0}
    with pytest.raises(ValueError, match="does not account for"):
        check_partition(bad)


def test_partition_without_buffers_is_all_managed():
    empty = build_buffer_polygons(_streams([]), WIDTHS)
    managed, riparian, accounting = partition_forest(_forest(), empty)
    assert len(riparian) == 0
    assert accounting["riparian_ha"] == 0.0
    assert accounting["managed_ha"] == pytest.approx(accounting["eligible_ha"])
