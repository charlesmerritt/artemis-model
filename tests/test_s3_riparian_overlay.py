"""
Tests for the riparian overlay (pipeline/s3_management/riparian_overlay.py).

The overlay is the last step in stand delineation: it runs on a settled stand map and
reclassifies the buffered parts as untouchable. Two invariants carry most of the weight —
**stands stay contiguous** (a stream through a stand makes two stands, not one stand on
both banks) and **area is conserved** (the overlay reclassifies ground, it never erases
any).
"""

from pathlib import Path
import sys

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiPolygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.riparian_overlay import (
    MANAGED,
    RIPARIAN,
    check_area_conserved,
    check_contiguity,
    explode_to_stands,
    overlay_riparian,
    summarize_overlay,
)
from pipeline.s3_management.sketch_management_units import build_buffer_polygons

CRS = "EPSG:5070"
WIDTHS = {"perennial_small": 15.0}


def _stands(*boxes):
    return gpd.GeoDataFrame(
        {"unit_id": [f"mu_{i:03d}" for i in range(len(boxes))]},
        geometry=list(boxes), crs=CRS,
    )


def _stream_buffer(line=LineString([(0, 0), (400, 0)])):
    streams = gpd.GeoDataFrame({"buffer_class": ["perennial_small"]}, geometry=[line], crs=CRS)
    return build_buffer_polygons(streams, WIDTHS)


# ---- contiguity ------------------------------------------------------------------------

def test_a_stream_through_a_stand_makes_two_stands_not_one():
    """The invariant. A unit spanning both banks of a stream is not a stand."""
    units, _ = overlay_riparian(_stands(box(0, -100, 400, 100)), _stream_buffer())
    managed = units[units["unit_class"] == MANAGED]
    assert len(managed) == 2
    assert all(g.geom_type == "Polygon" for g in managed.geometry)


def test_the_two_halves_are_on_opposite_banks():
    units, _ = overlay_riparian(_stands(box(0, -100, 400, 100)), _stream_buffer())
    managed = units[units["unit_class"] == MANAGED]
    centroids = sorted(g.centroid.y for g in managed.geometry)
    assert centroids[0] < 0 < centroids[1]


def test_no_output_unit_is_ever_multipart():
    stands = _stands(box(0, -100, 400, 100), box(0, 200, 400, 400))
    units, _ = overlay_riparian(stands, _stream_buffer())
    assert not units.geometry.geom_type.str.startswith("Multi").any()
    check_contiguity(units)          # does not raise


def test_check_contiguity_rejects_a_multipart_unit():
    """Guard the guard — gpd.overlay returns multipart by default, so this must fire."""
    multipart = gpd.GeoDataFrame(
        {"unit_id": ["a"]},
        geometry=[MultiPolygon([box(0, 0, 10, 10), box(20, 0, 30, 10)])],
        crs=CRS,
    )
    with pytest.raises(ValueError, match="multipart"):
        check_contiguity(multipart)


def test_explode_to_stands_splits_multipart_and_drops_empties():
    multipart = gpd.GeoDataFrame(
        {"unit_id": ["a"]},
        geometry=[MultiPolygon([box(0, 0, 10, 10), box(20, 0, 30, 10)])],
        crs=CRS,
    )
    assert len(explode_to_stands(multipart)) == 2


# ---- classification --------------------------------------------------------------------

def test_the_buffered_strip_becomes_a_riparian_stand():
    units, _ = overlay_riparian(_stands(box(0, -100, 400, 100)), _stream_buffer())
    riparian = units[units["unit_class"] == RIPARIAN]
    assert len(riparian) == 1
    assert set(riparian["buffer_class"]) == {"perennial_small"}
    assert riparian.geometry.iloc[0].bounds[1] == pytest.approx(-15.0)


def test_riparian_units_carry_full_smz_and_managed_units_none():
    units, _ = overlay_riparian(_stands(box(0, -100, 400, 100)), _stream_buffer())
    assert (units.loc[units["unit_class"] == RIPARIAN, "SMZ_Pct"] == 100.0).all()
    assert (units.loc[units["unit_class"] == MANAGED, "SMZ_Pct"] == 0.0).all()


def test_riparian_units_assign_no_management_through_the_existing_override():
    """The interlock: SMZ_Pct = 100 fires the tested riparian override, no new regime logic."""
    from pipeline.s3_management.regime_assignment import assign_prescription

    units, _ = overlay_riparian(_stands(box(0, -100, 400, 100)), _stream_buffer())
    unit = units[units["unit_class"] == RIPARIAN].iloc[0].to_dict()
    unit["OWN_CODE"] = 4          # corporate pine would otherwise be a plantation rotation
    unit["FORTYPCD"] = 161
    prescription = assign_prescription(unit)
    assert prescription.prescription_id == "no_management"


def test_every_piece_traces_back_to_the_stand_it_came_from():
    """What the post-sliver ordering buys: the buffer is an annotation on a known stand."""
    units, _ = overlay_riparian(_stands(box(0, -100, 400, 100)), _stream_buffer())
    assert set(units["parent_unit_id"]) == {"mu_000"}
    assert units["unit_id"].is_unique


def test_a_stand_the_buffer_misses_passes_through_unchanged():
    stands = _stands(box(0, 500, 400, 700))
    units, accounting = overlay_riparian(stands, _stream_buffer())
    assert len(units) == 1
    assert units["unit_class"].iloc[0] == MANAGED
    assert accounting["riparian_units"] == 0


def test_no_buffers_leaves_the_stand_map_intact():
    stands = _stands(box(0, 0, 100, 100), box(200, 0, 300, 100))
    empty = gpd.GeoDataFrame({"buffer_class": []}, geometry=[], crs=CRS)
    units, accounting = overlay_riparian(stands, empty)
    assert len(units) == 2
    assert (units["unit_class"] == MANAGED).all()
    assert accounting["riparian_ha"] == 0.0


# ---- area conservation -----------------------------------------------------------------

def test_the_overlay_conserves_area_exactly():
    """It reclassifies ground; it must never erase any.

    The failure this guards is the original bug: buffered acres silently leaving the
    landscape, under-counting standing volume and carbon with nothing in any summary.
    """
    stands = _stands(box(0, -100, 400, 100), box(0, 200, 400, 400))
    units, accounting = overlay_riparian(stands, _stream_buffer())
    assert accounting["residual_ha"] == pytest.approx(0.0, abs=1e-9)
    assert units["unit_area_ha"].sum() == pytest.approx(accounting["stand_ha_in"])
    check_area_conserved(accounting)


def test_riparian_area_equals_the_buffer_inside_the_stand():
    units, _ = overlay_riparian(_stands(box(0, -100, 400, 100)), _stream_buffer())
    riparian_ha = units.loc[units["unit_class"] == RIPARIAN, "unit_area_ha"].sum()
    assert riparian_ha == pytest.approx(400 * 30 / 10_000)      # 400 m of 15 m each side


def test_check_area_conserved_raises_when_acres_go_missing():
    with pytest.raises(ValueError, match="did not conserve area"):
        check_area_conserved({"stand_ha_in": 100.0, "managed_ha": 60.0,
                              "riparian_ha": 20.0, "residual_ha": 20.0})


def test_overlay_requires_the_id_field():
    stands = gpd.GeoDataFrame({"other": ["x"]}, geometry=[box(0, 0, 10, 10)], crs=CRS)
    with pytest.raises(ValueError, match="unit_id"):
        overlay_riparian(stands, _stream_buffer())


# ---- reporting -------------------------------------------------------------------------

def test_summary_cuts_by_unit_class_and_buffer_class():
    units, _ = overlay_riparian(_stands(box(0, -100, 400, 100)), _stream_buffer())
    summary = summarize_overlay(units)
    assert set(summary["unit_class"]) == {MANAGED, RIPARIAN}
    riparian_row = summary[summary["unit_class"] == RIPARIAN].iloc[0]
    assert riparian_row["buffer_class"] == "perennial_small"
    assert riparian_row["stands"] == 1
