"""Run the fitted Stage A / Stage B model over the STATEWIDE hole universe.

``embed_holes.run_apply`` is the AOI version of this. The statewide variant is
the same server-side evaluation, but tiled over the exact grid of the
statewide strata raster (:mod:`statewide_repair`'s output), because
``finalize_add_back.decide`` requires the scored raster and the strata raster
to share shape, CRS and transform bit-for-bit.

Every tile request stays under Earth Engine's ~50 MB download ceiling: two
uint16 bands at 30 m mean ``max_tile_pixels`` bounds the pixel count per tile.

Leakage rule: the model's feature year must be <= 2022 (same enforced cap as
``embed_holes.check_feature_years``).

Usage
-----
    uv run python -m pipeline.s1_initial_state.score_holes_statewide \\
        --strata-tif data/processed/statewide_repair/treemap_strata_fl.tif
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio import windows

from pipeline.s1_initial_state.embed_holes import (
    OUTPUT_SCALE_M,
    SCORE_SCALE,
    canvas_offsets,
    check_feature_years,
    init_ee,
    probability_image,
    similarity_image,
    tile_download_params,
)
from pipeline.spatial_ref import project_crs
from pipeline.s1_initial_state.statewide_repair import OUT_DIR as STRATA_DEFAULT

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "data/interim/treemap_holes"
DEFAULT_STRATA = STRATA_DEFAULT / "treemap_strata_fl.tif"
DEFAULT_MODEL = DATA_DIR / "hole_model.json"
DEFAULT_OUT = DATA_DIR / "hole_prob_similarity_statewide.tif"

# Two uint16 bands: the DOWNLOAD ceiling would allow ~12M px per tile, but the
# server-side COMPUTE of the 6-exemplar similarity + 64-band logistic measured
# out between 2M and 3M px per request ("User memory limit exceeded" above
# ~2.5M). 2M keeps a margin; at 640M statewide px that is ~320 tiles.
DEFAULT_MAX_TILE_PIXELS = 2_000_000


def grid_tiles(transform, rows: int, cols: int,
               max_tile_pixels: int = DEFAULT_MAX_TILE_PIXELS,
               max_tile_width: int = 2_000):
    """Split a raster into 2D tiles under the pixel budget AND the width cap.

    The pixel budget bounds the server-side compute of the per-tile scoring
    (measured: "User memory limit exceeded" between 2M and 3M px). The width
    cap bounds it independently: a 27,077-wide tile failed even at 1M px while
    a 2,000 x 1,000 tile passed at 2M px — the per-row footprint scales with
    width, so full-state row bands blow the limit at any height.
    Every tile edge sits on an exact pixel row/column, so the reassembled
    canvas aligns with the strata raster without resampling.
    Returns ``(window, bounds)`` pairs in reading order.
    """
    if max_tile_pixels < max_tile_width:
        raise ValueError(
            f"max_tile_pixels {max_tile_pixels} < max_tile_width {max_tile_width}: "
            "a single-row tile would not fit"
        )
    band_rows = max(1, max_tile_pixels // max_tile_width)
    tiles = []
    for row0 in range(0, rows, band_rows):
        height = min(band_rows, rows - row0)
        for col0 in range(0, cols, max_tile_width):
            width = min(max_tile_width, cols - col0)
            window = windows.Window(col_off=col0, row_off=row0, width=width, height=height)
            top = transform.f - row0 * OUTPUT_SCALE_M
            left = transform.c + col0 * OUTPUT_SCALE_M
            bounds = (left, top - height * OUTPUT_SCALE_M,
                      left + width * OUTPUT_SCALE_M, top)
            tiles.append((window, bounds))
    return tiles


def _fetch(url: str, dest: Path, attempts: int = 5, timeout_s: int = 600) -> None:
    """Download one tile with a hard timeout and backoff/retry.

    A bare urlretrieve can hang forever on a stalled connection and EE's
    download service returns transient 5xx/429 under load; both should retry,
    not stall the whole 300+-tile run.
    """
    import urllib.error

    delay = 5.0
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as response:
                dest.write_bytes(response.read())
            return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            status = getattr(exc, "code", None)
            if attempt == attempts:
                raise
            if status is not None and status < 500 and status != 429:
                raise  # 4xx other than 429 is not transient
            print(f"  tile fetch attempt {attempt} failed ({exc}); retrying in {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 120.0)


APPLY_STRATA = (3, 4)  # the only strata whose decision reads the scores


def needed_tiles(tiles, strata_tif: Path) -> list:
    """Drop tiles whose strata window contains no S3/S4 pixel.

    Only S3/S4 read the scores: S1/S2 are unconditional and S5 stays a hole,
    so a window with neither stratum contributes zeros no different from an
    unrequested window. Florida's grid is ~70% ocean/peninsula, so this skips
    most of the requests.
    """
    keep = []
    with rasterio.open(strata_tif) as src:
        for window, bounds in tiles:
            block = src.read(1, window=window)
            if np.isin(block, APPLY_STRATA).any():
                keep.append((window, bounds))
    return keep


def paste_tile(canvas: np.ndarray, tile: np.ndarray, row0: int, col0: int) -> None:
    """Place one tile on the canvas, clipped to the canvas edges."""
    src_r0, dst_r0 = (0, row0) if row0 >= 0 else (-row0, 0)
    src_c0, dst_c0 = (0, col0) if col0 >= 0 else (-col0, 0)
    h = min(tile.shape[1] - src_r0, canvas.shape[1] - dst_r0)
    w = min(tile.shape[2] - src_c0, canvas.shape[2] - dst_c0)
    canvas[:, dst_r0:dst_r0 + h, dst_c0:dst_c0 + w] = tile[:, src_r0:src_r0 + h, src_c0:src_c0 + w]


def score_statewide(model_json: Path, strata_tif: Path, out_tif: Path,
                    max_tile_pixels: int = DEFAULT_MAX_TILE_PIXELS,
                    max_tile_width: int = 2_000) -> Path:
    """Evaluate Stage A + Stage B over the strata raster's whole grid."""
    check_feature_years([json.loads(model_json.read_text())["feature_year"]])
    with rasterio.open(strata_tif) as src:
        rows, cols = src.height, src.width
        transform = src.transform
        profile = src.profile
    tiles = needed_tiles(grid_tiles(transform, rows, cols, max_tile_pixels, max_tile_width),
                         strata_tif)
    print(f"scoring {rows:,} x {cols:,} px in {len(tiles)} tile(s) with S3/S4 pixels")

    ee = init_ee()
    model = json.loads(model_json.read_text())

    origin = (transform.c, transform.f)
    canvas = None
    tmp = out_tif.parent / ".score_tile.tif"
    for i, (window, bounds) in enumerate(tiles, start=1):
        # Each tile composites only over itself: the statewide mosaic is the
        # same per-tile computation the AOI apply does, N times, not one
        # CONUS-spanning graph (which is what blows the user memory limit).
        region = ee.Geometry.Rectangle(list(bounds), proj=project_crs(), geodesic=False)
        stacked = (
            probability_image(ee, model, region).multiply(SCORE_SCALE).round()
            .addBands(similarity_image(ee, model, region).add(1).multiply(SCORE_SCALE).round())
            .toUint16()
        )
        params = tile_download_params(bounds)
        url = stacked.getDownloadURL(params)
        _fetch(url, tmp)
        with rasterio.open(tmp) as tile_src:
            if tile_src.crs != rasterio.crs.CRS.from_epsg(5070):
                raise ValueError(f"Earth Engine tile CRS {tile_src.crs} != EPSG:5070")
            data = tile_src.read()
            row0, col0 = canvas_offsets(tile_src.transform, origin)
            if data.shape != (2, window.height, window.width):
                raise ValueError(f"tile {i} shape {data.shape} != "
                                 f"{(2, window.height, window.width)}")
        tmp.unlink()
        if canvas is None:
            canvas = np.zeros((2, rows, cols), dtype=data.dtype)
        paste_tile(canvas, data, row0, col0)
        print(f"  tile {i}/{len(tiles)} rows {window.row_off}:{window.row_off + window.height}")
    tmp.unlink(missing_ok=True)

    profile.update(count=2, dtype="uint16", nodata=None, compress="lzw",
                   tiled=False, blockysize=None, blockxsize=None)
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_tif, "w", **profile) as dst:
        dst.write(canvas)
    print(f"wrote {out_tif} {canvas.shape} "
          f"(band 1 = prob*{SCORE_SCALE}, band 2 = (cosine+1)*{SCORE_SCALE})")
    return out_tif


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-json", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--strata-tif", type=Path, default=DEFAULT_STRATA)
    parser.add_argument("--out-tif", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-tile-pixels", type=int, default=DEFAULT_MAX_TILE_PIXELS)
    parser.add_argument("--max-tile-width", type=int, default=2_000)
    args = parser.parse_args()
    score_statewide(args.model_json, args.strata_tif, args.out_tif,
                    args.max_tile_pixels, args.max_tile_width)


if __name__ == "__main__":
    main()
