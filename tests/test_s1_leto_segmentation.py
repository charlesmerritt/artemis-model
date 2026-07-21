"""Tests for LETO's pure geometry subdivision primitives."""

from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.segmentation.leto import (
    LetoSegmentationConfig,
    SegmentationError,
    _polygon_parts,
    assign_majority_ownership,
    assign_smz_percent,
    build_leto_management_units,
    build_treemap_domain,
    calculate_acres,
    cleanup_and_clip_units,
    sample_constrained_points,
    split_unit_thiessen,
    subdivide_large_units,
)

INTERNATIONAL_SQUARE_METERS_PER_ACRE = 4_046.8564224
US_SURVEY_SQUARE_METERS_PER_ACRE = 4_046.872609874251


def _write_raster(path, values, *, nodata=-9999, crs="EPSG:5070", cell_size=100):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype=values.dtype,
        crs=crs,
        transform=from_origin(0, values.shape[0] * cell_size, cell_size, cell_size),
        nodata=nodata,
    ) as destination:
        destination.write(values, 1)


def test_calculate_acres_uses_projected_crs_units_without_mutating_input():
    one_acre_side = np.sqrt(US_SURVEY_SQUARE_METERS_PER_ACRE)
    units = gpd.GeoDataFrame(
        {"name": ["one-acre"]},
        geometry=[box(0, 0, one_acre_side, one_acre_side)],
        crs="EPSG:5070",
    )

    result = calculate_acres(units)

    assert "Acres" not in units.columns
    assert result.loc[0, "Acres"] == pytest.approx(1.0)
    assert result.loc[0, "name"] == "one-acre"


@pytest.mark.parametrize(
    ("geometry", "record_id"),
    [(None, "null-unit"), (Polygon(), "empty-unit")],
)
def test_calculate_acres_rejects_null_and_empty_geometry(geometry, record_id):
    units = gpd.GeoDataFrame(
        geometry=[geometry],
        index=[record_id],
        crs="EPSG:5070",
    )

    with pytest.raises(SegmentationError, match=rf"record '{record_id}'"):
        calculate_acres(units)


def test_calculate_acres_rejects_non_polygon_geometry():
    units = gpd.GeoDataFrame(
        geometry=[Point(0, 0)],
        index=["point-unit"],
        crs="EPSG:5070",
    )

    with pytest.raises(SegmentationError, match="record 'point-unit'"):
        calculate_acres(units)


def test_calculate_acres_rejects_invalid_polygon_topology():
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    units = gpd.GeoDataFrame(
        geometry=[bowtie],
        index=["invalid-unit"],
        crs="EPSG:5070",
    )

    with pytest.raises(SegmentationError, match="record 'invalid-unit'"):
        calculate_acres(units)


@pytest.mark.parametrize(
    ("geometry", "record_id"),
    [
        (box(0, 0, 1e-200, 1e-200), "zero-area-unit"),
        (box(0, 0, 1e200, 1e200), "infinite-area-unit"),
    ],
)
def test_calculate_acres_rejects_non_positive_and_non_finite_area(geometry, record_id):
    units = gpd.GeoDataFrame(
        geometry=[geometry],
        index=[record_id],
        crs="EPSG:5070",
    )

    with pytest.raises(SegmentationError, match=rf"record '{record_id}'"):
        calculate_acres(units)


def test_calculate_acres_accepts_multipolygon_geometry():
    units = gpd.GeoDataFrame(
        geometry=[MultiPolygon([box(0, 0, 10, 10), box(20, 0, 30, 10)])],
        crs="EPSG:5070",
    )

    assert calculate_acres(units).loc[0, "Acres"] > 0


def test_cleanup_uses_us_survey_acres_at_exact_five_acre_threshold():
    international_five_acres = box(0, 0, 1, 5 * INTERNATIONAL_SQUARE_METERS_PER_ACRE)
    us_survey_five_acres = box(10, 0, 11, 5 * US_SURVEY_SQUARE_METERS_PER_ACRE)
    parcels = gpd.GeoDataFrame(
        geometry=[box(-1, -1, 12, 5 * US_SURVEY_SQUARE_METERS_PER_ACRE + 1)],
        crs="EPSG:5070",
    )
    international_units = gpd.GeoDataFrame(
        geometry=[international_five_acres], crs=parcels.crs
    )
    us_survey_units = gpd.GeoDataFrame(geometry=[us_survey_five_acres], crs=parcels.crs)

    international_result = cleanup_and_clip_units(
        international_units,
        parcels,
        min_acres=5,
    )
    us_survey_result = cleanup_and_clip_units(
        us_survey_units,
        parcels,
        min_acres=5,
    )

    assert calculate_acres(international_units).loc[0, "Acres"] < 5
    assert international_result.empty
    assert calculate_acres(us_survey_units).loc[0, "Acres"] == pytest.approx(5.0)
    assert len(us_survey_result) == 1
    assert us_survey_result.loc[0, "Acres"] == pytest.approx(5.0)


def test_sample_constrained_points_stays_inside_and_respects_separation():
    geometry = box(0, 0, 100, 100)

    points = sample_constrained_points(
        geometry,
        count=4,
        min_distance=20,
        rng=np.random.default_rng(7),
    )

    assert len(points) == 4
    assert all(geometry.contains(point) for point in points)
    distances = [
        first.distance(second)
        for index, first in enumerate(points)
        for second in points[index + 1 :]
    ]
    assert min(distances) >= 20


def test_sample_constrained_points_returns_empty_list_for_zero_count():
    points = sample_constrained_points(
        box(0, 0, 1, 1),
        count=0,
        min_distance=10,
        rng=np.random.default_rng(2),
    )

    assert points == []


def test_constrained_points_fail_instead_of_looping_forever():
    with pytest.raises(SegmentationError, match="minimum separation"):
        sample_constrained_points(
            box(0, 0, 1, 1),
            count=3,
            min_distance=10,
            rng=np.random.default_rng(1),
        )


def test_split_unit_thiessen_returns_polygonal_coverage():
    parent = MultiPolygon([box(0, 0, 100, 100), box(200, 0, 300, 100)])

    children = split_unit_thiessen(
        parent,
        point_count=4,
        min_distance=10,
        rng=np.random.default_rng(8),
    )

    assert len(children) >= 2
    assert all(child.geom_type == "Polygon" and child.area > 0 for child in children)
    assert unary_union(children).symmetric_difference(parent).area == pytest.approx(0)


def test_polygon_parts_ignore_non_polygon_collection_members():
    polygon = box(0, 0, 1, 1)
    geometry = GeometryCollection([polygon, LineString([(0, 0), (1, 1)]), Point(0, 0)])

    assert _polygon_parts(geometry) == [polygon]


def test_subdivide_large_units_keeps_units_at_threshold():
    side = np.sqrt(200 * US_SURVEY_SQUARE_METERS_PER_ACRE)
    units = gpd.GeoDataFrame(
        {"source": ["threshold"]},
        geometry=[box(0, 0, side, side)],
        crs="EPSG:5070",
    )

    result = subdivide_large_units(
        units,
        LetoSegmentationConfig(max_acres=200, min_distance_feet=100),
    )

    assert len(result) == 1
    assert result.loc[0, "source"] == "threshold"
    assert result.geometry.iloc[0].equals_exact(units.geometry.iloc[0], tolerance=0)
    assert result.loc[0, "Acres"] == pytest.approx(200)
    assert result.loc[0, "Acres"] <= 200


def test_subdivide_large_units_is_repeatable_and_preserves_coverage():
    units = gpd.GeoDataFrame(
        {"source": ["large"]},
        geometry=[box(0, 0, 1_200, 1_200)],
        crs="EPSG:5070",
    )
    config = LetoSegmentationConfig(
        max_acres=200,
        acres_per_point=100,
        min_distance_feet=100,
        seed=42,
    )

    first = subdivide_large_units(units, config)
    second = subdivide_large_units(units, config)

    assert first.geometry.to_wkb().tolist() == second.geometry.to_wkb().tolist()
    assert first.geometry.union_all().symmetric_difference(
        units.geometry.iloc[0]
    ).area == pytest.approx(0)
    assert first["Acres"].max() <= 200
    assert set(first["source"]) == {"large"}


def test_build_treemap_domain_reads_aoi_window_and_clips_valid_cells(
    tmp_path, monkeypatch
):
    treemap_path = tmp_path / "treemap.tif"
    _write_raster(
        treemap_path,
        np.array(
            [
                [10, 10, -9999, -9999],
                [10, 10, -9999, -9999],
                [-9999, -9999, -9999, -9999],
                [-9999, -9999, -9999, -9999],
            ],
            dtype="int16",
        ),
    )
    parcels = gpd.GeoDataFrame(
        geometry=[box(50, 250, 150, 350)],
        crs="EPSG:5070",
    )
    original_open = rasterio.open
    windows = []

    class TrackedDataset:
        def __init__(self, dataset):
            self._dataset = dataset

        def __enter__(self):
            self._dataset.__enter__()
            return self

        def __exit__(self, *args):
            return self._dataset.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self._dataset, name)

        def read(self, *args, **kwargs):
            windows.append(kwargs.get("window"))
            return self._dataset.read(*args, **kwargs)

    def tracked_open(*args, **kwargs):
        return TrackedDataset(original_open(*args, **kwargs))

    monkeypatch.setattr(
        "pipeline.s1_initial_state.segmentation.leto.rasterio.open", tracked_open
    )

    result = build_treemap_domain(treemap_path, parcels)

    assert len(result) == 1
    assert result.crs == parcels.crs
    assert result.geometry.iloc[0].equals(box(50, 250, 150, 350))
    assert len(windows) == 1
    assert windows[0] is not None
    assert windows[0].width < 4
    assert windows[0].height < 4


def test_cleanup_matches_leto_singlepart_minimum_and_parcel_clip():
    large_piece = box(0, 0, 200, 200)
    small_piece = box(300, 0, 310, 310)
    parcels = gpd.GeoDataFrame(geometry=[box(0, 0, 500, 500)], crs="EPSG:5070")
    units = gpd.GeoDataFrame(
        geometry=[MultiPolygon([large_piece, small_piece])], crs="EPSG:5070"
    )

    result = cleanup_and_clip_units(units, parcels, min_acres=5)

    assert len(result) == 1
    assert result.iloc[0].geometry.within(parcels.geometry.union_all())
    assert result.iloc[0].geometry.equals(large_piece)


def test_cleanup_applies_minimum_before_final_parcel_clip():
    units = gpd.GeoDataFrame(
        geometry=[box(0, 0, 200, 200)],
        crs="EPSG:5070",
    )
    parcels = gpd.GeoDataFrame(
        geometry=[box(0, 0, 10, 10)],
        crs="EPSG:5070",
    )

    result = cleanup_and_clip_units(units, parcels, min_acres=5)

    assert len(result) == 1
    assert result.loc[0, "Acres"] < 5


def test_assign_majority_ownership_uses_pixel_centers_and_low_code_tie_break(
    tmp_path,
):
    ownership_path = tmp_path / "ownership.tif"
    _write_raster(
        ownership_path,
        np.array([[4, 3], [3, 4]], dtype="uint8"),
        nodata=255,
        cell_size=50,
    )
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"]},
        geometry=[box(0, 0, 100, 100), box(0, 0, 49, 49)],
        crs="EPSG:5070",
    )

    result = assign_majority_ownership(units, ownership_path)

    assert result["OWN_CODE"].tolist() == [3, 3]
    assert result["OWN_TYPE"].tolist() == ["Family Forest", "Family Forest"]


def test_assign_majority_ownership_leaves_units_without_valid_cells_null(tmp_path):
    ownership_path = tmp_path / "ownership.tif"
    _write_raster(
        ownership_path,
        np.array([[255]], dtype="uint8"),
        nodata=255,
        cell_size=50,
    )
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1", "2"]},
        geometry=[box(0, 0, 50, 50), box(100, 100, 150, 150)],
        crs="EPSG:5070",
    )

    result = assign_majority_ownership(units, ownership_path)

    assert result["OWN_CODE"].isna().all()
    assert result["OWN_TYPE"].isna().all()


def test_assign_smz_percent_matches_legacy_intersection_formula():
    units = gpd.GeoDataFrame(
        {"MU_ID": ["1"]}, geometry=[box(0, 0, 100, 100)], crs="EPSG:5070"
    )
    streams = gpd.GeoDataFrame(
        geometry=[LineString([(0, 50), (100, 50)])], crs=units.crs
    )

    result = assign_smz_percent(units, streams, buffer_feet=10 / 0.3048)

    assert result.loc[0, "SMZ_Pct"] == pytest.approx(20.0)
    assert result.loc[0, "SMZ_Acres"] == pytest.approx(
        2_000 / US_SURVEY_SQUARE_METERS_PER_ACRE
    )


def test_build_leto_management_units_preserves_stage_order_and_modal_ties(
    tmp_path, monkeypatch
):
    treemap_path = tmp_path / "treemap.tif"
    ownership_path = tmp_path / "ownership.tif"
    _write_raster(
        treemap_path,
        np.array([[10, 20], [10, 20]], dtype="int16"),
        cell_size=100,
    )
    _write_raster(
        ownership_path,
        np.array([[4, 3], [3, 4]], dtype="uint8"),
        nodata=255,
        cell_size=100,
    )
    parcels = gpd.GeoDataFrame(geometry=[box(0, 0, 200, 200)], crs="EPSG:5070")
    streams = gpd.GeoDataFrame(
        geometry=[LineString([(0, 100), (200, 100)])], crs="EPSG:5070"
    )
    lookup = pd.DataFrame({"VALUE": [10, 20], "PLT_CN": ["plot-10", "plot-20"]})
    stages = []

    def record_stage(name):
        original = getattr(
            sys.modules["pipeline.s1_initial_state.segmentation.leto"], name
        )

        def wrapped(*args, **kwargs):
            stages.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(
            f"pipeline.s1_initial_state.segmentation.leto.{name}", wrapped
        )

    for name in (
        "build_treemap_domain",
        "subdivide_large_units",
        "cleanup_and_clip_units",
        "build_plot_weights",
        "assign_majority_ownership",
        "assign_smz_percent",
    ):
        record_stage(name)

    units, weights = build_leto_management_units(
        treemap_path,
        lookup,
        parcels,
        ownership_path,
        streams,
        LetoSegmentationConfig(
            max_acres=200,
            min_acres=5,
        ),
    )

    assert stages == [
        "build_treemap_domain",
        "subdivide_large_units",
        "cleanup_and_clip_units",
        "build_plot_weights",
        "assign_majority_ownership",
        "assign_smz_percent",
    ]
    assert units.loc[0, "MU_ID"] == "1"
    assert units.loc[0, "SEGMENTATION_METHOD"] == "leto"
    assert units.loc[0, "TM_VALUE"] == 10
    assert units.loc[0, "PLT_CN"] == "plot-10"
    assert units.loc[0, "OWN_CODE"] == 3
    assert units.loc[0, "OWN_TYPE"] == "Family Forest"
    assert units.loc[0, "SMZ_Pct"] == pytest.approx(10.668)
    assert weights["TM_VALUE"].tolist() == [10, 20]
