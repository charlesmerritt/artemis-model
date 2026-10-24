"""Step 1: Hansen GFC lossyear and treecover2000 on Florida's TreeMap grid.

Warps the GFC-2025-v1.13 tiles in `common.HANSEN_DIR` (EPSG:4326, 0.00025°) with nearest
neighbour onto `common.florida_grid()`, writing one two-band GeoTIFF, `common.HANSEN_FL_TIF`:
band 1 lossyear, band 2 treecover2000, the layout `county_improvement.read_on_grid` reads.
Outside the tiles both bands are 0: no loss, no cover.

The FL5 runs read an Earth Engine `computePixels` export of the same dataset
(`common.HANSEN_AOI_TIF`). The script compares the two on that export's window, band by band
and through each recipe's Hansen rule, and writes the counts with the wall time and peak RSS to
`outputs/01_hansen.json`.

    uv run python experiments/2026-10-12_statewide-v1-v4/01_hansen.py
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
sys.path[:0] = [str(EXP), str(REPO)]

import common  # noqa: E402
from pipeline.s1_initial_state import county_improvement as ci  # noqa: E402

LAYERS = ("lossyear", "treecover2000")


def tiles(layer: str) -> list[Path]:
    found = sorted(common.HANSEN_DIR.glob(f"Hansen_GFC-2025-v1.13_{layer}_*.tif"))
    if len(found) != 2:
        raise FileNotFoundError(f"expected the 30N_090W and 40N_090W {layer} tiles, found {found}")
    return found


def warp_to_grid(sources: Mapping[str, Sequence[Path]], dst: Path, grid: common.Grid,
                 threads: int = 8) -> None:
    """Nearest-neighbour warp of each layer's tiles into one band of `dst` on `grid`.

    The tiles do not overlap, so each warps into the same band in turn. The destination is not
    reinitialised between tiles (`init_dest_nodata=False`): a tile leaves pixels outside its own
    footprint as the previous tile wrote them. `tolerance=0` makes GDAL transform every pixel
    centre exactly. Its default approximate transformer (0.125 px) sampled a neighbouring source
    pixel for 3.3 % of treecover2000 on the FL5 window; exact centres match the EE export.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(driver="GTiff", width=grid.width, height=grid.height, count=len(LAYERS),
                   dtype="uint8", crs="EPSG:5070", transform=grid.transform, tiled=True,
                   blockxsize=512, blockysize=512, compress="deflate", predictor=2,
                   BIGTIFF="IF_SAFER")
    with rasterio.open(dst, "w", **profile) as out:
        for band, layer in enumerate(LAYERS, 1):
            out.set_band_description(band, layer)
            for path in sources[layer]:
                with rasterio.open(path) as src:
                    reproject(rasterio.band(src, 1), rasterio.band(out, band),
                              resampling=Resampling.nearest, init_dest_nodata=False,
                              tolerance=0, num_threads=threads, warp_mem_limit=1024)


def _rule(bands: np.ndarray, years: tuple[int, int]) -> np.ndarray:
    loss, cover = bands
    return (loss >= years[0]) & (loss <= years[1]) & (cover >= ci.HANSEN_TREECOVER_MIN)


def agreement(statewide: np.ndarray, aoi: np.ndarray) -> dict:
    """Pixel differences per band and per recipe's Hansen rule; both arrays (2, rows, cols)."""
    out = {layer: {"pixels_differ": int((statewide[i] != aoi[i]).sum())}
           for i, layer in enumerate(LAYERS)}
    for recipe, years in common.HANSEN_LOSSYEARS.items():
        a, s = _rule(aoi, years), _rule(statewide, years)
        out[f"rule_{recipe}"] = {"aoi": int(a.sum()), "statewide": int(s.sum()),
                                 "both": int((a & s).sum())}
    return out


def main() -> None:
    record: dict = {}
    grid = common.florida_grid()
    with common.measured("warp", record):
        warp_to_grid({layer: tiles(layer) for layer in LAYERS}, common.HANSEN_FL_TIF, grid,
                     threads=common.workers(8))
    with rasterio.open(common.HANSEN_AOI_TIF) as src:
        aoi = src.read([1, 2])
        window = ci.Grid(src.transform, (src.height, src.width))
    statewide = ci.read_on_grid(common.HANSEN_FL_TIF, window, fill=0, indexes=[1, 2])
    record["grid"] = {"west": grid.west, "north": grid.north, "width": grid.width,
                      "height": grid.height}
    record["aoi_pixels"] = int(aoi[0].size)
    record["agreement_with_fl5_ee_export"] = agreement(statewide, aoi)
    record["output"] = str(common.HANSEN_FL_TIF.relative_to(common.EXP))
    common.write_json(common.OUTPUTS / "01_hansen.json", record)
    print(record)


if __name__ == "__main__":
    main()
