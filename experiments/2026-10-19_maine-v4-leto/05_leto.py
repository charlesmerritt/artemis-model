"""Step 5: LETO stages 1-3 for every Maine county on the v4-improved TreeMap.

Each county is its own LETO study area, as LETO clips to its study area. The attribute
rasters, the CA, the riparian split and the parent donor weights all run inside the county
mask, so county lines are hard stand edges. The code path is the FL statewide run's
(``experiments/2026-10-19_fl-statewide-leto/01_segment_counties.py``), with Maine inputs:

- **Vegetation:** ``maine/young_plots/treemap2022_improved.tif`` (step 3). Each add-back pixel
  (provenance 4) holds a new ``TM_ID`` whose VAT row is its young plot's. Its ``STDAGE`` is
  the extension's ``STAND_AGE_2022`` (ADR 0003: the dated age is authoritative; the young
  plot's FIA age is not). ``ADD_BACK_SHARE`` marks these pixels per stand, and
  ``NEIGHBOUR_KEPT_SHARE`` those of them with no young plot, which carry the neighbour
  plot's BA, QMD and TPA at a young age.
- **Ownership:** ``maine/nwos2022_improved.tif``. Codes 1, 2 and nodata become Unknown, as
  LETO maps them.
- **FIA:** step 4's COND and TREE rows.
- **Riparian:** :data:`leto_riparian.MAINE_V1_RULES` (LETO v1 Maine) on NHDPlus HR
  ``NHDFlowline``, ``NHDWaterbody`` and ``NHDArea``.

**Speckle cleanup** (post-LETO, :mod:`pipeline.stand_speckle`): after the CA, every parent
under LETO's 5 ac minimum that touches another stand is absorbed into the neighbour it
shares the most edge with, and so takes that stand's modal owner. LETO's own labels are kept
as ``<fips>_seg_leto.tif``. The riparian split and donor weights run on the cleaned labels.

Outputs per county under work/counties/: <fips>.gpkg (management units), <fips>_seg.tif
(parent labels, cleaned), <fips>_seg_leto.tif (LETO's), <fips>_weights.csv, <fips>_qa.json.
Finished counties are skipped.

    uv run python experiments/2026-10-19_maine-v4-leto/05_leto.py [--workers 3] [FIPS ...]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask, shapes
from rasterio.windows import Window, from_bounds
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

sys.path.insert(0, str(c.REPO))
from pipeline import leto_attributes as la  # noqa: E402
from pipeline import leto_ca, leto_donors, leto_riparian, stand_speckle  # noqa: E402
from pipeline.ids import as_id_series, read_id_csv  # noqa: E402
from pipeline.s1_initial_state.add_back_ids import (  # noqa: E402
    EXTENSION_ID_COLUMNS,
    OUT_DIR,
    VAT_EXTENSION_CSV,
)
from pipeline.s1_initial_state.county_improvement import TreeMapProvenance  # noqa: E402

YOUNG = c.RUN_DIR / OUT_DIR
TREEMAP_TIF = YOUNG / "treemap2022_improved.tif"
PROVENANCE_TIF = YOUNG / "treemap2022_provenance.tif"
OWNERSHIP_TIF = c.RUN_DIR / "nwos2022_improved.tif"
VAT_DBF = Path(str(c.TREEMAP_TIF) + ".vat.dbf")
FIA_COND_CSV = c.RUN_WORK / "fia_cond.csv"
FIA_TREE_CSV = c.RUN_WORK / "fia_tree.csv"
COUNTY_DIR = c.RUN_WORK / "counties"

NOT_A_TREEMAP_ID = 0
CELL_ACRES = 900 * 0.000247105381
CONTINUOUS = ["STDAGE", "BALIVE", "QMD", "TPA"]
HARRIS_TO_OWNER_CLASS = {0: "unknown", 3: "private_family", 4: "private_industrial",
                         5: "tribal", 6: "federal", 7: "state", 8: "local"}
NHD_LAYERS = {"flowline": "NHDFlowline", "waterbody": "NHDWaterbody", "area": "NHDArea"}
HYDRO_PAD_M = 100.0
MIN_STAND_PIXELS = 23   # LETO minimum_stand_acres 5.0 at 0.2224 ac per cell


def value_records() -> pd.DataFrame:
    """LETO stage-1 attributes per raster VALUE: FIA dominant condition first, RAT fallback.

    The young plots' VAT rows come from the extension, and their STDAGE is the dated age.
    """
    import pyogrio

    vat = pyogrio.read_dataframe(VAT_DBF, read_geometry=False)
    for column in ("Value", "TM_ID", "PLT_CN"):
        vat[column] = as_id_series(vat[column], column=column)
    extension = read_id_csv(YOUNG / VAT_EXTENSION_CSV, id_columns=EXTENSION_ID_COLUMNS)
    vat = pd.concat([vat, extension[[col for col in extension if col in vat]]], ignore_index=True)
    vat["Value"] = vat["Value"].astype(np.int64)
    cond = read_id_csv(FIA_COND_CSV, id_columns=("PLT_CN",))
    trees = read_id_csv(FIA_TREE_CSV, id_columns=("PLT_CN",))
    records = la.value_records(vat, la.plot_lookup(cond, trees))
    young = extension.set_index(extension["Value"].astype(np.int64))["STAND_AGE_2022"]
    records.loc[young.index, "STDAGE"] = young.astype(float)
    records["NEIGHBOUR_KEPT"] = False
    kept = extension.loc[extension["MATCH_LEVEL"] == "neighbour", "Value"].astype(np.int64)
    records.loc[kept.to_numpy(), "NEIGHBOUR_KEPT"] = True
    return records


def stage_hydro() -> None:
    """NHDPlus HR flowlines, waterbodies and areas over Maine, in the TreeMap CRS, once."""
    if c.HYDRO_GPKG.exists():
        return
    counties = c.maine_counties()
    for name, layer in NHD_LAYERS.items():
        parts = []
        for hu4 in c.NHD_HU4:
            gdb = c.NHD_DIR / hu4 / f"NHDPLUS_H_{hu4}_HU4_GDB.gdb"
            frame = gpd.read_file(gdb, layer=layer, columns=["FCode"], bbox=tuple(
                counties.to_crs(4269).total_bounds), force_2d=True)
            parts.append(frame.rename(columns={"FCode": "fcode"})[["fcode", "geometry"]])
        merged = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs).to_crs(5070)
        merged.to_file(c.HYDRO_GPKG, layer=name)
        print(f"{name}: {len(merged):,} features", flush=True)


def parent_polygons(seg: np.ndarray, transform, crs) -> gpd.GeoDataFrame:
    records = [{"SEG_ID": int(v), "geometry": shape(g)}
               for g, v in shapes(seg.astype(np.int32), mask=seg > 0, transform=transform)]
    return gpd.GeoDataFrame(records, crs=crs).dissolve(by="SEG_ID", as_index=False)


def parent_summary(seg, grids, ownership, forest_type, added_back, kept) -> pd.DataFrame:
    ids = seg[seg > 0].astype(np.int64)
    counts = np.bincount(ids)
    seg_ids = np.flatnonzero(counts)
    rows = {"SEG_ID": seg_ids,
            "OWN_CODE": leto_ca.segment_categorical_mode(seg, np.maximum(ownership, 0))[seg_ids],
            "FORTYPCD_DOM": leto_ca.segment_categorical_mode(seg, forest_type)[seg_ids],
            "ADD_BACK_SHARE": (np.bincount(ids, weights=added_back[seg > 0]) / counts)[seg_ids],
            "NEIGHBOUR_KEPT_SHARE": (np.bincount(ids, weights=kept[seg > 0]) / counts)[seg_ids]}
    for name in CONTINUOUS:
        values = np.nan_to_num(grids[name], nan=0.0)[seg > 0]
        rows[f"{name}_MEAN"] = (np.bincount(ids, weights=values) / counts)[seg_ids]
    out = pd.DataFrame(rows)
    out["OWNER_CLASS"] = [HARRIS_TO_OWNER_CLASS.get(int(code), "unknown") for code in out["OWN_CODE"]]
    # LETO stage3.read_stand_polygons: Series.round(), half to even
    out["AGE"] = out["STDAGE_MEAN"].round().astype(int)
    return out


def split_riparian(parents: gpd.GeoDataFrame, bounds) -> gpd.GeoDataFrame:
    pad = box(*bounds).buffer(HYDRO_PAD_M).bounds
    hydro = {name: gpd.read_file(c.HYDRO_GPKG, layer=name, bbox=pad) for name in NHD_LAYERS}
    try:
        footprint = leto_riparian.riparian_footprint(
            leto_riparian.MAINE_V1_RULES, flowlines=hydro["flowline"],
            waterbodies=hydro["waterbody"], areas=hydro["area"])
    except ValueError:  # no buffered hydrography in this county
        units = parents.explode(ignore_index=True)
        units["RIPARIAN"] = leto_riparian.UPLAND
        units["Acres"] = leto_riparian.geodesic_acres(units)
        return units
    return leto_riparian.split_stands(parents, footprint)


def read_window(path: Path, geometry):
    with rasterio.open(path) as src:
        raw = from_bounds(*geometry.bounds, transform=src.transform)
        col, row = int(np.floor(raw.col_off)), int(np.floor(raw.row_off))
        window = Window(col, row, int(np.ceil(raw.col_off + raw.width)) - col,
                        int(np.ceil(raw.row_off + raw.height)) - row
                        ).intersection(Window(0, 0, src.width, src.height))
        return src.read(1, window=window), src.window_transform(window), src.crs, src.nodata


def run_county(fips: str, name: str, geometry, records: pd.DataFrame) -> dict:
    out_gpkg = COUNTY_DIR / f"{fips}.gpkg"
    qa_json = COUNTY_DIR / f"{fips}_qa.json"
    if qa_json.exists():
        return json.loads(qa_json.read_text())
    log_path = COUNTY_DIR / f"{fips}.log"
    with open(log_path, "w") as log_fh, contextlib.redirect_stdout(log_fh):
        timings = {}
        t0 = time.time()
        tm, transform, crs, tm_nodata = read_window(TREEMAP_TIF, geometry)
        tm = tm.astype(np.int64)
        provenance, prov_transform, _, _ = read_window(PROVENANCE_TIF, geometry)
        harris, own_transform, _, _ = read_window(OWNERSHIP_TIF, geometry)
        if prov_transform != transform or own_transform != transform:
            raise ValueError(f"{fips}: rasters are not on one grid")
        inside = geometry_mask([geometry], out_shape=tm.shape, transform=transform, invert=True)
        valid = inside & (tm != tm_nodata) & (tm != NOT_A_TREEMAP_ID)
        qa = {"fips": fips, "county": name, "valid_cells": int(valid.sum()),
              "value_zero_cells": int((inside & (tm == NOT_A_TREEMAP_ID)).sum())}
        if not valid.any():
            qa["status"] = "no_forest"
            qa_json.write_text(json.dumps(qa, indent=2))
            return qa

        grids, sources = la.attribute_rasters(tm, valid, records)
        forest_type = np.where(valid, np.nan_to_num(grids["FORTYPCD"], nan=0), 0).astype(np.int32)
        ownership = np.where(np.isin(harris, list(HARRIS_TO_OWNER_CLASS)), harris, 0).astype(np.int16)
        ownership[~valid] = -1
        added_back = (valid & (provenance == TreeMapProvenance.ADDED_BACK_YOUNG)).astype(np.float64)
        kept_values = records.index[records["NEIGHBOUR_KEPT"]].to_numpy(np.int64)
        kept = (valid & np.isin(tm, kept_values)).astype(np.float64)
        timings["stage1_s"] = round(time.time() - t0, 1)

        t0 = time.time()
        seg = leto_ca.segment({n: grids[n] for n in CONTINUOUS}, forest_type, ownership, valid,
                              CELL_ACRES,
                              similarity_raw={"STDAGE": np.nan_to_num(grids["STDAGE"], nan=0.0)})
        timings["ca_s"] = round(time.time() - t0, 1)
        leto_seg = seg
        seg, speckle = stand_speckle.absorb_small_stands(leto_seg, MIN_STAND_PIXELS)
        for suffix, labels in (("_seg_leto", leto_seg), ("_seg", seg)):
            with rasterio.open(COUNTY_DIR / f"{fips}{suffix}.tif", "w", driver="GTiff",
                               height=labels.shape[0], width=labels.shape[1], count=1,
                               dtype="int32", crs=crs, transform=transform, nodata=0,
                               compress="deflate") as dst:
                dst.write(labels.astype(np.int32), 1)

        t0 = time.time()
        parents = parent_polygons(seg, transform, crs)
        units = split_riparian(parents, geometry.bounds)
        timings["riparian_s"] = round(time.time() - t0, 1)
        units = units.merge(parent_summary(seg, grids, ownership, forest_type, added_back, kept),
                            on="SEG_ID", how="left", validate="many_to_one")
        units.insert(0, "COUNTY_FIPS", fips)
        units.insert(1, "MU_ID", np.arange(1, len(units) + 1))
        units.to_file(out_gpkg, layer="management_units")

        value_to_plt = dict(zip(records.index.to_numpy(np.int64), records["PLT_CN"]))
        weights = leto_donors.parent_weights(seg, tm, value_to_plt)
        expanded = leto_donors.expand_to_units(
            weights, units[["MU_ID", "SEG_ID"]].rename(columns={"SEG_ID": "PARENT_SEG"}))
        leto_donors.filter_weights(expanded, key="MU_ID").assign(COUNTY_FIPS=fips).to_csv(
            COUNTY_DIR / f"{fips}_weights.csv", index=False)

        upland = units["RIPARIAN"] == leto_riparian.UPLAND
        seg_acres = np.bincount(seg[seg > 0])[1:] * CELL_ACRES
        seg_acres = seg_acres[seg_acres > 0]
        qa.update({
            "status": "ok",
            "forest_acres_cells": round(float(valid.sum() * CELL_ACRES), 1),
            "management_unit_acres_geodesic": round(float(units["Acres"].sum()), 1),
            "leto_parent_stands": int(leto_seg.max()),
            "speckle_absorbed_stands": speckle.absorbed_stands,
            "speckle_absorbed_acres": round(speckle.absorbed_pixels * CELL_ACRES, 1),
            "isolated_small_stands": speckle.isolated_small_stands,
            "parent_stands": int(seg.max()),
            "parent_stands_under_5_ac": int((seg_acres < 5).sum()),
            "parent_acre_median": round(float(np.median(seg_acres)), 1),
            "management_units": int(len(units)),
            "riparian_units": int((~upland).sum()),
            "riparian_acres": round(float(units.loc[~upland, "Acres"].sum()), 1),
            "added_back_acres": round(float(added_back.sum() * CELL_ACRES), 1),
            "neighbour_kept_acres": round(float(kept.sum() * CELL_ACRES), 1),
            "unknown_owner_acres": round(float((valid & (ownership == 0)).sum() * CELL_ACRES), 1),
            "harris_nonforest_or_water_acres": round(
                float((valid & np.isin(harris, (1, 2))).sum() * CELL_ACRES), 1),
            "attribute_provenance_share": {
                f: {la.Provenance(k).name: round(float((s[valid] == k).mean()), 4)
                    for k in np.unique(s[valid])} for f, s in sources.items()},
            "acres_by_owner_class": units.groupby("OWNER_CLASS")["Acres"].sum().round(0).to_dict(),
            "timings": timings,
        })
        qa_json.write_text(json.dumps(qa, indent=2))
        return qa


def _run(args):
    fips, name, geometry, records = args
    try:
        return run_county(fips, name, geometry, records)
    except Exception:
        return {"fips": fips, "county": name, "status": "error", "error": traceback.format_exc()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fips", nargs="*", help="county FIPS to run (default: all 16)")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    COUNTY_DIR.mkdir(parents=True, exist_ok=True)
    stage_hydro()
    records = value_records()
    counties = c.maine_counties()
    if args.fips:
        counties = counties[counties["ADMIN_FIPS"].isin(args.fips)]
    # Largest windows first so the long counties do not start last.
    counties = counties.assign(_area=counties.geometry.envelope.area).sort_values("_area",
                                                                                  ascending=False)
    jobs = [(r.ADMIN_FIPS, r.NAME, r.geometry, records) for r in counties.itertuples()]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_run, job) for job in jobs]
        for future in as_completed(futures):
            qa = future.result()
            line = {k: qa.get(k) for k in ("fips", "county", "status", "valid_cells",
                                           "parent_stands", "management_units", "timings")}
            print(json.dumps(line), flush=True)
            if qa.get("status") == "error":
                print(qa["error"], flush=True)


if __name__ == "__main__":
    main()
