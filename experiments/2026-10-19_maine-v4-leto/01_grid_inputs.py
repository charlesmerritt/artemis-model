"""Step 1: the Maine grid's hole strata, and Harris ownership and Hansen on that grid.

- ``treemap_strata_me.tif``: LANDFIRE 2016/2024 strata S1-S5 over TreeMap 2022's holes inside
  Maine (land per EVT 2022), as :func:`county_improvement.improve_county` derives them per
  county. Step 2 tiles the Earth Engine scoring on this grid and only requests S3/S4 tiles.
- ``us_forest_ownership_me.tif``: RDS-2025-0045 warped nearest-neighbour onto the grid
  (:mod:`prepare_ownership_fl`, which takes any grid).
- ``hansen_me.vrt``: band 1 lossyear, band 2 treecover2000, warped nearest onto the grid.

    uv run python experiments/2026-10-19_maine-v4-leto/01_grid_inputs.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio import features

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

sys.path.insert(0, str(c.REPO))
from pipeline.s1_initial_state import county_improvement as ci  # noqa: E402
from pipeline.s1_initial_state.statewide_repair import legend_code_sets, stratify_block  # noqa: E402

BAND_ROWS = 2048


def write_strata(grid: c.Grid) -> dict:
    inputs = c.improvement_inputs()
    counties = c.maine_counties()
    with rasterio.open(c.TREEMAP_TIF) as src:
        tm_nodata, crs = src.nodata, src.crs
    codes = {y: legend_code_sets(pd.read_csv(inputs.evt(y)[1])) for y in (2016, 2022, 2024)}
    evt_nodata = {}
    for y in codes:
        with rasterio.open(inputs.evt(y)[0]) as src:
            evt_nodata[y] = src.nodata
    profile = dict(driver="GTiff", height=grid.height, width=grid.width, count=1, dtype="uint8",
                   crs=crs, transform=grid.transform, nodata=0, compress="deflate", tiled=True,
                   blockxsize=512, blockysize=512)
    counts = np.zeros(6, dtype=np.int64)
    with rasterio.open(c.STRATA_TIF, "w", **profile) as dst:
        for row0 in range(0, grid.height, BAND_ROWS):
            rows = min(BAND_ROWS, grid.height - row0)
            band = ci.Grid(grid.transform * rasterio.Affine.translation(0, row0), (rows, grid.width))
            inside = features.rasterize(((g, 1) for g in counties.geometry), out_shape=band.shape,
                                        transform=band.transform, fill=0, dtype="uint8").astype(bool)
            tm = ci.read_on_grid(c.TREEMAP_TIF, band, fill=tm_nodata)
            evt = {y: ci.read_on_grid(inputs.evt(y)[0], band, fill=evt_nodata[y]) for y in codes}
            land = (evt[2022] != codes[2022].water) & (evt[2022] >= 0) & (evt[2022] != 32767)
            hole = (tm == tm_nodata) & land & inside
            strata = stratify_block(evt[2016], evt[2024], codes[2016], codes[2024])
            strata[~hole] = 0
            dst.write(strata.astype(np.uint8), 1, window=rasterio.windows.Window(0, row0, grid.width, rows))
            counts += np.bincount(strata.ravel(), minlength=6)[:6]
            print(f"strata rows {row0:,}-{row0 + rows:,}", flush=True)
    return {f"S{k}_acres": round(float(counts[k] * ci.ACRES_PER_PIXEL), 1) for k in range(1, 6)}


def warp_hansen(grid: c.Grid) -> None:
    west, south, east, north = grid.bounds
    layers = []
    for layer in ("lossyear", "treecover2000"):
        out = c.INPUTS / f"hansen_{layer}_me.tif"
        tiles = [str(c.HANSEN_DIR / f"Hansen_GFC-2025-v1.13_{layer}_{t}.tif") for t in c.HANSEN_TILES]
        if not out.exists():
            subprocess.run(["gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:5070", "-te", str(west),
                            str(south), str(east), str(north), "-tr", "30", "30", "-r", "near",
                            "-ot", "Byte", "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES",
                            "-multi", "-wo", "NUM_THREADS=ALL_CPUS", *tiles, str(out)], check=True)
        layers.append(str(out))
    subprocess.run(["gdalbuildvrt", "-q", "-overwrite", "-separate", str(c.HANSEN_VRT), *layers],
                   check=True)


def main() -> None:
    c.INPUTS.mkdir(parents=True, exist_ok=True)
    grid = c.maine_grid()
    record = {"grid": {"west": grid.west, "north": grid.north, "width": grid.width,
                       "height": grid.height}}
    print(record, flush=True)
    with c.measured("strata", record):
        if not c.STRATA_TIF.exists():
            record["strata_acres"] = write_strata(grid)
    with c.measured("ownership", record):
        if not c.OWNERSHIP_TIF.exists():
            subprocess.run([sys.executable, "-m", "pipeline.s1_initial_state.prepare_ownership_fl",
                            "--ownership-tif", str(c.OWNERSHIP_CONUS),
                            "--ownership-vat", str(c.OWNERSHIP_CONUS) + ".vat.dbf",
                            "--grid-tif", str(c.STRATA_TIF), "--out", str(c.OWNERSHIP_TIF)],
                           check=True, cwd=c.REPO)
    with c.measured("hansen", record):
        warp_hansen(grid)
    c.write_json(c.WORK / "01_grid_inputs.json", record)
    print(record)


if __name__ == "__main__":
    main()
