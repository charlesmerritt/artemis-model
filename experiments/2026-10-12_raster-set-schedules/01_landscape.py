"""Step 1 — the carved pixel-class landscape for one raster set.

The 2026-10-08 schedule's stands are 2026-08-24's carved landscape: pixel classes of the
*published* TreeMap and Harris rasters, cut by the BMP buffers. This rebuilds that landscape
from any raster pair in `raster_sets` with `landscape.py`, the same counties, buffers,
crosswalk and stand ages:

* counties and BMP buffers: 2026-08-24's `aoi_counties` and `build_smz_layer` (cached
  `data/interim/smz_buffers_5070.gpkg`), rasterised onto the set's grid;
* `TM_ID -> PLT_CN, FORTYPCD`: the 2026-08-10 crosswalk;
* stand age: 2026-08-17's `stand_ages` (the FVS baseline at 2022) for a plot, and the
  establishment list's age for a scaled-young class;
* owner class and forest branch: `classify_owner` and `forest_type_branch`, as 2026-08-17.

`--raster-set published` must reproduce 2026-09-14's `carved_landscape()` stand for stand;
the check runs every time that set is built.

Writes `work/<set>/carved_stands.csv` and `work/<set>/landscape_summary.json`.

    uv run python experiments/2026-10-12_raster-set-schedules/01_landscape.py --raster-set gate-v4
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
sys.path[:0] = [str(EXP), str(REPO)]

import landscape as L  # noqa: E402
import raster_sets as RS  # noqa: E402
from pipeline.ids import as_id_series  # noqa: E402
from pipeline.s3_management.owner_classes import MASKED, classify_owner  # noqa: E402
from pipeline.s3_management.regime_assignment import forest_type_branch  # noqa: E402
from pipeline.s4_fvs.paint_fvs_to_raster import load_crosswalk  # noqa: E402

log = logging.getLogger("raster_set_landscape")

LEGACY_OWNER_CLASSES = {"private_family": "family", "private_industrial": "corporate"}

ACRES_PER_PIXEL = 900.0 / 4046.8564224
STAND_COLUMNS = ["unit_id", "tm_id", "origin", "PLT_CN", "county", "owner_class",
                 "forest_branch", "acres", "stand_age", "OWN_CODE", "FORTYPCD", "unit_class"]


def load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


OVERLAY = load("weekly-artifact/2026-08-24/make_riparian_overlay.py", "overlay_20260824")
LIB17 = load("weekly-artifact/2026-08-17/make_trajectory_library.py", "library_20260817")
TL = load("weekly-artifact/2026-09-14/make_timing_library.py", "timing_library_20260914")
PRIOR_10 = OVERLAY.PRIOR_10


def canonical_window(profile: dict) -> tuple[slice, slice, dict]:
    """Where a raster set's grid sits inside 2026-08-10's 5-county TreeMap grid.

    Counties, buffers and ownership are rasterised on that canonical grid and cropped, as
    2026-08-24 did. Rasterising the same polygons on a grid with another origin flips a few
    pixels whose centres sit on a buffer edge, which renumbers the riparian corridors.
    """
    import rasterio

    with rasterio.open(PRIOR_10.TREEMAP_TIF) as ref:
        t, r = profile["transform"], ref.transform
        if (t.a, t.e) != (r.a, r.e):
            raise AssertionError("raster set and canonical grid differ in cell size")
        col, row = (t.c - r.c) / r.a, (t.f - r.f) / r.e
        if col != int(col) or row != int(row):
            raise AssertionError(f"raster set grid is not aligned to the canonical grid: {col}, {row}")
        rows = slice(int(row), int(row) + profile["height"])
        cols = slice(int(col), int(col) + profile["width"])
        return rows, cols, ref.profile


def grids(rs: RS.RasterSet) -> dict:
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize

    with rasterio.open(rs.treemap) as src:
        tm, profile, nodata = src.read(1).astype(np.int64), src.profile, src.nodata
    rows, cols, canon = canonical_window(profile)
    shape, transform = (canon["height"], canon["width"]), canon["transform"]
    if rs is RS.PUBLISHED:
        owner = harris_on_grid(canon)[rows, cols]
    else:
        with rasterio.open(rs.ownership) as src:
            owner = src.read(1)
    prov = None
    if rs.provenance is not None:
        with rasterio.open(rs.provenance) as src:
            prov = src.read(1)

    counties = OVERLAY.aoi_counties().to_crs(profile["crs"])
    codes = {name: i + 1 for i, name in enumerate(sorted(counties["NAME"]))}
    county = rasterize(((g, codes[n]) for g, n in zip(counties.geometry, counties["NAME"])),
                       out_shape=shape, transform=transform, fill=0, dtype="uint8")[rows, cols]
    if not OVERLAY.SMZ_LAYER_CACHE.exists():
        OVERLAY.build_smz_layer()
    buffers = gpd.read_file(OVERLAY.SMZ_LAYER_CACHE).to_crs(profile["crs"])
    smz_codes = {cls: i + 1 for i, cls in enumerate(OVERLAY.RIPARIAN_BUFFER_PRIORITY)}
    smz = rasterize(((g, smz_codes[c]) for g, c in zip(buffers.geometry, buffers["buffer_class"])),
                    out_shape=shape, transform=transform, fill=0, dtype="uint8")[rows, cols]
    return {"tm": tm, "nodata": int(nodata), "prov": prov, "county": county, "owner": owner,
            "smz": smz, "county_names": {v: k for k, v in codes.items()},
            "smz_names": {v: k for k, v in smz_codes.items()}}


def harris_on_grid(profile: dict) -> np.ndarray:
    """2026-08-10's ownership grid: Harris warped nearest-neighbour onto the canonical grid."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    cache = RS.WORK / "harris_on_treemap5co_grid.npy"
    if cache.exists():
        return np.load(cache)
    with rasterio.open(RS.HARRIS_TIF) as src, WarpedVRT(
            src, crs=profile["crs"], transform=profile["transform"], width=profile["width"],
            height=profile["height"], resampling=Resampling.nearest) as vrt:
        grid = vrt.read(1)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, grid)
    return grid


def crosswalk() -> pd.DataFrame:
    xw = load_crosswalk(PRIOR_10.CROSSWALK)
    fortyp = (pd.read_csv(PRIOR_10.CROSSWALK, usecols=["Value", "FORTYPCD"])
              .rename(columns={"Value": "tm_id"}).drop_duplicates("tm_id"))
    return xw.merge(fortyp, on="tm_id", how="left")


def establishment_ages(rs: RS.RasterSet) -> dict[str, float]:
    """`Y<TM_ID>` -> the scaled list's establishment age (ADR 0002's target age, 5)."""
    path = RS.work_dir(rs) / "establishment_tree_lists.csv"
    if not path.exists():
        return {}
    lists = pd.read_csv(path, dtype={"TM_ID": str, "PLT_CN": str})
    young = lists[lists["establishment_mode"] == str(L.PixelOrigin.SCALED_YOUNG)]
    ages = young.groupby("TM_ID")["establishment_age"].agg(["min", "max"])
    if (ages["min"] != ages["max"]).any():
        raise AssertionError(f"{rs.name}: an establishment list carries two ages")
    return {L.young_stand_cn(t): float(a) for t, a in ages["min"].items()}


def attribute(stands: pd.DataFrame, xw: pd.DataFrame, ages: pd.DataFrame,
              young_ages: dict[str, float]) -> tuple[pd.DataFrame, dict]:
    before = stands["pixel_count"].sum()
    out = L.stand_plot(stands, xw)
    no_plot = before - out["pixel_count"].sum()
    out["acres"] = out["pixel_count"] * ACRES_PER_PIXEL
    out["OWN_CODE"] = out["owner_code"]
    assigned = [classify_owner({"OWN_CODE": o, "FORTYPCD": f, "SMZ_Pct": 0.0}).owner_class
                for o, f in zip(out["OWN_CODE"], out["FORTYPCD"])]
    out["owner_class"] = assigned
    masked = out["owner_class"] == MASKED
    dropped = {"acres_without_a_crosswalk_plot": round(no_plot * ACRES_PER_PIXEL, 1),
               "acres_on_masked_owners": round(float(out.loc[masked, "acres"].sum()), 1)}
    out = out[~masked].copy()
    out["forest_branch"] = [forest_type_branch({"OWN_CODE": o, "FORTYPCD": f})
                            for o, f in zip(out["OWN_CODE"], out["FORTYPCD"])]
    out = out.merge(ages, on="PLT_CN", how="left")
    young = out["origin"] == L.PixelOrigin.SCALED_YOUNG
    out.loc[young, "stand_age"] = out.loc[young, "PLT_CN"].map(young_ages)
    if out.loc[young, "stand_age"].isna().any():
        raise AssertionError("a scaled-young class has no establishment list")
    return out, dropped


def reproduce_20260914(stands: pd.DataFrame) -> dict:
    """Stand for stand against 2026-09-14's `carved_landscape()`; raises on any difference."""
    ref, _ = TL.carved_landscape()
    # 2026-08-17 wrote owner classes in its own vocabulary (`private_family`, ...); today's
    # `classify_owner` names the same Harris classes `family`, `corporate`, ...
    ref = ref.assign(PLT_CN=as_id_series(ref["PLT_CN"], column="PLT_CN"),
                     owner_class=ref["owner_class"].replace(LEGACY_OWNER_CLASSES)).set_index("unit_id")
    ours = stands.set_index("unit_id")
    if set(ref.index) != set(ours.index):
        raise AssertionError(f"unit ids differ: {len(set(ref.index) ^ set(ours.index))} not shared")
    ours = ours.loc[ref.index]
    checks = {
        "acres": float((ours["acres"] - ref["acres"]).abs().max()),
        "PLT_CN": int((ours["PLT_CN"] != ref["PLT_CN"]).sum()),
        "owner_class": int((ours["owner_class"] != ref["owner_class"]).sum()),
        "forest_branch": int((ours["forest_branch"] != ref["forest_branch"]).sum()),
        "unit_class": int((ours["unit_class"] != ref["unit_class"]).sum()),
        "stand_age": int(((ours["stand_age"] - ref["stand_age"]).abs() > 1e-9).sum()),
        "stand_age_where_published_has_none": int(ref["stand_age"].isna().sum()),
    }
    bad = {k: v for k, v in checks.items()
           if k != "stand_age_where_published_has_none" and v > (1e-6 if k == "acres" else 0)}
    if bad:
        raise AssertionError(f"published landscape does not reproduce 2026-09-14: {bad}")
    return {"stands": len(stands), **checks}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raster-set", required=True, choices=list(RS.BY_NAME))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rs = RS.BY_NAME[args.raster_set]

    g = grids(rs)
    pieces = L.pixel_pieces(g["tm"], g["nodata"], g["prov"], g["county"], g["owner"], g["smz"],
                            g["county_names"], g["smz_names"])
    upland = L.upland_remainders(pieces)
    rip = L.riparian_stands(L.riparian_pixels(g["tm"], g["nodata"], g["prov"], g["county"],
                                              g["owner"], g["smz"], g["county_names"]))
    xw = crosswalk()
    ages = LIB17.stand_ages()[["PLT_CN", "stand_age"]]
    ages["PLT_CN"] = as_id_series(ages["PLT_CN"], column="PLT_CN")
    young_ages = establishment_ages(rs)

    up, up_drop = attribute(upland, xw, ages, young_ages)
    rp, rp_drop = attribute(rip, xw, ages, young_ages)
    # 2026-08-24 attributes a riparian stand's age from the upland units' plots.
    rp["stand_age"] = rp["PLT_CN"].map(up.drop_duplicates("PLT_CN").set_index("PLT_CN")["stand_age"])
    stands = pd.concat([up, rp], ignore_index=True)
    stands["origin"] = stands["origin"].map(str)
    stands["FORTYPCD"] = stands["FORTYPCD"].where(stands["unit_class"] == "managed")
    stands["OWN_CODE"] = stands["OWN_CODE"].where(stands["unit_class"] == "managed")
    stands = stands[STAND_COLUMNS]

    summary = {
        "raster_set": rs.name, "treemap": str(rs.treemap.relative_to(REPO)),
        "ownership": (str(RS.HARRIS_TIF) + " (warped)" if rs is RS.PUBLISHED
                      else str(rs.ownership.relative_to(REPO))),
        "stands": len(stands), "acres": round(stands["acres"].sum(), 1),
        "riparian_stands": int((stands["unit_class"] == "riparian").sum()),
        "riparian_acres": round(stands.loc[stands["unit_class"] == "riparian", "acres"].sum(), 1),
        "scaled_young_acres": round(stands.loc[stands["origin"] == "scaled_young", "acres"].sum(), 1),
        "acres_by_county": stands.groupby("county")["acres"].sum().round(1).to_dict(),
        "scaled_young_acres_by_county": (stands[stands["origin"] == "scaled_young"]
                                         .groupby("county")["acres"].sum().round(1).to_dict()),
        "upland_dropped": up_drop, "riparian_dropped": rp_drop,
    }
    if rs is RS.PUBLISHED:
        summary["reproduces_20260914"] = reproduce_20260914(stands)
    out = RS.work_dir(rs)
    out.mkdir(parents=True, exist_ok=True)
    stands.to_csv(out / "carved_stands.csv", index=False)
    (out / "landscape_summary.json").write_text(json.dumps(summary, indent=2))
    log.info("%s: %s", rs.name, json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
