"""Tests for management-unit sliver resolution (pipeline/s3_management/sliver_merge.py)."""

from pathlib import Path
import sys

import geopandas as gpd
import pytest
from shapely.geometry import MultiPolygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.sliver_merge import (
    SQ_M_PER_ACRE,
    area_acres,
    drop_slivers,
    explode_to_singlepart,
    flag_slivers,
    merge_slivers_to_neighbors,
    resolve_slivers,
)

CRS = "EPSG:5070"  # projected, metres


def _acre_box(x0, y0, side_m, **attrs):
    return box(x0, y0, x0 + side_m, y0 + side_m)


def test_area_acres_rejects_geographic_crs():
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    with pytest.raises(ValueError, match="projected CRS"):
        area_acres(gdf)


def test_area_acres_matches_known_size():
    # A square of side sqrt(5 acres in m^2) is exactly 5 acres.
    side = (5 * SQ_M_PER_ACRE) ** 0.5
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, side, side)], crs=CRS)
    assert area_acres(gdf).iloc[0] == pytest.approx(5.0)


def test_flag_slivers_uses_min_acres_threshold():
    small = box(0, 0, 50, 50)      # 2500 m^2 ≈ 0.62 ac  -> sliver
    big = box(0, 0, 300, 300)      # 90000 m^2 ≈ 22.2 ac -> not
    gdf = gpd.GeoDataFrame({"id": [1, 2]}, geometry=[small, big], crs=CRS)
    flags = flag_slivers(gdf, min_acres=5.0)
    assert list(flags) == [True, False]


def test_explode_to_singlepart_splits_multipolygon():
    mp = MultiPolygon([box(0, 0, 10, 10), box(100, 100, 110, 110)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[mp], crs=CRS)
    exploded = explode_to_singlepart(gdf)
    assert len(exploded) == 2
    assert all(g.geom_type == "Polygon" for g in exploded.geometry)


def test_merge_sends_sliver_to_longest_shared_boundary():
    # A (below) shares 100 m with the sliver; C (right) shares 40 m -> sliver joins A.
    a = box(0, 0, 300, 300)          # ~22.2 ac
    c = box(300, 0, 600, 300)        # ~22.2 ac
    sliver = box(200, 300, 340, 340)  # 140 x 40 = 5600 m^2 ≈ 1.38 ac
    gdf = gpd.GeoDataFrame(
        {"unit_id": ["A", "C", "S"]},
        geometry=[a, c, sliver],
        crs=CRS,
    )
    a_area, c_area, s_area = a.area, c.area, sliver.area

    result = merge_slivers_to_neighbors(gdf, min_acres=5.0)

    # Sliver is gone; A and C remain.
    assert len(result) == 2
    assert set(result["unit_id"]) == {"A", "C"}
    # A absorbed the sliver (its geometry grew by the sliver's area); C is unchanged.
    merged_a = result.loc[result["unit_id"] == "A"].geometry.iloc[0]
    merged_c = result.loc[result["unit_id"] == "C"].geometry.iloc[0]
    assert merged_a.area == pytest.approx(a_area + s_area)
    assert merged_c.area == pytest.approx(c_area)
    # No slivers remain.
    assert not flag_slivers(result, min_acres=5.0).any()


def test_merge_conserves_total_area():
    a = box(0, 0, 300, 300)
    c = box(300, 0, 600, 300)
    sliver = box(200, 300, 340, 340)
    gdf = gpd.GeoDataFrame({"unit_id": ["A", "C", "S"]}, geometry=[a, c, sliver], crs=CRS)
    total_before = gdf.geometry.area.sum()
    result = merge_slivers_to_neighbors(gdf, min_acres=5.0)
    assert result.geometry.area.sum() == pytest.approx(total_before)


def test_merge_chains_sliver_cluster_onto_anchor():
    # Two adjacent slivers; only one touches the big unit. Both should end up merged in.
    anchor = box(0, 0, 300, 300)          # ~22 ac
    s1 = box(0, 300, 200, 340)            # touches anchor along 200 m; 0.99 ac sliver
    s2 = box(0, 340, 200, 360)            # touches s1 along 200 m; 0.99 ac sliver
    gdf = gpd.GeoDataFrame({"unit_id": ["A", "S1", "S2"]}, geometry=[anchor, s1, s2], crs=CRS)
    result = merge_slivers_to_neighbors(gdf, min_acres=5.0)
    assert len(result) == 1
    assert result["unit_id"].iloc[0] == "A"
    assert not flag_slivers(result, min_acres=5.0).any()


def test_orphan_sliver_kept_by_default_dropped_when_requested():
    lonely = box(0, 0, 50, 50)  # 0.62 ac sliver, touches nothing
    gdf = gpd.GeoDataFrame({"unit_id": ["S"]}, geometry=[lonely], crs=CRS)
    kept = merge_slivers_to_neighbors(gdf, min_acres=5.0, drop_orphans=False)
    assert len(kept) == 1
    dropped = merge_slivers_to_neighbors(gdf, min_acres=5.0, drop_orphans=True)
    assert len(dropped) == 0


def test_drop_slivers_removes_subthreshold_polygons():
    big = box(0, 0, 300, 300)   # ~22 ac
    small = box(400, 400, 450, 450)  # 0.62 ac
    gdf = gpd.GeoDataFrame({"unit_id": ["A", "S"]}, geometry=[big, small], crs=CRS)
    result = drop_slivers(gdf, min_acres=5.0)
    assert list(result["unit_id"]) == ["A"]


def test_resolve_slivers_explodes_then_applies_policy():
    # Multipart unit = one big part + one sliver part; explode + merge should fold them.
    big = box(0, 0, 300, 300)
    sliver = box(0, 300, 200, 340)
    mp = MultiPolygon([big, sliver])
    gdf = gpd.GeoDataFrame({"unit_id": ["A"]}, geometry=[mp], crs=CRS)
    result = resolve_slivers(gdf, policy="merge", min_acres=5.0)
    assert len(result) == 1
    assert not flag_slivers(result, min_acres=5.0).any()
    assert "area_acres" in result.columns


def test_resolve_slivers_rejects_unknown_policy():
    gdf = gpd.GeoDataFrame({"unit_id": ["A"]}, geometry=[box(0, 0, 300, 300)], crs=CRS)
    with pytest.raises(ValueError, match="policy"):
        resolve_slivers(gdf, policy="delete")
