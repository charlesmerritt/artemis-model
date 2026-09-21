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
import urllib.request
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
from pipeline.s1_initial_state.statewide_repair import OUT_DIR as STRATA_DEFAULT

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "data/interim/treemap_holes"
DEFAULT_STRATA = STRATA_DEFAULT / "treemap_strata_fl.tif"
DEFAULT_MODEL = DATA_DIR / "hole_model.json"
DEFAULT_OUT = DATA_DIR / "hole_prob_similarity_statewide.tif"

# Two uint16 bands: 2 * max_tile_pixels * 2 bytes <= 48 MB, under the ceiling.
DEFAULT_MAX_TILE_PIXELS = 12_000_000


def grid_tiles(transform, rows: int, cols: int,
               max_tile_pixels: int = DEFAULT_MAX_TILE_PIXELS):
    """Split a whole raster into full-width row bands under the pixel budget.

    Each band starts on an exact pixel row, so every tile is on the same grid
    as the strata raster and the reassembled canvas aligns without resampling.
    Returns ``(window, bounds)`` pairs in reading order.
    """
    if max_tile_pixels < cols:
        raise ValueError(
            f"max_tile_pixels {max_tile_pixels} < tile width {cols}: "
            "a single row band would not fit"
        )
    band_rows = max(1, max_tile_pixels // cols)
    tiles = []
    for row0 in range(0, rows, band_rows):
        height = min(band_rows, rows - row0)
        window = windows.Window(col_off=0, row_off=row0, width=cols, height=height)
        top = transform.f - row0 * OUTPUT_SCALE_M
        left = transform.c
        bounds = (left, top - height * OUTPUT_SCALE_M,
                  left + cols * OUTPUT_SCALE_M, top)
        tiles.append((window, bounds))
    return tiles


def paste_tile(canvas: np.ndarray, tile: np.ndarray, row0: int, col0: int) -> None:
    """Place one tile on the canvas, clipped to the canvas edges."""
    src_r0, dst_r0 = (0, row0) if row0 >= 0 else (-row0, 0)
    src_c0, dst_c0 = (0, col0) if col0 >= 0 else (-col0, 0)
    h = min(tile.shape[1] - src_r0, canvas.shape[1] - dst_r0)
    w = min(tile.shape[2] - src_c0, canvas.shape[2] - dst_c0)
    canvas[:, dst_r0:dst_r0 + h, dst_c0:dst_c0 + w] = tile[:, src_r0:src_r0 + h, src_c0:src_c0 + w]


def score_statewide(model_json: Path, strata_tif: Path, out_tif: Path,
                    max_tile_pixels: int = DEFAULT_MAX_TILE_PIXELS) -> Path:
    """Evaluate Stage A + Stage B over the strata raster's whole grid."""
    check_feature_years([json.loads(model_json.read_text())["feature_year"]])
    with rasterio.open(strata_tif) as src:
        rows, cols = src.height, src.width
        transform = src.transform
        profile = src.profile
    tiles = grid_tiles(transform, rows, cols, max_tile_pixels)
    print(f"scoring {rows:,} x {cols:,} px in {len(tiles)} tile(s)")

    ee = init_ee()
    model = json.loads(model_json.read_text())
    stacked = (
        probability_image(ee, model).multiply(SCORE_SCALE).round()
        .addBands(similarity_image(ee, model).add(1).multiply(SCORE_SCALE).round())
        .toUint16()
    )

    origin = (transform.c, transform.f)
    canvas = None
    tmp = out_tif.parent / ".score_tile.tif"
    for i, (window, bounds) in enumerate(tiles, start=1):
        params = tile_download_params(bounds)
        url = stacked.getDownloadURL(params)
        urllib.request.urlretrieve(url, tmp)
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
    args = parser.parse_args()
    score_statewide(args.model_json, args.strata_tif, args.out_tif,
                    args.max_tile_pixels)


if __name__ == "__main__":
    main()
