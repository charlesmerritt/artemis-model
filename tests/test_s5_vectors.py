"""Tests for the shared vector helpers (pipeline/s5_imagery/vectors.py)."""

import json
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s5_imagery import vectors

# North Florida, in the five-county pilot area. Roughly 1 km on a side.
AOI_BOX = box(-82.60, 30.10, -82.59, 30.11)


def _write_layer(tmp_path, geometry, crs="EPSG:4326", name="aoi.geojson"):
    path = tmp_path / name
    gpd.GeoDataFrame({"name": ["test"]}, geometry=[geometry], crs=crs).to_file(path)
    return path


# ---- slugify ----


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Union County Stands", "union_county_stands"),
        ("  Mixed-Case/Slashes  ", "mixed_case_slashes"),
        ("2024 NAIP", "2024_naip"),
        ("!!!", "aoi"),  # never empty: Earth Engine rejects empty task descriptions
        ("", "aoi"),
    ],
)
def test_slugify(text, expected):
    assert vectors.slugify(text) == expected


# ---- load_layer ----


def test_load_layer_reprojects_to_wgs84(tmp_path):
    projected = gpd.GeoSeries([AOI_BOX], crs="EPSG:4326").to_crs("EPSG:5070").iloc[0]
    path = _write_layer(tmp_path, projected, crs="EPSG:5070")

    gdf = vectors.load_layer(path)

    assert gdf.crs.to_string() == vectors.WGS84
    minx, miny, maxx, maxy = gdf.total_bounds
    assert -82.61 < minx < -82.59
    assert 30.09 < miny < 30.11


def test_load_layer_rejects_missing_crs(tmp_path):
    # GeoJSON always reads back as WGS84, so the guard is exercised through a
    # format that preserves the absence of a CRS.
    path = tmp_path / "nocrs.gpkg"
    gdf = gpd.GeoDataFrame({"name": ["x"]}, geometry=[AOI_BOX], crs=None)
    gdf.to_file(path, driver="GPKG", layer="layer1")

    loaded = gpd.read_file(path)
    if loaded.crs is not None:
        pytest.skip("GDAL assigned a CRS on write; the no-CRS path cannot be exercised here")
    with pytest.raises(ValueError, match="no CRS"):
        vectors.load_layer(path)


def test_load_layer_missing_file():
    with pytest.raises(FileNotFoundError):
        vectors.load_layer("does/not/exist.geojson")


# ---- layer_geometry ----


def test_layer_geometry_dissolves_touching_parts():
    gdf = gpd.GeoDataFrame(
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:4326",
    )
    dissolved = vectors.layer_geometry(gdf)
    assert dissolved.geom_type == "Polygon"
    assert dissolved.bounds == (0.0, 0.0, 2.0, 1.0)


def test_layer_geometry_repairs_self_intersection():
    # Bowtie: invalid as digitized, and unary_union propagates that without repair.
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    assert not bowtie.is_valid

    dissolved = vectors.layer_geometry(gpd.GeoDataFrame(geometry=[bowtie], crs="EPSG:4326"))
    assert dissolved.is_valid


def test_layer_geometry_rejects_empty():
    gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    with pytest.raises(ValueError):
        vectors.layer_geometry(gdf)


# ---- derive_extent ----


def test_derive_extent_bbox_contains_aoi():
    extent = vectors.derive_extent(AOI_BOX, "bbox")
    assert vectors.containment_fraction(AOI_BOX, extent) == pytest.approx(1.0, abs=1e-6)


def test_derive_extent_buffer_is_larger_than_aoi():
    extent = vectors.derive_extent(AOI_BOX, "buffer", buffer_m=500)
    assert vectors.area_ha(extent) > vectors.area_ha(AOI_BOX)
    assert vectors.containment_fraction(AOI_BOX, extent) == pytest.approx(1.0, abs=1e-6)


def test_derive_extent_hull_of_concave_aoi_is_bigger():
    concave = Polygon([(0, 0), (2, 0), (2, 2), (1, 1), (0, 2)])
    hull = vectors.derive_extent(concave, "hull")
    assert vectors.area_ha(hull) > vectors.area_ha(concave)


def test_derive_extent_buffer_requires_positive_buffer():
    # A zero buffer would make extent == AOI, leaving no outside pixels to compare.
    with pytest.raises(ValueError, match="buffer-m"):
        vectors.derive_extent(AOI_BOX, "buffer", buffer_m=0)


def test_derive_extent_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Unknown extent mode"):
        vectors.derive_extent(AOI_BOX, "voronoi")


def test_derive_extent_rejects_negative_buffer():
    with pytest.raises(ValueError):
        vectors.derive_extent(AOI_BOX, "bbox", buffer_m=-10)


# ---- measurements ----


def test_area_ha_is_plausible_for_a_known_box():
    # 0.01 deg square near 30N: ~0.96 km x ~1.11 km.
    assert 90 < vectors.area_ha(AOI_BOX) < 130


def test_containment_fraction_partial_overlap():
    inner = box(0, 0, 2, 1)
    outer = box(0, 0, 1, 1)
    assert vectors.containment_fraction(inner, outer) == pytest.approx(0.5, abs=0.02)


def test_containment_fraction_disjoint():
    assert vectors.containment_fraction(box(0, 0, 1, 1), box(5, 5, 6, 6)) == 0.0


def test_bounds_list_order():
    assert vectors.bounds_list(box(-3, -2, 4, 5)) == [-3.0, -2.0, 4.0, 5.0]


def test_estimate_pixel_count_scales_quadratically():
    at_one_m = vectors.estimate_pixel_count(AOI_BOX, 1.0)
    at_two_m = vectors.estimate_pixel_count(AOI_BOX, 2.0)
    assert at_one_m == pytest.approx(at_two_m * 4, rel=0.01)
    # ~1 km box at 1 m is on the order of a million pixels.
    assert 500_000 < at_one_m < 2_000_000


def test_estimate_pixel_count_rejects_zero_scale():
    with pytest.raises(ValueError):
        vectors.estimate_pixel_count(AOI_BOX, 0)


# ---- GeoJSON ----


def test_feature_collection_round_trips_as_json():
    collection = vectors.feature_collection(AOI_BOX, {"role": "area_of_interest"})
    parsed = json.loads(json.dumps(collection))

    assert parsed["type"] == "FeatureCollection"
    assert len(parsed["features"]) == 1
    assert parsed["features"][0]["properties"]["role"] == "area_of_interest"
    assert parsed["features"][0]["geometry"]["type"] == "Polygon"
