"""Tests for S1 boundary-overlay management-unit segmentation."""

from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.segmentation import boundary_overlay
from pipeline.s1_initial_state.segmentation.boundary_overlay import (
    SEGMENTATION_METHOD,
    normalize_output_contract,
)


@pytest.fixture
def candidate_units():
    return gpd.GeoDataFrame(
        {
            "unit_id": ["mu_12003_00000000", "mu_12003_00000001"],
            "unit_area_ha": [1.0, 2.0],
            "size_class": ["sliver_lt_min", "candidate"],
            "ACRES": [5.0, 10.0],
            "PARCELID": ["parcel-1", "parcel-2"],
        },
        geometry=[box(0, 0, 100, 100), box(100, 0, 300, 100)],
        crs="EPSG:5070",
    )


def test_boundary_output_meets_s1_contract(candidate_units):
    result = normalize_output_contract(candidate_units)

    assert {"MU_ID", "Acres", "SEGMENTATION_METHOD", "geometry"} <= set(result.columns)
    assert result["MU_ID"].tolist() == candidate_units["unit_id"].tolist()
    assert result["Acres"].tolist() == pytest.approx(
        candidate_units["unit_area_ha"] * 2.471053814671653
    )
    assert set(result["SEGMENTATION_METHOD"]) == {SEGMENTATION_METHOD}


def test_boundary_output_with_source_acres_writes_to_geopackage(
    candidate_units, tmp_path
):
    result = normalize_output_contract(candidate_units)

    output_path = tmp_path / "candidate_management_units.gpkg"
    result.to_file(output_path, driver="GPKG")
    written = gpd.read_file(output_path)

    assert "ACRES" not in written.columns
    assert written["Acres"].tolist() == pytest.approx(result["Acres"])
    assert written["PARCELID"].tolist() == candidate_units["PARCELID"].tolist()


def test_boundary_preflight_reports_every_missing_source_with_recovery_guidance(
    tmp_path,
):
    data_root = tmp_path / "missing-production-mount"
    config_path = tmp_path / "missing-bmp-rules.yaml"

    with pytest.raises(FileNotFoundError) as error:
        boundary_overlay.preflight_boundary_overlay_data(data_root, config_path)

    message = str(error.value)
    for source_name in (
        "parcels",
        "roads",
        "boundary_streams",
        "waterbodies",
        "landfire_evt",
        "bmp_rules",
    ):
        assert source_name in message
    assert "mount" in message.lower()
    assert "R2" in message


def test_boundary_preflight_accepts_complete_temp_source_tree(tmp_path):
    data_root = tmp_path / "production"
    config_path = tmp_path / "config" / "bmp_rules.yaml"
    expected_paths = boundary_overlay.BoundaryOverlayDataPaths.from_root(
        data_root, config_path
    )
    for source_name, source_path in expected_paths.__dict__.items():
        if source_name in {"landfire_evt", "bmp_rules"}:
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.touch()
        else:
            source_path.mkdir(parents=True, exist_ok=True)

    result = boundary_overlay.preflight_boundary_overlay_data(data_root, config_path)

    assert result == expected_paths


def test_process_county_preflights_before_dry_run(monkeypatch, tmp_path):
    config_path = tmp_path / "bmp_rules.yaml"
    calls = []

    def record_preflight(data_root, selected_config_path):
        calls.append((data_root, selected_config_path))

    monkeypatch.setattr(
        boundary_overlay,
        "preflight_boundary_overlay_data",
        record_preflight,
    )

    result = boundary_overlay.process_county(
        county_fips="125",
        output_dir=tmp_path / "outputs",
        data_root=tmp_path / "production",
        config_path=config_path,
        dry_run=True,
    )

    assert result is None
    assert calls == [(tmp_path / "production", config_path)]


def test_create_forest_mask_vectorizes_tree_dominated_evt(tmp_path):
    evt_path = tmp_path / "evt.tif"
    with rasterio.open(
        evt_path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="int16",
        crs="EPSG:5070",
        transform=from_origin(0, 60, 30, 30),
    ) as destination:
        destination.write(np.array([[1500, 4000], [4000, 4000]], dtype="int16"), 1)

    result = boundary_overlay.create_forest_mask_from_evt(
        evt_path,
        aoi_bounds=(0, 0, 60, 60),
    )

    assert len(result) == 1
    assert result.crs == "EPSG:5070"
    assert result.geometry.area.iloc[0] == pytest.approx(900.0)
