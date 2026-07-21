"""Tests for S1 boundary-overlay management-unit segmentation."""

from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.segmentation import boundary_overlay
from pipeline.s1_initial_state.segmentation.artifacts import (
    load_comparable_artifacts,
    write_segmentation_artifact,
)
from pipeline.s1_initial_state.segmentation.boundary_overlay import (
    SEGMENTATION_METHOD,
    normalize_output_contract,
)
from pipeline.s1_initial_state.segmentation.leto import attribute_management_units


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


def test_boundary_output_is_fully_attributed_before_canonical_artifact_write(
    candidate_units, tmp_path
):
    treemap_path = tmp_path / "treemap.tif"
    ownership_path = tmp_path / "ownership.tif"
    for path, values, nodata in (
        (treemap_path, np.array([[10, 20, 20], [10, 20, 20]], dtype="int16"), -9999),
        (ownership_path, np.array([[3, 4, 4], [3, 4, 4]], dtype="uint8"), 255),
    ):
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=2,
            width=3,
            count=1,
            dtype=values.dtype,
            crs="EPSG:5070",
            transform=from_origin(0, 200, 100, 100),
            nodata=nodata,
        ) as destination:
            destination.write(values, 1)
    streams = gpd.GeoDataFrame(geometry=[], crs="EPSG:5070")
    units, weights = attribute_management_units(
        normalize_output_contract(candidate_units),
        treemap_path,
        pd.DataFrame({"VALUE": [10, 20], "PLT_CN": ["plot-10", "plot-20"]}),
        ownership_path,
        streams,
        smz_buffer_feet=35,
    )
    shared_sources = {
        "treemap": treemap_path,
        "fiadb": treemap_path,
        "ownership": ownership_path,
        "species": treemap_path,
    }
    artifact = tmp_path / "boundary" / "ManagementUnits.gpkg"

    write_segmentation_artifact(
        units,
        artifact,
        strategy="boundary_overlay",
        aoi_id="fixture",
        experiment_id="integration",
        seed=0,
        strategy_parameters={"split_large": True},
        code_version="test",
        shared_sources=shared_sources,
        strategy_sources={"parcels": treemap_path},
    )
    written, _ = load_comparable_artifacts(artifact, artifact, allow_same=True)[0]

    assert set(units["MU_ID"]) == set(weights["MU_ID"])
    assert units["PLT_CN"].tolist() == ["plot-10", "plot-20"]
    assert units["TM_VALUE"].tolist() == [10, 20]
    assert units["OWN_CODE"].tolist() == [3, 4]
    assert units["SMZ_Pct"].tolist() == [0.0, 0.0]
    assert set(written.columns) >= {
        "MU_ID",
        "Acres",
        "SEGMENTATION_METHOD",
        "PLT_CN",
        "TM_VALUE",
        "OWN_CODE",
        "OWN_TYPE",
        "SMZ_Pct",
        "geometry",
    }


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


def test_cli_processes_remaining_counties_but_exits_nonzero_after_failure(
    monkeypatch, tmp_path
):
    calls = []

    def fail_one_county(*, county_fips, **kwargs):
        calls.append(county_fips)
        if county_fips == "023":
            raise FileNotFoundError("production preflight failed")
        return [{"size_class": "candidate"}]

    monkeypatch.setattr(boundary_overlay, "process_county", fail_one_county)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "boundary-overlay",
            "--pilot-five-county",
            "--output-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as error:
        boundary_overlay.main()

    assert error.value.code == 1
    assert calls == boundary_overlay.PILOT_COUNTIES


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
