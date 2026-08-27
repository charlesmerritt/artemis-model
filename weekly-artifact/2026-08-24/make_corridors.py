"""
Riparian buffers as stands in their own right.

The design intent, stated as a picture: a BMP buffer is not an attribute of the stand it
happens to fall in. It **cuts** every stand it crosses, and the corridor it carves out
becomes one stand of its own — grow-only, never entered — with the old boundaries erased
inside it. There is no threshold anywhere in that sentence: a stand is riparian or it is
not, and the geometry decides.

`pipeline/s3_management/sketch_management_units.py` already implements exactly this for
parcel-derived polygons (`unit_class = "riparian"`, retained not erased, and
`sliver_merge.py` treats the class as a hard constraint so buffer acres can never be
absorbed back into a harvest unit). What has never been measured is what that carving does
to the *scheduling* landscape the 2026-08-10 schedule and the 2026-08-17 library were built
on. This driver measures it:

  1. Label every contiguous run of attributed riparian forest on the TreeMap grid — these
     are the corridors, the stands the buffer creates.
  2. Count what each corridor cuts: how many pre-existing scheduling units it crosses, how
     many TreeMap plots, owner classes and counties it spans.
  3. Report the corridor layer both ways — dissolved by contiguity alone, and subdivided by
     the attributes a stand cannot straddle (tree list, ownership, county) — because a
     corridor that spans two owners cannot be one stand however clean the geometry is.

Contiguity is 8-connected on 30 m pixels: two riparian pixels touching at a corner are the
same corridor, which is the reading that keeps a diagonal stream reach in one piece.

Usage (from the repo root, with the R2 inputs staged under ./data):

    uv run python weekly-artifact/2026-08-24/make_corridors.py
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("make_corridors")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"
OWNER_GRID_CACHE = DATA / "interim/owner_grid_treemap.npy"   # gitignored


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from pipeline.s3_management.sketch_management_units import (  # noqa: E402
    MIN_UNIT_AREA_HA,
    SQ_M_PER_ACRE,
)

OVERLAY = _load("weekly-artifact/2026-08-24/make_riparian_overlay.py", "overlay_20260824")
PRIOR_10 = OVERLAY.PRIOR_10


def grids() -> dict:
    """Every grid this driver needs, co-registered on the TreeMap raster."""
    import geopandas as gpd
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import rasterize
    from rasterio.vrt import WarpedVRT

    with rasterio.open(PRIOR_10.TREEMAP_TIF) as src:
        tm = src.read(1)
        profile = src.profile
        nodata = src.nodata
        bounds = src.bounds

    counties = OVERLAY.aoi_counties().to_crs(profile["crs"])
    county_codes = {name: i + 1 for i, name in enumerate(sorted(counties["NAME"]))}
    county_grid = rasterize(
        ((geom, county_codes[name]) for geom, name in zip(counties.geometry, counties["NAME"])),
        out_shape=tm.shape, transform=profile["transform"], fill=0, dtype="uint8",
    )

    buffers = gpd.read_file(OVERLAY.SMZ_LAYER_CACHE).to_crs(profile["crs"])
    smz_codes = {cls: i + 1 for i, cls in enumerate(OVERLAY.RIPARIAN_BUFFER_PRIORITY)}
    smz_grid = rasterize(
        ((geom, smz_codes[cls]) for geom, cls in zip(buffers.geometry, buffers["buffer_class"])),
        out_shape=tm.shape, transform=profile["transform"], fill=0, dtype="uint8",
    )

    if OWNER_GRID_CACHE.exists():
        log.info("Reusing cached ownership grid %s", OWNER_GRID_CACHE)
        owner_grid = np.load(OWNER_GRID_CACHE)
    else:
        PRIOR_10._r2_gdal_env()
        log.info("Warping the Harris 2025 ownership raster onto the TreeMap grid (R2, windowed)")
        with rasterio.open(PRIOR_10.OWNERSHIP_VSI) as osrc:
            with WarpedVRT(osrc, crs=profile["crs"], transform=profile["transform"],
                           width=profile["width"], height=profile["height"],
                           resampling=Resampling.nearest) as vrt:
                owner_grid = vrt.read(1)
        OWNER_GRID_CACHE.parent.mkdir(parents=True, exist_ok=True)
        np.save(OWNER_GRID_CACHE, owner_grid)

    forest = (tm != nodata) & (county_grid > 0)
    attributed = forest & np.isin(owner_grid, list(PRIOR_10.OWNER_CLASSES))
    return {
        "tm": tm, "profile": profile, "bounds": bounds, "counties": counties,
        "county_grid": county_grid, "county_codes": county_codes, "smz": smz_grid,
        "smz_codes": smz_codes, "owner": owner_grid, "forest": forest,
        "attributed": attributed, "buffers": buffers,
    }


def label_corridors(g: dict) -> tuple[np.ndarray, pd.DataFrame]:
    """Contiguous riparian forest -> corridor stands, with what each one cuts."""
    from scipy import ndimage

    riparian = g["attributed"] & (g["smz"] > 0)
    structure = np.ones((3, 3), dtype=bool)          # 8-connectivity
    labels, n = ndimage.label(riparian, structure=structure)
    log.info("Riparian corridors: %d contiguous stands over %d pixels", n, int(riparian.sum()))

    idx = np.flatnonzero(riparian.ravel())
    code_to_county = {v: k for k, v in g["county_codes"].items()}
    flat = pd.DataFrame({
        "corridor_id": labels.ravel()[idx],
        "tm_id": g["tm"].ravel()[idx].astype("int64"),
        "county": pd.Series(g["county_grid"].ravel()[idx]).map(code_to_county).to_numpy(),
        "owner_class": g["owner"].ravel()[idx],
        "buffer_code": g["smz"].ravel()[idx],
    })
    flat["unit_id"] = ("TM" + flat["tm_id"].astype(str) + "_" + flat["county"]
                       + "_O" + flat["owner_class"].astype(str))

    corridors = (
        flat.groupby("corridor_id")
        .agg(pixels=("tm_id", "size"),
             units_cut=("unit_id", "nunique"),
             plots=("tm_id", "nunique"),
             owner_classes=("owner_class", "nunique"),
             counties=("county", "nunique"))
        .reset_index()
    )
    corridors["acres"] = corridors["pixels"] * PRIOR_10.ACRES_PER_PIXEL
    log.info("Corridor acreage %.0f ac; median %.2f ac; largest %.0f ac",
             corridors["acres"].sum(), corridors["acres"].median(), corridors["acres"].max())
    corridors = corridors.merge(
        flat.groupby("corridor_id")["county"].agg(lambda s: ";".join(sorted(set(s)))).reset_index(),
        on="corridor_id")
    return labels, corridors, flat


def summarize(corridors: pd.DataFrame, flat: pd.DataFrame, g: dict) -> dict[str, pd.DataFrame]:
    total_ac = corridors["acres"].sum()

    # A riparian stand may draw its tree list from many TreeMap plots — `assign_plt_cn`
    # imputes a unit from the area-weighted mix of the plots inside it, which is the
    # documented design. So plot heterogeneity is NOT a reason to cut a corridor. What a
    # stand genuinely cannot straddle is ownership (a different owner is a different
    # decision-maker) and county (TPO caps are per county).
    stand_key = ["corridor_id", "county", "owner_class"]
    stand_ac = flat.groupby(stand_key).size() * PRIOR_10.ACRES_PER_PIXEL
    # The repo's own minimum unit area, converted rather than restated.
    min_unit_ac = MIN_UNIT_AREA_HA * 10_000 / SQ_M_PER_ACRE

    spans = pd.DataFrame([
        {"reading": "contiguity alone (the corridor as drawn)",
         "stands": len(corridors), "acres": total_ac,
         "note": "one stand per contiguous run of riparian forest"},
        {"reading": "corridors spanning >1 ownership class",
         "stands": int((corridors["owner_classes"] > 1).sum()),
         "acres": corridors.loc[corridors["owner_classes"] > 1, "acres"].sum(),
         "note": "must be cut: a stand cannot straddle two owners"},
        {"reading": "corridors spanning >1 county",
         "stands": int((corridors["counties"] > 1).sum()),
         "acres": corridors.loc[corridors["counties"] > 1, "acres"].sum(),
         "note": "must be cut: TPO caps are per county"},
        {"reading": "riparian stands (corridor x county x ownership class)",
         "stands": int(len(stand_ac)), "acres": total_ac,
         "note": "the layer as it would enter the scheduler"},
        {"reading": f"...of those, below MIN_UNIT_AREA_HA (2 ha = {min_unit_ac:.2f} ac)",
         "stands": int((stand_ac < min_unit_ac).sum()),
         "acres": float(stand_ac[stand_ac < min_unit_ac].sum()),
         "note": "slivers by construction; sliver_merge must never absorb them"},
        {"reading": "corridors drawing on >1 TreeMap plot",
         "stands": int((corridors["plots"] > 1).sum()),
         "acres": corridors.loc[corridors["plots"] > 1, "acres"].sum(),
         "note": "not a cut — assign_plt_cn imputes from the area-weighted plot mix"},
    ])

    bands = [(0, 0.5), (0.5, 2), (2, 10), (10, 50), (50, 1e9)]
    names = ["< 0.5 ac", "0.5–2 ac", "2–10 ac", "10–50 ac", "≥ 50 ac"]
    size = pd.DataFrame({
        "size_band": names,
        "corridors": [int(((corridors["acres"] >= lo) & (corridors["acres"] < hi)).sum())
                      for lo, hi in bands],
        "acres": [corridors.loc[(corridors["acres"] >= lo) & (corridors["acres"] < hi),
                                "acres"].sum() for lo, hi in bands],
    })
    size["share_of_riparian_acres"] = 100 * size["acres"] / total_ac

    cut = pd.DataFrame({
        "units_cut": ["1", "2", "3–5", "6–10", ">10"],
        "corridors": [
            int((corridors["units_cut"] == 1).sum()),
            int((corridors["units_cut"] == 2).sum()),
            int(corridors["units_cut"].between(3, 5).sum()),
            int(corridors["units_cut"].between(6, 10).sum()),
            int((corridors["units_cut"] > 10).sum()),
        ],
    })
    cut["acres"] = [
        corridors.loc[corridors["units_cut"] == 1, "acres"].sum(),
        corridors.loc[corridors["units_cut"] == 2, "acres"].sum(),
        corridors.loc[corridors["units_cut"].between(3, 5), "acres"].sum(),
        corridors.loc[corridors["units_cut"].between(6, 10), "acres"].sum(),
        corridors.loc[corridors["units_cut"] > 10, "acres"].sum(),
    ]
    return {"corridor_readings": spans, "corridor_size_distribution": size,
            "corridor_units_cut": cut}


def riparian_stands(flat: pd.DataFrame) -> pd.DataFrame:
    """The riparian layer as stands: corridor cut only where a stand cannot straddle.

    Ownership and county are hard boundaries; the TreeMap plot is not, because
    `assign_plt_cn` imputes a unit from the area-weighted mix of the plots inside it. The
    majority plot is carried as the stand's representative tree list, which is what the
    FVS-input builder would resolve to before weighting.
    """
    from pipeline.s4_fvs.paint_fvs_to_raster import load_crosswalk

    key = ["corridor_id", "county", "owner_class"]
    stands = (flat.groupby(key)
              .agg(pixels=("tm_id", "size"), plots=("tm_id", "nunique"),
                   majority_tm_id=("tm_id", lambda s: s.mode().iat[0]))
              .reset_index())
    stands["acres"] = stands["pixels"] * PRIOR_10.ACRES_PER_PIXEL
    stands["stand_id"] = ("RIP" + stands["corridor_id"].astype(str) + "_"
                          + stands["county"] + "_O" + stands["owner_class"].astype(str))
    xwalk = load_crosswalk(PRIOR_10.CROSSWALK).rename(columns={"tm_id": "majority_tm_id"})
    stands = stands.merge(xwalk, on="majority_tm_id", how="left")
    stands["owner_name"] = stands["owner_class"].map(
        lambda c: PRIOR_10.OWNER_CLASSES.get(c, (None, None))[0])
    log.info("Riparian stands: %d over %.0f ac (median %.2f ac); %d carry no PLT_CN",
             len(stands), stands["acres"].sum(), stands["acres"].median(),
             int(stands["PLT_CN"].isna().sum()))
    return stands[["stand_id", "corridor_id", "county", "owner_class", "owner_name",
                   "pixels", "acres", "plots", "majority_tm_id", "PLT_CN"]]


def main() -> None:
    g = grids()
    labels, corridors, flat = label_corridors(g)
    summaries = summarize(corridors, flat, g)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    corridors.sort_values("acres", ascending=False).to_csv(
        OUT_DIR / "riparian_corridors.csv", index=False)
    riparian_stands(flat).to_csv(OUT_DIR / "riparian_stands.csv", index=False)
    for name, df in summaries.items():
        df.to_csv(OUT_DIR / f"{name}.csv", index=False)
        log.info("%s\n%s", name, df.to_string(index=False))

    np.save(DATA / "interim/riparian_corridor_labels.npy", labels)
    log.info("Corridor label grid cached for the mechanic figure")


if __name__ == "__main__":
    main()
