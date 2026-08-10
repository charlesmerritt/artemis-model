"""Tests for draft Florida management-unit helpers."""

from pathlib import Path
import sys

import geopandas as gpd
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.sketch_management_units import (
    SQ_M_PER_ACRE,
    area_accounting_table,
    build_exclusion_layer,
    build_riparian_buffer_layer,
    classify_stream_fcode,
    classify_unit_size,
    clean_geometries,
    erase,
    feet_to_meters,
    polygon_parts,
    split_large_geometry,
    target_grid_cell_size_m,
)

BUFFER_WIDTHS = {
    "ephemeral_intermittent": feet_to_meters(35),
    "perennial_small": feet_to_meters(50),
    "perennial_large": feet_to_meters(75),
}


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



def test_clean_geometries_keeps_area_of_valid_clockwise_multipolygon():
    # Regression, taken from the Union County managed layer (translated to the origin and
    # rounded). Two disjoint parts, both exterior rings wound clockwise. The MultiPolygon
    # is valid -- OGC validity does not constrain ring orientation -- but buffer(0) reads
    # the smaller part as a hole of the larger one and erases it, losing 2,186 m2. That
    # single polygon accounted for the entire county-level area-balance residual.
    notched = Polygon(
        [(160.6, 172), (158.98, 156.93), (107, 0.16), (107, 47), (77, 47), (77, 77),
         (107, 77), (107, 308.99), (140.79, 313.47), (141.26, 311.87)]
    )
    detached = Polygon(
        [(0.96, 77), (17, 77), (17, 96.6), (25.5, 107), (47, 107), (47, 47),
         (4.56, 47), (0.77, 76.77)]
    )
    geometry = MultiPolygon([notched, detached])
    assert geometry.is_valid
    assert not shapely.is_ccw(notched.exterior)
    # Guard the premise: this is the operation the old implementation performed.
    assert geometry.buffer(0).area < geometry.area

    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[geometry], crs="EPSG:5070")

    cleaned = clean_geometries(gdf)

    assert len(cleaned) == 1
    assert cleaned.geometry.area.sum() == pytest.approx(geometry.area)


def test_clean_geometries_repairs_self_intersecting_polygon():
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert not bowtie.is_valid
    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[bowtie], crs="EPSG:5070")

    cleaned = clean_geometries(gdf)

    assert len(cleaned) == 1
    assert cleaned.geometry.iloc[0].is_valid


def test_clean_geometries_drops_empty_geometries():
    gdf = gpd.GeoDataFrame(
        {"a": [1, 2]},
        geometry=[box(0, 0, 10, 10), Polygon()],
        crs="EPSG:5070",
    )

    cleaned = clean_geometries(gdf)

    assert list(cleaned["a"]) == [1]


def test_split_large_geometry_keeps_parts_at_or_below_target_area():
    # 1,000 m x 1,000 m = 100 ha, so a 40 ha target should split it.
    geometry = box(0, 0, 1_000, 1_000)

    parts = split_large_geometry(geometry, target_max_area_ha=40.0)

    assert len(parts) > 1
    assert sum(part.area for part in parts) == pytest.approx(geometry.area)
    assert max(part.area for part in parts) <= 40.0 * 10_000 + 1e-6


def test_split_large_geometry_keeps_polygons_from_geometry_collection_results():
    # Regression: a fishnet cell that fully contains one part of a multipart polygon while
    # only *touching* a detached part returns GeometryCollection([Polygon, Point]). The old
    # geom_type check matched neither "Polygon" nor "MultiPolygon" and dropped the whole
    # result, taking the contained polygon with it — 23 ha of Columbia County forest.
    side = target_grid_cell_size_m(40.0)
    contained = box(0, 0, side - 10, side - 10)  # inside the first cell, clear of its corner
    touching = box(side, side, side + 400, side + 400)  # touches that cell only at (side, side)
    geometry = MultiPolygon([contained, touching])
    assert geometry.is_valid
    # Guard the premise: the cell-0 clip really is a mixed collection.
    clipped = geometry.intersection(box(0, 0, side, side))
    assert clipped.geom_type == "GeometryCollection"

    parts = split_large_geometry(geometry, target_max_area_ha=40.0)

    assert sum(part.area for part in parts) == pytest.approx(geometry.area)
    assert all(part.geom_type == "Polygon" for part in parts)


def test_polygon_parts_recurses_and_drops_zero_area_debris():
    collection = GeometryCollection(
        [box(0, 0, 10, 10), Point(50, 50), LineString([(0, 20), (10, 20)])]
    )

    parts = polygon_parts(collection)

    assert [p.geom_type for p in parts] == ["Polygon"]
    assert parts[0].area == pytest.approx(100.0)


def test_polygon_parts_returns_empty_for_geometry_without_area():
    assert polygon_parts(LineString([(0, 0), (1, 1)])) == []
    assert polygon_parts(Polygon()) == []


def _streams(rows):
    return gpd.GeoDataFrame(
        {"fcode": [fcode for fcode, _ in rows]},
        geometry=[geom for _, geom in rows],
        crs="EPSG:5070",
    )


def test_build_riparian_buffer_layer_carries_class_without_merging_classes():
    # Ephemeral reach runs the full length; the perennial reach covers only the first half.
    streams = _streams(
        [
            (46000, LineString([(0, 0), (200, 0)])),  # ephemeral, 35 ft
            (46006, LineString([(0, 0), (100, 0)])),  # perennial small, 50 ft
        ]
    )

    layer = build_riparian_buffer_layer(streams, BUFFER_WIDTHS)

    assert set(layer["buffer_class"]) == {"ephemeral_intermittent", "perennial_small"}
    assert layer.crs == "EPSG:5070"


def test_build_riparian_buffer_layer_rows_are_disjoint_with_wider_class_winning():
    streams = _streams(
        [
            (46000, LineString([(0, 0), (200, 0)])),
            (46006, LineString([(0, 0), (100, 0)])),
        ]
    )

    layer = build_riparian_buffer_layer(streams, BUFFER_WIDTHS)
    by_class = dict(zip(layer["buffer_class"], layer.geometry))

    # The more protective class keeps its full buffer; the narrower one is what got cut.
    expected_perennial = LineString([(0, 0), (100, 0)]).buffer(BUFFER_WIDTHS["perennial_small"])
    assert by_class["perennial_small"].area == pytest.approx(expected_perennial.area)

    overlap = by_class["perennial_small"].intersection(by_class["ephemeral_intermittent"])
    assert overlap.area == pytest.approx(0.0, abs=1e-9)


def test_build_riparian_buffer_layer_returns_empty_layer_when_no_classified_streams():
    layer = build_riparian_buffer_layer(_streams([(55800, LineString([(0, 0), (100, 0)]))]), BUFFER_WIDTHS)

    assert len(layer) == 0
    assert "buffer_class" in layer.columns


def test_build_exclusion_layer_tags_both_classes():
    waterbodies = gpd.GeoDataFrame(geometry=[box(0, -50, 100, 50)], crs="EPSG:5070")
    roads = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (200, 0)])], crs="EPSG:5070")

    layer = build_exclusion_layer(waterbodies, roads, road_buffer_m=3.0)

    assert list(layer["exclusion_class"]) == ["waterbody", "road_buffer"]
    assert layer.crs == "EPSG:5070"


def test_build_exclusion_layer_leaves_rows_undissolved():
    # A county-wide union_all over NHD swamp polygons segfaults GEOS, so every input
    # feature must survive as its own row.
    waterbodies = gpd.GeoDataFrame(
        geometry=[box(0, 0, 10, 10), box(5, 5, 15, 15), box(40, 40, 50, 50)],
        crs="EPSG:5070",
    )
    roads = gpd.GeoDataFrame(
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(0, 20), (100, 20)])],
        crs="EPSG:5070",
    )

    layer = build_exclusion_layer(waterbodies, roads, road_buffer_m=3.0)

    assert len(layer) == 5
    assert (layer["exclusion_class"] == "waterbody").sum() == 3
    assert (layer["exclusion_class"] == "road_buffer").sum() == 2


def test_erase_handles_overlapping_erase_rows_without_double_counting():
    # Overlapping erase rows are applied successively, so the remainder is the difference
    # against their union even though no union is ever built.
    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[box(0, 0, 10, 10)], crs="EPSG:5070")
    overlapping = gpd.GeoDataFrame(
        {"exclusion_class": ["waterbody", "waterbody"]},
        geometry=[box(0, 0, 4, 10), box(2, 0, 6, 10)],
        crs="EPSG:5070",
    )

    result = erase(gdf, overlapping)

    assert result.geometry.area.sum() == pytest.approx(40.0)


def test_build_exclusion_layer_handles_missing_inputs():
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:5070")

    layer = build_exclusion_layer(empty, empty)

    assert len(layer) == 0
    assert "exclusion_class" in layer.columns


def test_erase_passes_input_through_when_nothing_to_erase():
    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[box(0, 0, 10, 10)], crs="EPSG:5070")

    result = erase(gdf, gpd.GeoDataFrame(geometry=[], crs="EPSG:5070"))

    assert len(result) == 1
    assert result.geometry.iloc[0].area == pytest.approx(100.0)


def test_erase_removes_every_row_of_the_erase_layer():
    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[box(0, 0, 10, 10)], crs="EPSG:5070")
    erase_gdf = gpd.GeoDataFrame(
        {"exclusion_class": ["waterbody", "road_buffer"]},
        geometry=[box(0, 0, 2, 10), box(8, 0, 10, 10)],
        crs="EPSG:5070",
    )

    result = erase(gdf, erase_gdf)

    assert result.geometry.area.sum() == pytest.approx(60.0)


def test_area_accounting_table_balances_managed_plus_riparian_against_eligible():
    # Forest 100 ha; 4 ha excluded; the rest splits 76/20 managed/riparian.
    table = area_accounting_table(
        forest_in_parcels_m2=1_000_000,
        waterbody_excluded_m2=10_000,
        road_excluded_m2=30_000,
        managed_m2=760_000,
        riparian_m2=200_000,
    )
    by_line = dict(zip(table["line"], table["area_ha"]))

    assert by_line["excluded_total"] == pytest.approx(4.0)
    assert by_line["eligible_forest"] == pytest.approx(96.0)
    assert by_line["managed"] + by_line["riparian"] == pytest.approx(by_line["eligible_forest"])
    assert by_line["balance_residual"] == pytest.approx(0.0)


def test_area_accounting_table_surfaces_a_broken_balance_as_residual():
    # Riparian acres silently dropped: the residual must expose them, not hide them.
    table = area_accounting_table(
        forest_in_parcels_m2=1_000_000,
        waterbody_excluded_m2=0,
        road_excluded_m2=0,
        managed_m2=760_000,
        riparian_m2=0,
    )
    by_line = dict(zip(table["line"], table["area_ha"]))

    assert by_line["balance_residual"] == pytest.approx(24.0)


def test_area_accounting_table_reports_acres_alongside_hectares():
    table = area_accounting_table(
        forest_in_parcels_m2=1_000_000,
        waterbody_excluded_m2=0,
        road_excluded_m2=0,
        managed_m2=1_000_000,
        riparian_m2=0,
    )
    row = table[table["line"] == "forest_in_parcels"].iloc[0]

    assert row["area_acres"] == pytest.approx(1_000_000 / SQ_M_PER_ACRE)
