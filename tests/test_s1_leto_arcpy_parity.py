"""Optional behavioral checks against a tiny ArcPy LETO fixture."""

import json
import os
from pathlib import Path
import subprocess
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.segmentation.leto import (
    LetoSegmentationConfig,
    build_leto_management_units,
    build_treemap_domain,
)


ARCGIS_PYTHON = os.environ.get("ARCGIS_PYTHON")
REFERENCE_RUNNER = (
    Path(__file__).resolve().parent / "arcpy_reference" / "leto_segmentation_fixture.py"
)


def _write_raster(path, values, *, nodata=-9999):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype=values.dtype,
        crs="EPSG:5070",
        transform=from_origin(0, 200, 100, 100),
        nodata=nodata,
    ) as destination:
        destination.write(values, 1)


def _run_python_fixture(tmp_path):
    tmp_path.mkdir(parents=True)
    treemap_path = tmp_path / "treemap.tif"
    ownership_path = tmp_path / "ownership.tif"
    _write_raster(treemap_path, np.array([[10, 20], [10, 20]], dtype="int32"))
    _write_raster(
        ownership_path,
        np.array([[4, 3], [3, 4]], dtype="int16"),
    )
    parcels = gpd.GeoDataFrame(geometry=[box(0, 0, 200, 200)], crs="EPSG:5070")
    streams = gpd.GeoDataFrame(
        geometry=[LineString([(0, 100), (200, 100)])], crs="EPSG:5070"
    )
    domain = build_treemap_domain(treemap_path, parcels)
    units, _ = build_leto_management_units(
        treemap_path,
        pd.DataFrame({"VALUE": [10, 20], "PLT_CN": ["plot-10", "plot-20"]}),
        parcels,
        ownership_path,
        streams,
        LetoSegmentationConfig(min_acres=5),
    )
    return {
        "domain_count": len(domain),
        "cleanup_count": len(units),
        "ownership_codes": units["OWN_CODE"].astype(int).tolist(),
        "smz_pct": units["SMZ_Pct"].tolist(),
    }


def _runtime_path(path: Path) -> str:
    if sys.platform != "linux" or not ARCGIS_PYTHON.lower().endswith(".exe"):
        return str(path)
    return subprocess.run(
        ["wslpath", "-w", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.arcpy
@pytest.mark.skipif(not ARCGIS_PYTHON, reason="ARCGIS_PYTHON is not set")
def test_arcpy_reference_stages_preserve_leto_invariants(tmp_path):
    python_result = _run_python_fixture(tmp_path / "python")
    completed = subprocess.run(
        [
            ARCGIS_PYTHON,
            _runtime_path(REFERENCE_RUNNER),
            _runtime_path(tmp_path / "arcpy"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    assert result["domain_count"] == python_result["domain_count"] == 1
    assert result["cleanup_count"] == python_result["cleanup_count"] == 1
    assert result["ownership_codes"] == python_result["ownership_codes"] == [3]
    assert python_result["smz_pct"] == pytest.approx([10.668])
    assert result["smz_pct"] == pytest.approx(python_result["smz_pct"])
