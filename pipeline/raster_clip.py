"""
Clip a CONUS-scale project raster down to a region, reading it where it lives.

The problem this solves: the LANDFIRE EVT raster is 2.99 GB of CONUS at 30 m, and
Florida is 4% of it. Every workflow that only needs Florida was paying for the
other 96% — or, off the workstation, was simply blocked, because 3 GB is over
``data_access``'s fetch cap and a notebook has no business pulling that mid-cell.

Two decisions do the work:

**Stage the source, then read it locally.** The tempting alternative is to leave
the file in R2 and let GDAL range-request only the tiles the region covers — the
EVT is internally tiled (128 x 128, LZW), so that works. Measured on this bucket,
it is the wrong trade by a wide margin: bulk transfer runs at ~100 MB/s, so the
whole 2.99 GB arrives in about 30 seconds, while a windowed read over ``/vsicurl/``
issues roughly 39,000 range requests at GDAL's default 16 KB chunk and spends
minutes on latency alone. So the file is staged once through ``data_access``
(landing in the gitignored ``data/r2_cache/``) and clipped from disk. The staged
copy is disposable — that is the point of producing the clip.

**Clip to the region's bounding box, not to its outline.** Masking to the state
polygon would set every pixel in Georgia to nodata, and the workflows that consume
these clips need exactly that ground: ``raster_correction`` trains on a padding
ring around an AOI, and an AOI near the state line would lose half its ring to a
political boundary that means nothing to a classifier. Pass ``mask=True`` when the
outline really is the point.

Nothing here resamples or reprojects: the clip keeps the source CRS, the source
grid alignment, and the source dtype and nodata, so it stays pixel-co-registered
with everything else on the project's 30 m grid.

Usage
-----
    # Florida out of the LANDFIRE EVT, staging from R2 if the drive is absent
    uv run python -m pipeline.raster_clip \
        --raster raw.landfire.evt_tif --region config/extent.geojson \
        --name LF2022_EVT_FL

    # Check the size before committing to it
    uv run python -m pipeline.raster_clip \
        --raster raw.landfire.vcc_tif --region config/extent.geojson \
        --name LF2022_VCC_FL --dry-run

    # An explicit path, masked to the region outline
    uv run python -m pipeline.raster_clip \
        --raster /mnt/d/LF2022_EVT_CONUS/.../LF2022_EVT_CONUS.tif \
        --region config/extent.geojson --name evt_fl_masked --mask
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterator

import geopandas as gpd
import numpy as np
import rasterio
from rasterio import features as rio_features
from rasterio import windows as rio_windows
from shapely.geometry.base import BaseGeometry

from pipeline import data_access
from pipeline.raster_windows import round_outward

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = "data/interim/clips"

# Output tiling. 512 beats the EVT's own 128 for consumers that read AOI-sized
# windows: fewer, larger reads for the same bytes.
DEFAULT_BLOCK = 512

# Rows per read/write pass. 4096 rows of a Florida-width clip is ~200 MB at int16,
# which keeps peak memory bounded no matter how large the region is.
DEFAULT_CHUNK_ROWS = 4096

# Staging cap for the source raster, well above data_access's conservative 512 MB
# default: the whole point of this module is to handle the multi-gigabyte CONUS
# rasters, and refusing to fetch one would defeat it.
DEFAULT_MAX_STAGE_MB = 8_000


class RemoteRasterUnavailable(RuntimeError):
    """The raster is on neither the drive nor R2."""


# ──────────────────────────────────────────────────────────────────────────────
# Getting at the source
# ──────────────────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def open_source(
    path: str | Path, max_stage_mb: int = DEFAULT_MAX_STAGE_MB
) -> Iterator[rasterio.DatasetReader]:
    """
    Open a declared raster for windowed reading, staging it from R2 if need be.

    A local file is opened directly. Otherwise the bucket copy is fetched to
    ``data/r2_cache/`` first (see the module docstring for why staging beats
    streaming here) and opened from there; a second run reuses the staged copy.

    Raises RemoteRasterUnavailable naming both sources when neither has it.
    """
    local = Path(path)
    if not local.exists():
        logger.info("not on the drive, staging from R2: %s", data_access.remote_url(path))
        staged = data_access.ensure_local(path, max_fetch_mb=max_stage_mb)
        if staged is None:
            raise RemoteRasterUnavailable(data_access.unavailable_reason(path))
        local = staged

    logger.info("reading %s (%.2f GB)", local, local.stat().st_size / 1e9)
    with rasterio.open(local) as dataset:
        yield dataset


# ──────────────────────────────────────────────────────────────────────────────
# The clip
# ──────────────────────────────────────────────────────────────────────────────


def read_region(region_path: str | Path, layer: str | None = None) -> gpd.GeoDataFrame:
    """Read and validate a region vector layer, once — the file can be large."""
    gdf = gpd.read_file(region_path, layer=layer) if layer else gpd.read_file(region_path)
    if gdf.empty:
        raise ValueError(f"Region layer has no features: {region_path}")
    if gdf.crs is None:
        raise ValueError(f"Region layer has no CRS: {region_path}")
    return gdf


def region_geometry(region_path: str | Path, layer: str | None = None) -> BaseGeometry:
    """Dissolve a region vector to one geometry, in its own declared CRS."""
    return read_region(region_path, layer).union_all()


def region_window(dataset: rasterio.DatasetReader, region: BaseGeometry, region_crs) -> Any:
    """
    The whole-pixel window of ``dataset`` covering the region's bounding box.

    Rounded outward so the clip is never a fraction of a pixel short, and
    intersected with the dataset so a region that overhangs the raster still
    yields the part that exists.
    """
    local = gpd.GeoSeries([region], crs=region_crs).to_crs(dataset.crs).iloc[0]
    minx, miny, maxx, maxy = local.bounds
    requested = rio_windows.from_bounds(minx, miny, maxx, maxy, dataset.transform)
    outward = round_outward(requested)
    try:
        return rio_windows.intersection(
            outward, rio_windows.Window(0, 0, dataset.width, dataset.height)
        )
    except rio_windows.WindowError as err:
        raise ValueError(
            "The region does not overlap this raster at all — check that both are "
            "where you think they are."
        ) from err


def clip_profile(
    dataset: rasterio.DatasetReader,
    window: Any,
    block: int = DEFAULT_BLOCK,
    compress: str = "lzw",
) -> dict[str, Any]:
    """An output profile co-registered with the source: same CRS, grid, dtype, nodata."""
    return {
        "driver": "GTiff",
        "height": int(window.height),
        "width": int(window.width),
        "count": dataset.count,
        "dtype": dataset.dtypes[0],
        "crs": dataset.crs,
        "transform": dataset.window_transform(window),
        "nodata": dataset.nodata,
        "tiled": True,
        "blockxsize": block,
        "blockysize": block,
        "compress": compress,
        "BIGTIFF": "IF_SAFER",
    }


def clip_raster(
    source: str | Path,
    region_path: str | Path,
    out_path: str | Path,
    region_layer: str | None = None,
    mask: bool = False,
    block: int = DEFAULT_BLOCK,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    compress: str = "lzw",
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Clip ``source`` to the bounding box of ``region_path`` and write a tiled GeoTIFF.

    Copied in horizontal strips so peak memory is bounded by ``chunk_rows``
    regardless of region size. Returns a record of what was written, suitable for
    printing or storing beside the output.
    """
    region_gdf = read_region(region_path, region_layer)
    region = region_gdf.union_all()
    region_crs = region_gdf.crs

    out_path = Path(out_path)

    with open_source(source) as dataset:
        window = region_window(dataset, region, region_crs)
        profile = clip_profile(dataset, window, block, compress)
        record = {
            "source": str(source),
            "region": str(region_path),
            "crs": str(dataset.crs),
            "source_shape": [dataset.height, dataset.width],
            "clip_shape": [int(window.height), int(window.width)],
            "clip_fraction": round(
                (window.height * window.width) / (dataset.height * dataset.width), 5
            ),
            "dtype": dataset.dtypes[0],
            "nodata": None if dataset.nodata is None else float(dataset.nodata),
            "masked_to_outline": bool(mask),
            "output": str(out_path),
        }

        logger.info(
            "source %d x %d -> clip %d x %d (%.2f%% of the raster, %.2f GB uncompressed)",
            dataset.height,
            dataset.width,
            window.height,
            window.width,
            record["clip_fraction"] * 100,
            window.height * window.width * np.dtype(dataset.dtypes[0]).itemsize / 1e9,
        )
        if dry_run:
            record["written"] = False
            return record

        if mask and dataset.nodata is None:
            raise ValueError(
                "mask=True needs a nodata value to write outside the outline, and this "
                "raster declares none."
            )

        local_region = gpd.GeoSeries([region], crs=region_crs).to_crs(dataset.crs).iloc[0]
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with rasterio.open(out_path, "w", **profile) as sink:
            for row in range(0, int(window.height), chunk_rows):
                rows = min(chunk_rows, int(window.height) - row)
                read_window = rio_windows.Window(
                    col_off=window.col_off,
                    row_off=window.row_off + row,
                    width=window.width,
                    height=rows,
                )
                data = dataset.read(window=read_window)

                if mask:
                    keep = rio_features.rasterize(
                        [(local_region, 1)],
                        out_shape=(rows, int(window.width)),
                        transform=dataset.window_transform(read_window),
                        fill=0,
                        dtype="uint8",
                    ).astype(bool)
                    data = np.where(keep, data, dataset.nodata).astype(profile["dtype"])

                sink.write(data, window=rio_windows.Window(0, row, window.width, rows))
                logger.info("  rows %d–%d of %d", row, row + rows, int(window.height))

    record["written"] = True
    record["size_mb"] = round(out_path.stat().st_size / 1e6, 1)
    logger.info("wrote %s (%.1f MB)", out_path, record["size_mb"])
    return record


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def resolve_raster_argument(value: str) -> str:
    """
    Accept either a filesystem path or a dotted ``config/data_paths.yaml`` key.

    ``raw.landfire.evt_tif`` is easier to get right than the path it stands for, and
    it keeps the declared location in one place.
    """
    if "/" in value or "\\" in value:
        return value

    node: Any = data_access.data_paths()
    for part in value.split("."):
        if not isinstance(node, dict) or part not in node:
            raise SystemExit(f"Not a path and not a key in data_paths.yaml: {value!r}")
        node = node[part]
    if not isinstance(node, str):
        raise SystemExit(f"{value!r} names a group in data_paths.yaml, not a file")
    return node


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clip a large raster to a region, reading it from the drive or R2",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--raster",
        required=True,
        help="Path, or a dotted data_paths.yaml key such as raw.landfire.evt_tif",
    )
    parser.add_argument("--region", default="config/extent.geojson", help="Region vector layer")
    parser.add_argument("--region-layer", help="Layer name within a multi-layer region source")
    parser.add_argument("--name", help="Output stem (default: <raster stem>_clip)")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--mask",
        action="store_true",
        help="Also null out pixels outside the region outline, not just its bounding box",
    )
    parser.add_argument("--block", type=int, default=DEFAULT_BLOCK, help="Output tile size")
    parser.add_argument("--chunk-rows", type=int, default=DEFAULT_CHUNK_ROWS)
    parser.add_argument("--compress", default="lzw")
    parser.add_argument("--dry-run", action="store_true", help="Report the size, write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    source = resolve_raster_argument(args.raster)
    name = args.name or f"{Path(source).stem}_clip"
    out_path = Path(args.out_dir) / f"{name}.tif"

    try:
        record = clip_raster(
            source,
            args.region,
            out_path,
            region_layer=args.region_layer,
            mask=args.mask,
            block=args.block,
            chunk_rows=args.chunk_rows,
            compress=args.compress,
            dry_run=args.dry_run,
        )
    except RemoteRasterUnavailable as err:
        raise SystemExit(str(err)) from err

    if record["written"]:
        manifest = out_path.with_suffix(".json")
        manifest.write_text(json.dumps(record, indent=2))
        logger.info("manifest: %s", manifest)

    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
