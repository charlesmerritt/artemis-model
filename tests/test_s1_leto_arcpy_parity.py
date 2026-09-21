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
from shapely import wkt
from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.segmentation.leto import (
    LetoSegmentationConfig,
    build_leto_management_units,
    build_treemap_domain,
    cleanup_and_clip_units,
    subdivide_large_units,
)


ARCGIS_PYTHON = os.environ.get("ARCGIS_PYTHON")
REFERENCE_RUNNER = (
    Path(__file__).resolve().parent / "arcpy_reference" / "leto_segmentation_fixture.py"
)


SQUARE_METERS_PER_ACRE = 4_046.872609874251


def _write_raster(path, values, *, nodata=-9999, cell_size=100):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype=values.dtype,
        crs="EPSG:5070",
        transform=from_origin(0, values.shape[0] * cell_size, cell_size, cell_size),
        nodata=nodata,
    ) as destination:
        destination.write(values, 1)


def _run_python_fixture(tmp_path):
    tmp_path.mkdir(parents=True)
    treemap_path = tmp_path / "treemap.tif"
    ownership_path = tmp_path / "ownership.tif"
    treemap_values = np.tile([10] * 6 + [20] * 6, (9, 1)).astype("int32")
    _write_raster(treemap_path, treemap_values)
    _write_raster(
        ownership_path,
        np.tile([3] * 6 + [4] * 6, (9, 1)).astype("int16"),
    )
    parcels = gpd.GeoDataFrame(geometry=[box(0, 0, 1_200, 900)], crs="EPSG:5070")
    streams = gpd.GeoDataFrame(
        geometry=[LineString([(0, 450), (1_200, 450)])], crs="EPSG:5070"
    )
    domain = build_treemap_domain(treemap_path, parcels)
    config = LetoSegmentationConfig(min_acres=5)
    subdivided = subdivide_large_units(domain, config)
    cleaned = cleanup_and_clip_units(subdivided, parcels, config.min_acres)
    units, weights = build_leto_management_units(
        treemap_path,
        pd.DataFrame({"VALUE": [10, 20], "PLT_CN": ["plot-10", "plot-20"]}),
        parcels,
        ownership_path,
        streams,
        config,
    )
    coverage = subdivided.geometry.union_all()
    weight_sums = weights.groupby("MU_ID")["WEIGHT"].sum().tolist()
    modal_in_donors = [
        row.PLT_CN in set(weights.loc[weights["MU_ID"] == row.MU_ID, "PLT_CN"])
        for row in units[["MU_ID", "PLT_CN"]].itertuples(index=False)
    ]
    return {
        "domain_count": len(domain),
        "parent_acres": domain.geometry.area.iloc[0] / SQUARE_METERS_PER_ACRE,
        "parent_wkt": domain.geometry.iloc[0].wkt,
        "pre_cleanup_coverage_ratio": coverage.area / domain.geometry.area.iloc[0],
        "pre_cleanup_overlap_acres": (subdivided.geometry.area.sum() - coverage.area)
        / SQUARE_METERS_PER_ACRE,
        "pre_cleanup_children_valid": subdivided.geometry.is_valid.all(),
        "cleanup_count": len(units),
        "cleanup_acres": sorted(units["Acres"].tolist()),
        "sliver_count": int((cleaned["Acres"] < 5).sum()),
        "oversized_count": int((cleaned["Acres"] > 200).sum()),
        "weight_sums": weight_sums,
        "modal_in_donors": modal_in_donors,
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
    assert result["parent_acres"] > 200
    assert python_result["parent_acres"] > 200
    arcpy_parent = wkt.loads(result["parent_wkt"])
    python_parent = wkt.loads(python_result["parent_wkt"])
    assert arcpy_parent.intersection(python_parent).area / arcpy_parent.union(
        python_parent
    ).area == pytest.approx(1.0, abs=1e-9)
    assert result["parent_acres"] == pytest.approx(
        python_result["parent_acres"], rel=1e-9
    )
    for method_result in (result, python_result):
        assert method_result["pre_cleanup_coverage_ratio"] == pytest.approx(
            1.0, abs=1e-6
        )
        assert method_result["pre_cleanup_overlap_acres"] == pytest.approx(
            0.0, abs=1e-6
        )
        assert bool(method_result["pre_cleanup_children_valid"])
        assert method_result["cleanup_count"] >= 2
        assert method_result["sliver_count"] == 0
        assert method_result["oversized_count"] == 0
        assert min(method_result["cleanup_acres"]) >= 5
        assert max(method_result["cleanup_acres"]) <= 200
        assert sum(method_result["cleanup_acres"]) == pytest.approx(
            method_result["parent_acres"], rel=1e-6
        )
        assert method_result["weight_sums"] == pytest.approx(
            [1.0] * method_result["cleanup_count"]
        )
        assert all(method_result["modal_in_donors"])
