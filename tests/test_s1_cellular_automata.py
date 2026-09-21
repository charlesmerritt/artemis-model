"""Tests for the production LETO cellular-automata segmentation adapter."""

import sqlite3
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.segmentation import cellular_automata
from pipeline.s1_initial_state.segmentation.cellular_automata import (
    CellularAutomataSegmentationConfig,
    build_cellular_automata_management_units,
)
from pipeline.s1_initial_state.segmentation.leto import SegmentationError

CELL_M = 30
GRID = 20  # 20 x 20 cells = 600 m x 600 m


def _write_treemap(path):
    values = np.where(
        np.tile(np.arange(GRID), (GRID, 1)) < GRID // 2, 10, 20
    ).astype("int32")
    with rasterio.open(
        path, "w", driver="GTiff", height=GRID, width=GRID, count=1,
        dtype="int32", crs="EPSG:5070",
        transform=from_origin(0, GRID * CELL_M, CELL_M, CELL_M),
        nodata=-9999,
    ) as dst:
        dst.write(values, 1)


def _write_ownership(path):
    # Deliberately a coarser grid than TreeMap (60 m vs. 30 m) to exercise the
    # warp, but grid-aligned (shared origin, an exact resolution multiple) so
    # the ownership split falls exactly on a TreeMap cell boundary rather than
    # splitting a TreeMap cell -- that ambiguity is a real property of
    # resampling a categorical raster, not something this fixture is testing.
    coarse_cell = 60
    coarse_n = GRID * CELL_M // coarse_cell
    values = np.where(
        np.tile(np.arange(coarse_n), (coarse_n, 1)) < coarse_n // 2, 3, 4
    ).astype("int16")
    with rasterio.open(
        path, "w", driver="GTiff", height=coarse_n, width=coarse_n, count=1,
        dtype="int16", crs="EPSG:5070",
        transform=from_origin(0, GRID * CELL_M, coarse_cell, coarse_cell),
        nodata=255,
    ) as dst:
        dst.write(values, 1)


def _write_fiadb(path, ages):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "create table COND (PLT_CN text, CONDID integer, STDAGE real, "
            "CONDPROP_UNADJ real, COND_STATUS_CD integer)"
        )
        connection.executemany(
            "insert into COND values (?, ?, ?, ?, ?)",
            [(plt_cn, 1, age, 1.0, 1) for plt_cn, age in ages.items()],
        )


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    treemap_path = tmp_path / "treemap.tif"
    ownership_path = tmp_path / "ownership.tif"
    fiadb_path = tmp_path / "fia.db"
    _write_treemap(treemap_path)
    _write_ownership(ownership_path)
    _write_fiadb(fiadb_path, {"plot-10": 15.0, "plot-20": 80.0})

    vat = pd.DataFrame({
        "VALUE": [10, 20],
        "PLT_CN": ["plot-10", "plot-20"],
        "FORTYPCD": [161, 401],
        "BALIVE": [90.0, 140.0],
        "QMD": [6.0, 11.0],
        "TPA": [300.0, 120.0],
    })
    monkeypatch.setattr(cellular_automata, "load_treemap_attributes", lambda path: vat)

    parcels = gpd.GeoDataFrame(
        geometry=[box(0, 0, GRID * CELL_M, GRID * CELL_M)], crs="EPSG:5070"
    )
    streams = gpd.GeoDataFrame(
        geometry=[LineString([(0, GRID * CELL_M / 2), (GRID * CELL_M, GRID * CELL_M / 2)])],
        crs="EPSG:5070",
    )
    config = CellularAutomataSegmentationConfig(
        # Wide enough that a full row of 30 m TreeMap cell centers (15 m from
        # the stream at y=300) falls inside the buffer -- rasterize tests
        # cell centers, so a narrower buffer would catch no cells at all.
        smz_buffer_feet=60.0,
        overrides={
            "initial_seed_acres": 2.0,
            "minimum_stand_acres": 0.5,
            "maximum_stand_acres": 5.0,
            "maximum_iterations": 20,
        },
    )
    return {
        "treemap_path": treemap_path,
        "treemap_vat_path": tmp_path / "unused.dbf",
        "fiadb_path": fiadb_path,
        "parcels": parcels,
        "ownership_path": ownership_path,
        "streams": streams,
        "config": config,
    }


def _run(scenario):
    return build_cellular_automata_management_units(
        scenario["treemap_path"],
        scenario["treemap_vat_path"],
        scenario["fiadb_path"],
        scenario["parcels"],
        scenario["ownership_path"],
        scenario["streams"],
        scenario["config"],
    )


def test_produces_the_shared_canonical_output_contract(scenario):
    units, weights = _run(scenario)

    for column in ("MU_ID", "Acres", "SEGMENTATION_METHOD", "PLT_CN", "TM_VALUE",
                   "OWN_CODE", "OWN_TYPE", "SMZ_Pct", "geometry"):
        assert column in units.columns
    assert set(units["SEGMENTATION_METHOD"]) == {"cellular_automata"}
    assert units["MU_ID"].is_unique
    assert set(weights["MU_ID"]) <= set(units["MU_ID"])


def test_never_crosses_the_ownership_hard_boundary(scenario):
    units, _weights = _run(scenario)

    assert set(units["OWN_CODE"].dropna().astype(int)) == {3, 4}
    # Every unit's centroid x falls on the same side of the ownership split
    # as its assigned owner code (west = 3, east = 4).
    midpoint = GRID * CELL_M / 2
    west = units[units["OWN_CODE"] == 3]
    east = units[units["OWN_CODE"] == 4]
    assert (west.geometry.centroid.x < midpoint + 1e-6).all()
    assert (east.geometry.centroid.x > midpoint - 1e-6).all()


def test_splits_riparian_units_along_the_stream(scenario):
    units, _weights = _run(scenario)

    riparian = units[units["SMZ_Pct"] > 50]
    upland = units[units["SMZ_Pct"] < 50]
    assert len(riparian) > 0
    assert len(upland) > 0


def test_multiple_parent_stands_from_the_small_maximum_stand_acres(scenario):
    units, _weights = _run(scenario)

    # 600 m x 600 m at ~0.222 ac/cell is ~89 ac total; a 5-acre cap must force
    # more than the couple of units a single parent stand would produce.
    assert len(units) > 4


def test_raises_when_parcels_overlap_no_treemap_cells(scenario):
    scenario["parcels"] = gpd.GeoDataFrame(
        geometry=[box(10_000, 10_000, 10_100, 10_100)], crs="EPSG:5070"
    )
    with pytest.raises((SegmentationError, ValueError)):
        _run(scenario)


def test_missing_stand_age_falls_back_to_the_aoi_median(scenario, monkeypatch):
    # plot-20 has no COND row; STDAGE must be filled from plot-10's median
    # rather than raising or leaving NaN in the CA cost function.
    fiadb_path = scenario["fiadb_path"]
    with sqlite3.connect(fiadb_path) as connection:
        connection.execute("delete from COND where PLT_CN = 'plot-20'")

    units, _weights = _run(scenario)
    assert len(units) > 0
