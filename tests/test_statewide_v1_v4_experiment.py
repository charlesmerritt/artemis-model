"""Hansen GFC on Florida's TreeMap grid for experiments/2026-10-12_statewide-v1-v4.

`01_hansen.py` warps the Hansen lossyear and treecover2000 tiles (EPSG:4326, 0.00025°) onto
TreeMap 2022's 30 m EPSG:5070 lattice, the input `county_improvement.read_on_grid` reads without
resampling. The FL5 runs read an Earth Engine export of the same layers instead.
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from pipeline.s1_initial_state import county_improvement as ci

EXP = Path(__file__).resolve().parents[1] / "experiments/2026-10-12_statewide-v1-v4"
spec = importlib.util.spec_from_file_location("statewide_hansen", EXP / "01_hansen.py")
hansen = importlib.util.module_from_spec(spec)
sys.modules["statewide_hansen"] = hansen
spec.loader.exec_module(hansen)
common = sys.modules["common"]
spec = importlib.util.spec_from_file_location("statewide_rasters", EXP / "02_rasters.py")
rasters = importlib.util.module_from_spec(spec)
sys.modules["statewide_rasters"] = rasters
spec.loader.exec_module(rasters)

DEG = 0.00025
TREEMAP_ORIGIN = (-2361585.0, 3177435.0)


def _tile(path: Path, west: float, north: float, size: int, salt: int) -> np.ndarray:
    rows, cols = np.indices((size, size))
    values = ((rows * 7 + cols * 13 + salt) % 251).astype(np.uint8)
    with rasterio.open(path, "w", driver="GTiff", width=size, height=size, count=1,
                       dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(west, north, DEG, DEG)) as dst:
        dst.write(values, 1)
    return values


@pytest.fixture
def seam(tmp_path):
    """Two tiles meeting at 30°N, like Hansen's 30N_090W and 40N_090W, for each layer.

    The grid is large enough (18 km) for GDAL's default approximate transformer to move
    sampling points across source pixel edges, as it did statewide.
    """
    size, west = 1200, -82.3
    sources, truth = {}, {}
    for salt, layer in enumerate(hansen.LAYERS):
        north_tile = tmp_path / f"{layer}_north.tif"
        south_tile = tmp_path / f"{layer}_south.tif"
        n = _tile(north_tile, west, 30.0 + size * DEG, size, salt)
        s = _tile(south_tile, west, 30.0, size, salt + 100)
        sources[layer] = [north_tile, south_tile]
        truth[layer] = (west, 30.0 + size * DEG, np.vstack([n, s]))
    # A grid on the TreeMap lattice inside both tiles and crossing the seam.
    x, y = Transformer.from_crs(4326, 5070, always_xy=True).transform(-82.15, 30.0)
    col = int((x - TREEMAP_ORIGIN[0]) // 30)
    row = int((TREEMAP_ORIGIN[1] - y) // 30)
    grid = common.Grid(TREEMAP_ORIGIN[0] + (col - 300) * 30, TREEMAP_ORIGIN[1] - (row - 300) * 30,
                       600, 600)
    return sources, truth, grid


def _value_at_centres(grid, west, north, values):
    rows, cols = np.indices((grid.height, grid.width))
    x = grid.west + (cols + 0.5) * 30
    y = grid.north - (rows + 0.5) * 30
    lon, lat = Transformer.from_crs(5070, 4326, always_xy=True).transform(x, y)
    return values[np.floor((north - lat) / DEG).astype(int), np.floor((lon - west) / DEG).astype(int)]


def test_warp_takes_each_pixel_from_the_source_value_at_its_centre(seam, tmp_path):
    sources, truth, grid = seam
    out = tmp_path / "hansen.tif"
    hansen.warp_to_grid(sources, out, grid)
    with rasterio.open(out) as src:
        assert src.count == 2 and src.descriptions == hansen.LAYERS
        got = src.read()
    for band, layer in enumerate(hansen.LAYERS):
        np.testing.assert_array_equal(got[band], _value_at_centres(grid, *truth[layer]))


def test_warped_raster_reads_back_on_the_treemap_lattice(seam, tmp_path):
    sources, _, grid = seam
    out = tmp_path / "hansen.tif"
    hansen.warp_to_grid(sources, out, grid)
    county = ci.Grid(grid.transform, (grid.height, grid.width))
    loss, cover = ci.read_on_grid(out, county, fill=0, indexes=[1, 2])
    with rasterio.open(out) as src:
        np.testing.assert_array_equal(np.stack([loss, cover]), src.read())


def test_agreement_counts_band_and_rule_differences():
    aoi = np.zeros((2, 2, 3), np.uint8)
    fl = aoi.copy()
    aoi[0] = [[0, 5, 15], [20, 0, 0]]
    aoi[1] = [[90, 90, 90], [90, 10, 90]]
    fl[0] = [[0, 5, 15], [0, 0, 3]]      # lost one loss pixel, gained one
    fl[1] = [[90, 90, 90], [90, 10, 90]]
    a = hansen.agreement(fl, aoi)
    assert a["lossyear"]["pixels_differ"] == 2
    assert a["treecover2000"]["pixels_differ"] == 0
    # v1 counts loss years 1-22 on >= 30 % cover: AOI has 5, 15, 20; FL has 5, 15, 3.
    assert a["rule_v1"] == {"aoi": 3, "statewide": 3, "both": 2}
    # v4 counts 12-22: AOI 15, 20; FL 15.
    assert a["rule_v4"] == {"aoi": 2, "statewide": 1, "both": 1}


def _tif(path, values, west, nodata=255):
    values = np.asarray(values, dtype=np.uint8)
    with rasterio.open(path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0],
                       count=1, dtype="uint8", crs="EPSG:5070", nodata=nodata,
                       transform=from_origin(west, 60, 30, 30)) as dst:
        dst.write(values, 1)


def test_compare_counts_differences_on_the_released_grid_and_names_missing_rasters(tmp_path):
    ours, released = tmp_path / "ours", tmp_path / "released"
    ours.mkdir(), released.mkdir()
    # The rerun's grid starts one column further west; only one pixel truly differs.
    _tif(released / "a.tif", [[1, 2], [3, 4]], west=30)
    _tif(ours / "a.tif", [[255, 1, 2], [255, 3, 5]], west=0)
    _tif(released / "b.tif", [[1]], west=30)
    assert rasters.compare(ours, released) == {"a.tif": 1, "b.tif": "missing from the rerun"}


def test_seams_count_crossing_patches_that_disagree_and_pixels_cut_at_the_line():
    county = np.array([[1, 1, 2, 2, 2]] * 4)
    added = np.array([[1, 1, 1, 0, 0],     # crosses, one donor: consistent
                      [0, 0, 0, 0, 0],
                      [1, 1, 1, 1, 0],     # crosses, two donors: a seam
                      [0, 0, 0, 0, 0]], dtype=bool)
    tm = np.array([[5, 5, 5, 0, 0], [0] * 5, [5, 5, 6, 6, 0], [0] * 5])
    prov = np.where(added, 4, 3).astype(np.uint8)
    left_out = np.zeros(added.shape, dtype=bool)
    left_out[0, 2] = False
    left_out[1, 2] = True                  # county 2, touching county 1's added pixels
    left_out[3, 4] = True                  # touches only its own county's pixels
    r = rasters.seam_counts(added, tm, prov, county, left_out)
    assert (r["patches"], r["crossing_patches"], r["split_donor_patches"]) == (2, 2, 1)
    assert r["split_mode_patches"] == 0
    assert r["cut_at_line_px"] == 1


def test_scene_split_scales_add_back_outside_the_scene_by_the_fl5_loss():
    P = ci.TreeMapProvenance
    prov = np.array([[P.ADDED_BACK, P.UNMAPPED_LAND, P.ADDED_BACK, P.ADDED_BACK_YOUNG],
                     [P.PUBLISHED, P.WATER, P.ADDED_BACK_YOUNG, P.PUBLISHED],
                     [255, 255, 255, 255]], dtype=np.uint8)   # off the state: nodata
    # Pixel centres x = 15, 45 | 75, 105: the scene holds the left two columns.
    scene = rasterio.coords.BoundingBox(left=0, bottom=0, right=60, top=90)
    r = rasters.scene_split(prov, from_origin(0, 90, 30, 30), scene, loss=0.75)
    acres = ci.ACRES_PER_PIXEL
    assert r["added_back_in_scene_ac"] == round(1 * acres, 1)
    assert r["added_back_outside_ac"] == round(3 * acres, 1)
    assert r["land_share_in_scene"] == round(3 / 7, 3)
    # Outside the scene only the non-Obata 25 % was found: 3 px found implies 9 px missed.
    assert r["est_missing_outside_ac"] == round(9 * acres, 1)
