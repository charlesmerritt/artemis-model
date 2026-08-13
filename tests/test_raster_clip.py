"""
Tests for pipeline/raster_clip.py.

All local: the R2 path (``_served`` plus ``/vsicurl/``) needs a configured bucket
and is not exercised here. Everything that decides *what* gets clipped and whether
the result is still co-registered with its source is.
"""

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio import windows as rio_windows
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import raster_clip

CRS = "EPSG:5070"
PIXEL = 30.0
SIZE = 200
ORIGIN_X = 1_200_000.0
ORIGIN_Y = 1_000_000.0
TRANSFORM = Affine(PIXEL, 0, ORIGIN_X, 0, -PIXEL, ORIGIN_Y)
NODATA = 32767


@pytest.fixture
def source_raster(tmp_path):
    """A 200 x 200 int16 raster whose values encode their own pixel position."""
    rows, cols = np.indices((SIZE, SIZE))
    values = (rows * SIZE + cols).astype(np.int16)
    path = tmp_path / "source.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=SIZE,
        width=SIZE,
        count=1,
        dtype="int16",
        crs=CRS,
        transform=TRANSFORM,
        nodata=NODATA,
        tiled=True,
        blockxsize=64,
        blockysize=64,
    ) as dataset:
        dataset.write(values, 1)
    return path


def write_region(tmp_path, geom_5070, name="region.geojson"):
    path = tmp_path / name
    gpd.GeoDataFrame({"name": ["r"]}, geometry=[geom_5070], crs=CRS).to_file(path)
    return path


@pytest.fixture
def region(tmp_path):
    """A box covering rows/cols 50–120 of the source grid."""
    return write_region(
        tmp_path,
        box(
            ORIGIN_X + 50 * PIXEL,
            ORIGIN_Y - 120 * PIXEL,
            ORIGIN_X + 120 * PIXEL,
            ORIGIN_Y - 50 * PIXEL,
        ),
    )


# ──────────────────────────────────────────────────────────────────────────────
# resolve_raster_argument
# ──────────────────────────────────────────────────────────────────────────────


def test_dotted_key_resolves_through_data_paths():
    resolved = raster_clip.resolve_raster_argument("raw.landfire.evt_tif")
    assert resolved.endswith("LF2022_EVT_CONUS.tif")


def test_a_path_is_passed_through_untouched():
    assert raster_clip.resolve_raster_argument("/tmp/some/raster.tif") == "/tmp/some/raster.tif"


def test_an_unknown_key_is_rejected():
    with pytest.raises(SystemExit, match="not a key in data_paths"):
        raster_clip.resolve_raster_argument("raw.landfire.nope")


def test_a_group_key_is_rejected():
    with pytest.raises(SystemExit, match="names a group"):
        raster_clip.resolve_raster_argument("raw.landfire")


# ──────────────────────────────────────────────────────────────────────────────
# region_window / clip_profile
# ──────────────────────────────────────────────────────────────────────────────


def test_region_window_covers_the_region_bbox(source_raster, region):
    with rasterio.open(source_raster) as dataset:
        window = raster_clip.region_window(dataset, raster_clip.region_geometry(region), CRS)
        left, bottom, right, top = rio_windows.bounds(window, dataset.transform)

    minx, miny, maxx, maxy = raster_clip.region_geometry(region).bounds
    assert left <= minx and bottom <= miny and right >= maxx and top >= maxy
    assert (window.height, window.width) == (70, 70)


def test_region_window_rounds_outward_to_whole_pixels(source_raster, tmp_path):
    # A region offset by a third of a pixel must still be fully covered.
    offset = write_region(
        tmp_path,
        box(
            ORIGIN_X + 50 * PIXEL + 10,
            ORIGIN_Y - 120 * PIXEL - 10,
            ORIGIN_X + 120 * PIXEL + 10,
            ORIGIN_Y - 50 * PIXEL - 10,
        ),
        name="offset.geojson",
    )
    geom = raster_clip.region_geometry(offset)
    with rasterio.open(source_raster) as dataset:
        window = raster_clip.region_window(dataset, geom, CRS)
        left, bottom, right, top = rio_windows.bounds(window, dataset.transform)

    # 50.33 → 120.33 pixels rounds outward to whole pixels 50 → 121, so 71 of them,
    # and the region must sit strictly inside the result.
    assert (window.height, window.width) == (71, 71)
    minx, miny, maxx, maxy = geom.bounds
    assert left <= minx and bottom <= miny and right >= maxx and top >= maxy
    assert window.col_off == 50 and window.row_off == 50


def test_region_window_clips_to_the_raster(source_raster, tmp_path):
    overhang = write_region(
        tmp_path,
        box(ORIGIN_X - 50_000, ORIGIN_Y - 50_000, ORIGIN_X + 50_000, ORIGIN_Y + 50_000),
        name="overhang.geojson",
    )
    with rasterio.open(source_raster) as dataset:
        window = raster_clip.region_window(dataset, raster_clip.region_geometry(overhang), CRS)
    assert (window.row_off, window.col_off) == (0, 0)
    assert (window.height, window.width) == (SIZE, SIZE)


def test_region_window_rejects_a_disjoint_region(source_raster, tmp_path):
    elsewhere = write_region(
        tmp_path,
        box(ORIGIN_X + 500_000, ORIGIN_Y, ORIGIN_X + 501_000, ORIGIN_Y + 1_000),
        name="elsewhere.geojson",
    )
    with rasterio.open(source_raster) as dataset:
        with pytest.raises(ValueError, match="does not overlap"):
            raster_clip.region_window(dataset, raster_clip.region_geometry(elsewhere), CRS)


def test_clip_profile_preserves_the_source_properties(source_raster, region):
    with rasterio.open(source_raster) as dataset:
        window = raster_clip.region_window(dataset, raster_clip.region_geometry(region), CRS)
        profile = raster_clip.clip_profile(dataset, window)

    assert profile["dtype"] == "int16"
    assert profile["nodata"] == NODATA
    assert profile["count"] == 1
    assert profile["crs"] == rasterio.crs.CRS.from_string(CRS)
    assert profile["tiled"] is True
    # Same 30 m grid, origin moved to the window's corner.
    assert profile["transform"].a == PIXEL
    assert profile["transform"].c == ORIGIN_X + 50 * PIXEL


# ──────────────────────────────────────────────────────────────────────────────
# clip_raster
# ──────────────────────────────────────────────────────────────────────────────


def test_clip_matches_the_source_window_pixel_for_pixel(source_raster, region, tmp_path):
    out = tmp_path / "clip.tif"
    record = raster_clip.clip_raster(source_raster, region, out, chunk_rows=17)

    with rasterio.open(source_raster) as src, rasterio.open(out) as dst:
        expected = src.read(1, window=rio_windows.Window(50, 50, 70, 70))
        np.testing.assert_array_equal(dst.read(1), expected)
        assert dst.crs == src.crs
        assert dst.nodata == src.nodata
        assert dst.dtypes == src.dtypes
        # Co-registered: the clip's origin sits on a source pixel corner.
        assert (dst.transform.c - src.transform.c) % PIXEL == 0
        assert (dst.transform.f - src.transform.f) % PIXEL == 0

    assert record["written"] is True
    assert record["clip_shape"] == [70, 70]
    assert record["clip_fraction"] == pytest.approx(70 * 70 / (SIZE * SIZE), abs=1e-6)


def test_chunking_does_not_change_the_result(source_raster, region, tmp_path):
    one_pass = tmp_path / "one.tif"
    many_passes = tmp_path / "many.tif"
    raster_clip.clip_raster(source_raster, region, one_pass, chunk_rows=10_000)
    raster_clip.clip_raster(source_raster, region, many_passes, chunk_rows=7)

    with rasterio.open(one_pass) as a, rasterio.open(many_passes) as b:
        np.testing.assert_array_equal(a.read(1), b.read(1))


def test_dry_run_writes_nothing(source_raster, region, tmp_path):
    out = tmp_path / "nope.tif"
    record = raster_clip.clip_raster(source_raster, region, out, dry_run=True)
    assert record["written"] is False
    assert record["clip_shape"] == [70, 70]
    assert not out.exists()


def test_bbox_clip_keeps_pixels_outside_the_outline(source_raster, tmp_path):
    """The default. A ring around an AOI is evidence, not overspill to be trimmed."""
    diamond = write_region(
        tmp_path,
        box(
            ORIGIN_X + 50 * PIXEL,
            ORIGIN_Y - 120 * PIXEL,
            ORIGIN_X + 120 * PIXEL,
            ORIGIN_Y - 50 * PIXEL,
        ).intersection(
            box(
                ORIGIN_X + 60 * PIXEL,
                ORIGIN_Y - 110 * PIXEL,
                ORIGIN_X + 130 * PIXEL,
                ORIGIN_Y - 40 * PIXEL,
            )
        ),
        name="inner.geojson",
    )
    out = tmp_path / "bbox.tif"
    raster_clip.clip_raster(source_raster, diamond, out)
    with rasterio.open(out) as dataset:
        assert NODATA not in set(np.unique(dataset.read(1)))


def test_mask_nulls_pixels_outside_the_outline(source_raster, tmp_path):
    circle = write_region(
        tmp_path,
        box(
            ORIGIN_X + 50 * PIXEL,
            ORIGIN_Y - 120 * PIXEL,
            ORIGIN_X + 120 * PIXEL,
            ORIGIN_Y - 50 * PIXEL,
        ).centroid.buffer(500),
        name="circle.geojson",
    )
    out = tmp_path / "masked.tif"
    record = raster_clip.clip_raster(source_raster, circle, out, mask=True)

    with rasterio.open(out) as dataset:
        values = dataset.read(1)
    assert record["masked_to_outline"] is True
    # The corners of a bounding box around a circle are outside the circle.
    assert values[0, 0] == NODATA
    assert values[values.shape[0] // 2, values.shape[1] // 2] != NODATA


def test_mask_requires_a_nodata_value(tmp_path, region):
    path = tmp_path / "no_nodata.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=SIZE, width=SIZE, count=1,
        dtype="int16", crs=CRS, transform=TRANSFORM,
    ) as dataset:
        dataset.write(np.zeros((SIZE, SIZE), dtype=np.int16), 1)

    with pytest.raises(ValueError, match="needs a nodata value"):
        raster_clip.clip_raster(path, region, tmp_path / "out.tif", mask=True)


def test_open_source_reads_a_local_file(source_raster):
    with raster_clip.open_source(source_raster) as dataset:
        assert dataset.width == SIZE


def test_open_source_reports_when_neither_source_has_it(tmp_path):
    with pytest.raises(raster_clip.RemoteRasterUnavailable):
        with raster_clip.open_source(tmp_path / "absent.tif"):
            pass


def test_cli_writes_a_manifest_beside_the_output(source_raster, region, tmp_path):
    out_dir = tmp_path / "clips"
    exit_code = raster_clip.main(
        [
            "--raster", str(source_raster),
            "--region", str(region),
            "--name", "demo",
            "--out-dir", str(out_dir),
        ]
    )
    assert exit_code == 0
    manifest = json.loads((out_dir / "demo.json").read_text())
    assert manifest["clip_shape"] == [70, 70]
    assert manifest["output"].endswith("demo.tif")
    assert (out_dir / "demo.tif").exists()
