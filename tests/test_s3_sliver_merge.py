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


def test_nearest_fallback_absorbs_isolated_sliver_across_gap():
    # A sliver separated from the only real unit by a gap shares no boundary, so the
    # shared-boundary stage can't touch it; the nearest-unit fallback must absorb it.
    big = box(0, 0, 300, 300)                # ~22 ac non-sliver
    isolated = box(0, 320, 100, 340)         # 100 x 20 = 0.49 ac, 20 m gap above `big`
    gdf = gpd.GeoDataFrame({"unit_id": ["A", "S"]}, geometry=[big, isolated], crs=CRS)

    # With the fallback (default) the sliver is absorbed into A -> one complete unit.
    merged = merge_slivers_to_neighbors(gdf, min_acres=5.0)
    assert len(merged) == 1
    assert merged["unit_id"].iloc[0] == "A"
    assert not flag_slivers(merged, min_acres=5.0).any()

    # Without the fallback the isolated sliver survives (nothing shares its boundary).
    no_fb = merge_slivers_to_neighbors(gdf, min_acres=5.0, nearest_fallback=False)
    assert len(no_fb) == 2


def test_orphan_sliver_kept_by_default_dropped_when_requested():
    # No non-sliver anywhere, so even the nearest fallback has nothing to attach to.
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


def test_resolve_slivers_defaults_to_leto_drop():
    # Default policy is LETO-style drop: sub-5-acre polygons are eliminated, geometry stays
    # single-part (no multipart merge artefacts).
    big = box(0, 0, 300, 300)        # ~22 ac
    sliver = box(400, 400, 450, 450)  # 0.62 ac, isolated
    gdf = gpd.GeoDataFrame({"unit_id": ["A", "S"]}, geometry=[big, sliver], crs=CRS)
    result = resolve_slivers(gdf)  # no policy -> drop
    assert list(result["unit_id"]) == ["A"]
    assert not flag_slivers(result, min_acres=5.0).any()
    assert (result.geometry.geom_type == "Polygon").all()


def test_merge_output_always_has_area_acres_column():
    # No-sliver input and empty input must return the same schema as the merged path.
    big = gpd.GeoDataFrame({"unit_id": ["A"]}, geometry=[box(0, 0, 300, 300)], crs=CRS)
    assert "area_acres" in merge_slivers_to_neighbors(big, min_acres=5.0).columns
    empty = gpd.GeoDataFrame({"unit_id": []}, geometry=[], crs=CRS)
    assert "area_acres" in merge_slivers_to_neighbors(empty, min_acres=5.0).columns


def test_merge_recomputes_size_class_from_resolved_area():
    from pipeline.s3_management.sketch_management_units import classify_unit_size
    # Big unit (~25 ac / 10 ha, "candidate") carrying a STALE label, plus a sliver to merge.
    big = box(0, 0, 320, 320)          # 10.24 ha -> candidate
    sliver = box(320, 0, 340, 320)     # 0.64 ha -> sliver
    gdf = gpd.GeoDataFrame(
        {"unit_id": ["A", "S"], "unit_area_ha": [10.24, 0.64],
         "size_class": ["sliver_lt_min", "sliver_lt_min"]},  # deliberately wrong on A
        geometry=[big, sliver], crs=CRS,
    )
    out = resolve_slivers(gdf, policy="merge")
    # Every emitted size_class matches its recomputed area class (no stale labels).
    for _, r in out.iterrows():
        assert r["size_class"] == classify_unit_size(r["unit_area_ha"])
    assert set(out["size_class"]) == {"candidate"}


def test_merge_resolves_sliver_cluster_through_valid_anchor():
    # Reviewer scenario: two slivers share a LONG internal edge but only a SHORT edge with
    # the valid stand, so each sliver's longest neighbour is the other sliver. Iteration must
    # still land the whole cluster on the stand -> no sub-5-acre unit survives.
    v = box(0, 0, 300, 300)        # 22 ac valid stand
    s1 = box(300, 0, 700, 20)      # 1.98 ac; 20 m contact with v, 400 m with s2
    s2 = box(300, 20, 700, 40)     # 1.98 ac; 20 m contact with v, 400 m with s1
    gdf = gpd.GeoDataFrame({"unit_id": ["V", "S1", "S2"]}, geometry=[v, s1, s2], crs=CRS)
    assert not flag_slivers(resolve_slivers(gdf, policy="merge"), min_acres=5.0).any()
    # Even with the nearest fallback off, iterated shared-boundary passes resolve it.
    boundary_only = merge_slivers_to_neighbors(gdf, min_acres=5.0, nearest_fallback=False)
    assert not flag_slivers(boundary_only, min_acres=5.0).any()


def test_resolve_slivers_rejects_unknown_policy():
    gdf = gpd.GeoDataFrame({"unit_id": ["A"]}, geometry=[box(0, 0, 300, 300)], crs=CRS)
    with pytest.raises(ValueError, match="policy"):
        resolve_slivers(gdf, policy="delete")
