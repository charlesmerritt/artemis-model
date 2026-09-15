"""Reproject the Harris/NWOS forest-ownership raster onto the Florida repair grid.

``RDS-2025-0045`` publishes ``US_forest_ownership.tif`` as a ~10 m geographic
raster (values 0 unknown, 3 family, 4 corporate, 5 tribal, 6 federal, 7 state,
8 local, 15 nodata — the only ownership vocabulary ARTEMIS reads, see
``config/ownership_policy.yaml``). Downstream stages need it on the same
30 m EPSG:5070 grid as the repaired TreeMap, pixel-co-registered.

This module warps the Florida window to that grid, nearest-neighbour (the
values are categorical — resampling them any other way invents classes), in
destination tiles so no full-state array ever lives in the source CRS. It
produces a *prepared* layer: the classes are exactly the published ones, on
the right grid. The ownership repair is a separate stage; nothing here changes
a class.

Output: ``data/processed/statewide_repair/us_forest_ownership_fl.tif``
plus the raster's VAT DBF sidecar.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
from rasterio.windows import Window, from_bounds

from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL
from pipeline.spatial_ref import project_crs
from pipeline.s1_initial_state.statewide_repair import CACHE, OUT_DIR
from pipeline.s3_management.owner_classes import load_ownership_policy

OWNERSHIP_TIF = CACHE / "RDS-2025-0045/Data/US_forest_ownership.tif"
OWNERSHIP_VAT = CACHE / "RDS-2025-0045/Data/US_forest_ownership.tif.vat.dbf"
GRID_TIF = OUT_DIR / "treemap2022_fl_repaired.tif"


def reproject_tile(src_path: Path, tile: Window, grid_transform, dst: np.ndarray) -> None:
    """Warp the source region under one destination tile into `dst` (in place).

    ``grid_transform`` is the full-window transform; the tile's own transform
    (its origin shifted to the tile's top-left corner) is derived here so the
    destination block is georeferenced correctly.
    """
    tile_transform = grid_transform * rasterio.Affine.translation(tile.col_off, tile.row_off)
    bounds = rasterio.windows.bounds(tile, grid_transform)  # bounds() applies the offsets itself
    src_bounds = rasterio.warp.transform_bounds(project_crs(), src_crs_of(src_path), *bounds)
    with rasterio.open(src_path) as src:
        window = from_bounds(*src_bounds, src.transform).round_offsets().round_lengths()
        try:
            window = window.intersection(Window(0, 0, src.width, src.height))
        except rasterio.errors.WindowError:
            dst[:] = src.nodata
            return
        if window.width <= 0 or window.height <= 0:
            dst[:] = src.nodata
            return
        source = src.read(1, window=window)
        src_transform = src.window_transform(window)
        src_nodata = src.nodata

    rasterio.warp.reproject(
        source=source,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs_of(src_path),
        src_nodata=src_nodata,
        dst_transform=tile_transform,
        dst_crs=project_crs(),
        dst_nodata=src_nodata,
        resampling=rasterio.warp.Resampling.nearest,
    )


_CRS_CACHE: dict[Path, str] = {}


def src_crs_of(path: Path) -> str:
    if path not in _CRS_CACHE:
        with rasterio.open(path) as src:
            _CRS_CACHE[path] = src.crs.to_string()
    return _CRS_CACHE[path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ownership-tif", type=Path, default=OWNERSHIP_TIF)
    parser.add_argument("--ownership-vat", type=Path, default=OWNERSHIP_VAT)
    parser.add_argument("--grid-tif", type=Path, default=GRID_TIF,
                        help="any raster defining the target grid window")
    parser.add_argument("--out", type=Path, default=OUT_DIR / "us_forest_ownership_fl.tif")
    parser.add_argument("--tile", type=int, default=2048)
    args = parser.parse_args()

    with rasterio.open(args.grid_tif) as grid:
        rows, cols = grid.height, grid.width
        transform, crs = grid.transform, grid.crs
    with rasterio.open(args.ownership_tif) as src:
        nodata = src.nodata

    profile = {
        "driver": "GTiff", "height": rows, "width": cols, "count": 1,
        "dtype": "uint8", "crs": crs, "transform": transform, "nodata": nodata,
        "compress": "lzw", "tiled": True, "blockxsize": 512, "blockysize": 512,
    }
    with rasterio.open(args.out, "w", **profile) as dst:
        for row0 in range(0, rows, args.tile):
            for col0 in range(0, cols, args.tile):
                r1, c1 = min(row0 + args.tile, rows), min(col0 + args.tile, cols)
                tile = Window(col0, row0, c1 - col0, r1 - row0)
                block = np.full((r1 - row0, c1 - col0), nodata, dtype="uint8")
                reproject_tile(args.ownership_tif, tile, transform, block)
                dst.write(block, 1, window=tile)
            print(f"rows {row0:,}–{r1:,} done", flush=True)
    if args.ownership_vat.exists():
        # ArcGIS-style VAT sidecar, so the class names travel with the raster.
        shutil.copy2(args.ownership_vat, Path(str(args.out) + ".vat.dbf"))

    hist: dict[int, int] = {}
    with rasterio.open(args.out) as done:
        for row0 in range(0, done.height, 4096):
            values = done.read(
                1, window=Window(0, row0, done.width, min(4096, done.height - row0))
            )
            ids, counts = np.unique(values, return_counts=True)
            for k, c in zip(ids, counts):
                hist[int(k)] = hist.get(int(k), 0) + int(c)
    acres = {k: round(v * ACRES_PER_PIXEL, 1) for k, v in sorted(hist.items())}
    masked = set(load_ownership_policy()["masked_harris_values"])
    summary = {
        "acres_per_pixel": ACRES_PER_PIXEL,
        "owner_class_acres": {k: a for k, a in acres.items() if k not in masked and k != 15},
        "masked_acres": {k: a for k, a in acres.items() if k in masked},
        "outside_product_acres": acres.get(15),
        "note": (
            "values are the published Harris/NWOS raster classes, warped "
            "nearest-neighbour; kept raw — config/ownership_policy.yaml masks them "
            "(1 non-forest, 2 water) via owner_classes.MASKED. Legend resolved "
            "against the product's own metadata, see tests/test_ownership_policy.py"
        ),
    }
    (args.out.parent / (args.out.stem + "_summary.json")).write_text(json.dumps(summary, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
