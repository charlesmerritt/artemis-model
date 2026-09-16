"""Stage inputs from the artemis-r2 bucket, for either region.

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
    county_p010g.shp_nt00934/                        raw.counties (--region full only, for
                                                     the five-county union polygon)

The two CONUS rasters are read as windows over the region directly from R2 via
GDAL /vsis3/ (they are 4.8 GB and 3.9 GB; --region aoi needs ~2 MB of each,
--region full needs the five-county bounding box), the ownership raster is
snapped to the TreeMap grid with nearest-neighbour resampling (categorical),
and the rest is copied with rclone.

--region full also rasterizes a county-membership mask at the TreeMap grid:
the five counties are not a rectangle, so clipping to their bounding box pulls
in slivers of neighbouring counties along the edges. 03_ca_segment.py ANDs
this mask into its valid-cell mask so segmentation never crosses the pilot
boundary.

Usage:
    uv run python experiments/2026-08-24_leto-ca-forest-viz/01_stage_aoi.py
    uv run python experiments/2026-08-24_leto-ca-forest-viz/01_stage_aoi.py --region full
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    AOI_BOUNDS_4269,
    COUNTIES_SHP,
    FIA_DB,
    OWNERSHIP_NODATA,
    PILOT_COUNTY_FIPS,
    SHARED_STAGE_ROOT,
    STREAMS_SHP,
    TREEMAP_VAT_DBF,
    region_paths,
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


def rclone_copyto(remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if local_path.exists():
        print(f"already staged: {local_path.name}")
        return
    subprocess.run(["rclone", "copyto", f"{R2}/{remote_path}", str(local_path)], check=True)
    print(f"staged {local_path.name}")


def rclone_copy_dir(remote_dir: str, local_dir: Path) -> None:
    if local_dir.exists() and any(local_dir.iterdir()):
        print(f"already staged: {local_dir.name}/")
        return
    local_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rclone", "copy", f"{R2}/{remote_dir}", str(local_dir)], check=True)
    print(f"staged {local_dir.name}/")


def five_county_union(paths) -> tuple[tuple[float, float, float, float], "object"]:
    """The five-county union polygon (EPSG:5070) and its bounds (EPSG:4269).

    Cached to `paths.counties_gpkg` — building it needs the ~50 MB national
    county shapefile, which is otherwise only used for this one bounding box
    and mask.
    """
    import geopandas as gpd

    if paths.counties_gpkg.exists():
        union = gpd.read_file(paths.counties_gpkg)
    else:
        rclone_copy_dir("county_p010g.shp_nt00934/", COUNTIES_SHP.parent)
        counties = gpd.read_file(COUNTIES_SHP)
        pilot = counties[counties["ADMIN_FIPS"].isin(PILOT_COUNTY_FIPS)]
        if len(pilot) != len(PILOT_COUNTY_FIPS):
            raise RuntimeError(
                f"expected {len(PILOT_COUNTY_FIPS)} pilot counties, matched {len(pilot)}"
            )
        union = pilot.dissolve(by="ADMIN_FIPS", as_index=False).to_crs("EPSG:5070")
        paths.stage_root.mkdir(parents=True, exist_ok=True)
        union.to_file(paths.counties_gpkg, driver="GPKG")
    bounds_4269 = tuple(union.to_crs("EPSG:4269").total_bounds)
    return bounds_4269, union


def clip_rasters(bounds_4269, paths) -> None:
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    b5070 = transform_bounds("EPSG:4269", "EPSG:5070", *bounds_4269)
    with rasterio.open("/vsis3/artemis-r2/data/TreeMap-2022/Data/TreeMap2022_CONUS.tif") as src:
        w = from_bounds(*b5070, src.transform)
        win = Window(math.floor(w.col_off), math.floor(w.row_off),
                     math.ceil(w.width), math.ceil(w.height))
        tm = src.read(1, window=win)
        tr = src.window_transform(win)
        profile = dict(driver="GTiff", height=tm.shape[0], width=tm.shape[1], count=1,
                       dtype="uint32", crs=src.crs, transform=tr, nodata=src.nodata,
                       compress="lzw")
        with rasterio.open(paths.treemap_tif, "w", **profile) as dst:
            dst.write(tm, 1)
    print(f"TreeMap clip {tm.shape} ({tm.size:,} cells), "
          f"{int((tm != profile['nodata']).sum()):,} forested")

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
    with rasterio.open(paths.ownership_tif, "w", **profile) as dst:
        dst.write(own, 1)
    vals, counts = np.unique(own, return_counts=True)
    print("ownership counts:", dict(zip(vals.tolist(), counts.tolist())))

    paths.stage_root.mkdir(parents=True, exist_ok=True)
    meta = {"transform": [tr.a, tr.b, tr.c, tr.d, tr.e, tr.f], "shape": list(tm.shape)}
    paths.meta_json.write_text(json.dumps(meta))
    return tr, tm.shape


def rasterize_county_mask(union, transform, shape, paths) -> None:
    import rasterio
    from rasterio.features import rasterize

    mask = rasterize(((geom, 1) for geom in union.geometry), out_shape=shape,
                     transform=transform, fill=0, dtype="uint8")
    profile = dict(driver="GTiff", height=shape[0], width=shape[1], count=1,
                   dtype="uint8", crs="EPSG:5070", transform=transform,
                   nodata=0, compress="lzw")
    with rasterio.open(paths.county_mask_tif, "w", **profile) as dst:
        dst.write(mask, 1)
    inside = int(mask.sum())
    print(f"county mask: {inside:,} of {mask.size:,} cells inside the five-county "
          f"union ({100 * inside / mask.size:.1f}%)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=("aoi", "full"), default="aoi")
    args = parser.parse_args()
    paths = region_paths(args.region)

    SHARED_STAGE_ROOT.mkdir(parents=True, exist_ok=True)
    configure_gdal_s3_env()

    rclone_copyto("TreeMap-2022/Data/TreeMap2022_CONUS.tif.vat.dbf", TREEMAP_VAT_DBF)
    rclone_copyto("Lowe_TreeMap_Chaz/output/FIA_5county_consolidated.db", FIA_DB)

    streams_zip = SHARED_STAGE_ROOT / "FL_5_Co_Streams.zip"
    rclone_copyto("FL_5_Co_Streams.zip", streams_zip)
    if not STREAMS_SHP.exists():
        with zipfile.ZipFile(streams_zip) as zf:
            zf.extractall(SHARED_STAGE_ROOT / "FL_5_Co_Streams")

    required = [paths.treemap_tif, paths.ownership_tif, paths.meta_json]
    if args.region == "full":
        required.append(paths.county_mask_tif)
    if all(p.exists() for p in required):
        print(f"[{args.region}] rasters already staged")
        return

    if args.region == "full":
        bounds_4269, union = five_county_union(paths)
        print(f"five-county union bounds (4269): {bounds_4269}")
        tr, shape = clip_rasters(bounds_4269, paths)
        rasterize_county_mask(union, tr, shape, paths)
    else:
        clip_rasters(AOI_BOUNDS_4269, paths)


if __name__ == "__main__":
    main()
