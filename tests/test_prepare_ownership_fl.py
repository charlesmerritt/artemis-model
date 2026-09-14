"""Prepared Florida ownership raster on the TreeMap grid."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine


def _write_src(path, values, transform, crs="EPSG:4326", nodata=15):
    h, w = values.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs=crs, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(values, 1)


def test_reproject_tile_moves_values_onto_the_target_grid(tmp_path):
    from pipeline.s1_initial_state.prepare_ownership_fl import reproject_tile

    # A 20 m source grid in geographic degrees, one class blob in the middle.
    src_transform = Affine(9e-5, 0, -83.0, 0, -9e-5, 29.0)
    src = np.full((400, 400), 15, dtype=np.uint8)
    src[150:250, 150:250] = 4  # corporate
    src_path = tmp_path / "src.tif"
    _write_src(src_path, src, src_transform)

    # Target: the 30 m 5070 grid tile that contains the blob's footprint.
    import rasterio.windows
    with rasterio.open(src_path) as s:
        bounds = rasterio.warp.transform_bounds(s.crs, "EPSG:5070", *s.bounds)
    tile = rasterio.windows.Window(0, 0, 100, 100)
    # place the blob's 5070 footprint inside the tile: pick tile origin near it
    from rasterio.transform import Affine as A
    tile_transform = A(30, 0, round(bounds[0] // 30) * 30, 0, -30, round(bounds[3] // 30) * 30)
    dst = np.full((100, 100), 15, dtype=np.uint8)
    reproject_tile(src_path, tile, tile_transform, dst)
    assert (dst == 4).sum() > 100          # the blob arrived
    assert (dst == 15).sum() > 0           # outside stayed nodata
    assert set(np.unique(dst)) <= {4, 15}  # nearest neighbour, no new classes


def test_reproject_tile_outside_source_is_all_nodata(tmp_path):
    from pipeline.s1_initial_state.prepare_ownership_fl import reproject_tile

    src_transform = Affine(9e-5, 0, -83.0, 0, -9e-5, 29.0)
    src_path = tmp_path / "src.tif"
    _write_src(src_path, np.full((10, 10), 3, dtype=np.uint8), src_transform)
    dst = np.zeros((50, 50), dtype=np.uint8)
    tile_transform = Affine(30, 0, 1500000, 0, -30, 900000)  # far away in 5070
    reproject_tile(src_path, rasterio.windows.Window(0, 0, 50, 50), tile_transform, dst)
    assert (dst == 15).all()
