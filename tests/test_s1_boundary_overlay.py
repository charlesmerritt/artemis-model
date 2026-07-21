"""Tests for S1 boundary-overlay management-unit segmentation."""

from pathlib import Path
import sys

import geopandas as gpd
import pytest
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
        },
        geometry=[box(0, 0, 100, 100), box(100, 0, 300, 100)],
        crs="EPSG:5070",
    )


def test_boundary_output_meets_s1_contract(candidate_units):
    result = normalize_output_contract(candidate_units)

    assert {"MU_ID", "Acres", "SEGMENTATION_METHOD", "geometry"} <= set(
        result.columns
    )
    assert result["MU_ID"].tolist() == candidate_units["unit_id"].tolist()
    assert result["Acres"].tolist() == pytest.approx(
        candidate_units["unit_area_ha"] * 2.471053814671653
    )
    assert set(result["SEGMENTATION_METHOD"]) == {SEGMENTATION_METHOD}
