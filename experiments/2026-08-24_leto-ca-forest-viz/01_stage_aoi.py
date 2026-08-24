"""Stage the AOI inputs from the artemis-r2 bucket.

Everything comes from the same sources the production configs declare
(config/data_paths.yaml, data/index.md) and the LETO/Chaz provenance scripts
(`Lowe_TreeMap_Chaz/scripts/01..07`) used to build the FVS-ready database:

    TreeMap-2022/Data/TreeMap2022_CONUS.tif          raw.treemap_2022 (RDS-2025-0032)
    TreeMap-2022/Data/TreeMap2022_CONUS.tif.vat.dbf  TreeMap VAT: TM_ID -> PLT_CN,
                                                     FORTYPCD, BALIVE, QMD, TPA_LIVE
    RDS-2025-0045/Data/US_forest_ownership.tif       raw.ownership (Harris et al. 2025)
    Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db
                                                     FVS-ready FIA SQLite for every plot
                                                     referenced by TreeMap 2022 in the
                                                     five-county pilot (scripts 02-05)
    FL_5_Co_Streams.zip                              EPA NHDPlus 2022 streams, five counties

The two CONUS rasters are read as windows over the AOI directly from R2 via
GDAL /vsis3/ (they are 4.8 GB and 3.9 GB; the AOI needs ~2 MB of each), the
ownership raster is snapped to the TreeMap grid with nearest-neighbour
resampling (categorical), and the rest is copied with rclone.

Usage:
    uv run python experiments/2026-08-24_leto-ca-forest-viz/01_stage_aoi.py
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import zipfile

import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from common import (
    AOI_BOUNDS_4269,
    AOI_OWNERSHIP_TIF,
    AOI_TREEMAP_TIF,
    FIA_DB,
    OWNERSHIP_NODATA,
    STAGE_ROOT,
    STREAMS_SHP,
    TREEMAP_VAT_DBF,
)

R2 = "r2:artemis-r2/data"


def configure_gdal_s3_env() -> None:
    """Point GDAL's /vsis3/ at the R2 endpoint using the rclone credentials."""
    endpoint = os.environ["RCLONE_CONFIG_R2_ENDPOINT"]
    os.environ.update(
        AWS_S3_ENDPOINT=endpoint.replace("https://", ""),
        AWS_ACCESS_KEY_ID=os.environ["RCLONE_CONFIG_R2_ACCESS_KEY_ID"],
        AWS_SECRET_ACCESS_KEY=os.environ["RCLONE_CONFIG_R2_SECRET_ACCESS_KEY"],
        AWS_VIRTUAL_HOSTING="FALSE",
        AWS_REGION="auto",
    )


def rclone_copyto(remote_path: str, local_path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if local_path.exists():
        print(f"already staged: {local_path.name}")
        return
    subprocess.run(["rclone", "copyto", f"{R2}/{remote_path}", str(local_path)], check=True)
    print(f"staged {local_path.name}")


def clip_rasters() -> None:
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    b5070 = transform_bounds("EPSG:4269", "EPSG:5070", *AOI_BOUNDS_4269)
    with rasterio.open("/vsis3/artemis-r2/data/TreeMap-2022/Data/TreeMap2022_CONUS.tif") as src:
        w = from_bounds(*b5070, src.transform)
        win = Window(math.floor(w.col_off), math.floor(w.row_off),
                     math.ceil(w.width), math.ceil(w.height))
        tm = src.read(1, window=win)
        tr = src.window_transform(win)
        profile = dict(driver="GTiff", height=tm.shape[0], width=tm.shape[1], count=1,
                       dtype="uint32", crs=src.crs, transform=tr, nodata=src.nodata,
                       compress="lzw")
        with rasterio.open(AOI_TREEMAP_TIF, "w", **profile) as dst:
            dst.write(tm, 1)
    print(f"TreeMap clip {tm.shape}, {int((tm != src.nodata).sum())} forested cells")

    own = np.full(tm.shape, OWNERSHIP_NODATA, dtype=np.uint8)
    with rasterio.open("/vsis3/artemis-r2/data/RDS-2025-0045/Data/US_forest_ownership.tif") as osrc:
        ob = transform_bounds("EPSG:5070", "EPSG:4269",
                              tr.c, tr.f + tr.e * tm.shape[0],
                              tr.c + tr.a * tm.shape[1], tr.f)
        ow = from_bounds(*ob, osrc.transform)
        owin = Window(math.floor(ow.col_off) - 2, math.floor(ow.row_off) - 2,
                      math.ceil(ow.width) + 4, math.ceil(ow.height) + 4)
        oarr = osrc.read(1, window=owin)
        reproject(oarr, own,
                  src_transform=osrc.window_transform(owin), src_crs=osrc.crs,
                  src_nodata=OWNERSHIP_NODATA,
                  dst_transform=tr, dst_crs="EPSG:5070",
                  dst_nodata=OWNERSHIP_NODATA, resampling=Resampling.nearest)
    profile.update(dtype="uint8", nodata=OWNERSHIP_NODATA)
    with rasterio.open(AOI_OWNERSHIP_TIF, "w", **profile) as dst:
        dst.write(own, 1)
    vals, counts = np.unique(own, return_counts=True)
    print("ownership counts:", dict(zip(vals.tolist(), counts.tolist())))

    meta = {"transform": [tr.a, tr.b, tr.c, tr.d, tr.e, tr.f], "shape": list(tm.shape)}
    (STAGE_ROOT / "aoi_meta.json").write_text(json.dumps(meta))


def main() -> None:
    STAGE_ROOT.mkdir(parents=True, exist_ok=True)
    configure_gdal_s3_env()

    rclone_copyto("TreeMap-2022/Data/TreeMap2022_CONUS.tif.vat.dbf", TREEMAP_VAT_DBF)
    rclone_copyto("Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db", FIA_DB)

    streams_zip = STAGE_ROOT / "FL_5_Co_Streams.zip"
    rclone_copyto("FL_5_Co_Streams.zip", streams_zip)
    if not STREAMS_SHP.exists():
        with zipfile.ZipFile(streams_zip) as zf:
            zf.extractall(STAGE_ROOT / "FL_5_Co_Streams")

    if not (AOI_TREEMAP_TIF.exists() and AOI_OWNERSHIP_TIF.exists()):
        clip_rasters()
    else:
        print("AOI rasters already staged")


if __name__ == "__main__":
    main()
