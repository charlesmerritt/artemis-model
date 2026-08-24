"""Warp Harris ownership onto the FL TreeMap grid, load streams, and score AOI windows."""
from __future__ import annotations
import os
import json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
import geopandas as gpd

REPO = Path("/home/user/artemis-model")
DATA = REPO / "data"
SCR = REPO / "research" / "leto_ca_demo"
TM_TIF = DATA / "interim/treemap5co/TreeMap2022_CONUS_5FlCntys.tif"
OWN_VSI = "/vsis3/artemis-r2/data/RDS-2025-0045/Data/US_forest_ownership.tif"
STREAMS = DATA / "interim/management_units_pilot/streams_5070.gpkg"
BUFFERS = DATA / "interim/management_units_pilot/stream_bmp_buffers.gpkg"
CACHE = SCR / "own_aoi_full.npy"

def warp_ownership(tm):
    if CACHE.exists():
        return np.load(CACHE)
    from rasterio.session import AWSSession
    endpoint = os.environ["RCLONE_CONFIG_R2_ENDPOINT"]
    os.environ["AWS_S3_ENDPOINT"] = endpoint.replace("https://", "")
    os.environ["AWS_VIRTUAL_HOSTING"] = "FALSE"
    os.environ["AWS_REGION"] = "auto"
    os.environ["AWS_HTTPS"] = "YES"
    os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
    os.environ["VSI_CACHE"] = "TRUE"
    session = AWSSession(aws_access_key_id=os.environ["RCLONE_CONFIG_R2_ACCESS_KEY_ID"],
                         aws_secret_access_key=os.environ["RCLONE_CONFIG_R2_SECRET_ACCESS_KEY"],
                         region_name="auto")
    out = np.zeros((tm.height, tm.width), dtype=np.int16)
    with rasterio.Env(session=session), rasterio.open(OWN_VSI) as src:
        reproject(source=rasterio.band(src, 1), destination=out,
                  src_transform=src.transform, src_crs=src.crs,
                  dst_transform=tm.transform, dst_crs=tm.crs, resampling=Resampling.nearest)
    np.save(CACHE, out)
    return out

def main():
    with rasterio.open(TM_TIF) as tm:
        tmid = tm.read(1)
        transform = tm.transform
        crs = tm.crs
        own = warp_ownership(tm)
    forest = tmid > 0  # valid TreeMap = forest tree list present
    print("forest pixels:", int(forest.sum()), "of", forest.size)
    # owner classes present on forest
    vals, cnts = np.unique(own[forest], return_counts=True)
    print("ownership on forest:", dict(zip(vals.tolist(), cnts.tolist())))

    # rasterize stream presence onto the grid (for AOI scoring)
    streams = gpd.read_file(STREAMS).to_crs(crs)
    print("streams:", len(streams), "crs", streams.crs)
    from rasterio.features import rasterize
    stream_ras = rasterize(((g, 1) for g in streams.geometry if g is not None),
                           out_shape=tmid.shape, transform=transform, fill=0, dtype="uint8")
    print("stream pixels:", int(stream_ras.sum()))

    # scan windows: want mixed ownership (3+ real owner classes 3-8), stream present, forest-rich
    H, W = tmid.shape
    win = 120  # 120 px * 30 m = 3.6 km box
    step = 20
    best = []
    owners_real = {3, 4, 5, 6, 7, 8}
    for r0 in range(0, H - win, step):
        for c0 in range(0, W - win, step):
            fmask = forest[r0:r0+win, c0:c0+win]
            fcount = int(fmask.sum())
            if fcount < win * win * 0.45:
                continue
            ow = own[r0:r0+win, c0:c0+win][fmask]
            ow_collapsed = np.where(np.isin(ow, [1, 2]), 0, ow)
            real_frac = float(np.isin(ow_collapsed, list(owners_real)).sum()) / max(1, fcount)
            if real_frac < 0.80:   # forest pixels must mostly carry a real owner class
                continue
            classes = owners_real.intersection(np.unique(ow_collapsed).tolist())
            n_owner = len(classes)
            if n_owner < 3:
                continue
            spx = int(stream_ras[r0:r0+win, c0:c0+win].sum())
            if spx < 25:
                continue
            # balance: reward real-owner coverage + diversity + stream + a minority class
            counts = {int(k): int((ow_collapsed == k).sum()) for k in classes}
            minor = min(counts.values()) / max(1, fcount)
            score = real_frac * 1000 + n_owner * 60 + min(spx, 300) * 0.3 + minor * 400
            best.append((round(score, 1), r0, c0, n_owner, spx, fcount, round(real_frac, 2), counts))
    best.sort(reverse=True)
    print("\ntop AOI candidates (score, r0, c0, n_owner, stream_px, forest_px, owner_counts):")
    for b in best[:12]:
        print(b)
    if best:
        _, r0, c0, *_ = best[0]
        out = dict(r0=int(r0), c0=int(c0), win=int(win))
        (SCR / "aoi.json").write_text(json.dumps(out))
        print("\nwrote", SCR / "aoi.json", out)

if __name__ == "__main__":
    main()
