"""LETO stage 2-3 over all of FL5: v1 CA, vector riparian split, parent donor weights.

- CA: ``pipeline.leto_ca.segment`` with ``DEFAULT_CFG`` (the LETO v1 verified baseline,
  including ``force_merge`` for remaining small stands) on the improved NWOS hard boundaries.
- Riparian: parent stands are vectorised (SEG_ID) and split by ``pipeline.leto_riparian``.
  The buffers come from NHDPlus flowlines (``FL_5_Co_Streams``) and NHD waterbodies
  (``nhd_waterbody_fl5.gpkg``) under LETO's Florida first-pass rules. NHDArea is not staged
  anywhere, so river-channel area buffers are approximated by buffering FCode 55800
  (artificial path, the mapped centreline of double-banked rivers) at the perennial 75 ft.
  This is the one deviation from LETO.
- Donor weights: each parent's TreeMap pixel shares (``pipeline.leto_donors``), inherited by
  every piece of that parent.

Outputs: work/segmentation.npz, work/management_units.gpkg, work/mu_summary.csv,
work/mu_donor_weights.csv, outputs/segmentation_qa.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deck_paths as P  # noqa: E402
from pipeline import leto_ca, leto_donors, leto_riparian  # noqa: E402

CONTINUOUS = ["STDAGE", "BALIVE", "QMD", "TPA"]
ARTIFICIAL_PATH_PROXY = {55800: ("Artificial Path (NHDArea proxy)", 75.0)}
WATERBODY_GPKG = P.viz.SHARED_STAGE_ROOT / "nhd_waterbody_fl5.gpkg"


def parent_polygons(seg: np.ndarray, transform, crs) -> gpd.GeoDataFrame:
    records = [{"SEG_ID": int(v), "geometry": shape(g)}
               for g, v in shapes(seg.astype(np.int32), mask=seg > 0, transform=transform)]
    return gpd.GeoDataFrame(records, crs=crs).dissolve(by="SEG_ID", as_index=False)


def parent_summary(seg, data, ownership, forest_type) -> pd.DataFrame:
    ids = seg[seg > 0].astype(np.int64)
    counts = np.bincount(ids)
    seg_ids = np.flatnonzero(counts)
    seg_ids = seg_ids[seg_ids > 0]
    rows = {"SEG_ID": seg_ids,
            "OWN_CODE": leto_ca.segment_categorical_mode(seg, np.maximum(ownership, 0))[seg_ids],
            "FORTYPCD_DOM": leto_ca.segment_categorical_mode(seg, forest_type)[seg_ids],
            "YOUNG_SHARE": (np.bincount(ids, weights=data["young"][seg > 0]) / counts)[seg_ids]}
    for name in CONTINUOUS:
        values = np.nan_to_num(data[name], nan=0.0)[seg > 0]
        rows[f"{name}_MEAN"] = (np.bincount(ids, weights=values) / counts)[seg_ids]
    out = pd.DataFrame(rows)
    out["OWNER_CLASS"] = [P.viz.HARRIS_TO_OWNER_CLASS.get(int(c), "unknown") for c in out["OWN_CODE"]]
    # LETO stage3.read_stand_polygons: Series.round(), half to even (its floor(x + 0.5) never runs)
    out["AGE"] = out["STDAGE_MEAN"].round().astype(int)
    return out


def main() -> None:
    data = np.load(P.ATTRIBUTES_NPZ)
    valid = data["valid"]
    forest_type = np.where(valid, np.nan_to_num(data["FORTYPCD"], nan=0), 0).astype(np.int32)
    features_raw = {n: data[n] for n in CONTINUOUS}
    with rasterio.open(P.NWOS_TIF) as src:
        harris = src.read(1)
        transform, crs = src.transform, src.crs
    ownership = np.where(np.isin(harris, list(P.viz.HARRIS_TO_OWNER_CLASS)), harris, 0).astype(np.int16)
    ownership[~valid] = -1

    t0 = time.time()
    info: dict = {}
    seg = leto_ca.segment(features_raw, forest_type, ownership, valid, P.viz.CELL_ACRES,
                          similarity_raw={"STDAGE": np.nan_to_num(data["STDAGE"], nan=0.0)},
                          info=info)
    print(f"CA: {time.time() - t0:,.0f} s; parent stands {int(seg.max()):,}")
    np.savez_compressed(P.SEGMENTATION_NPZ, seg_labels=seg)

    t0 = time.time()
    parents = parent_polygons(seg, transform, crs)
    flow = gpd.read_file(P.viz.STREAMS_SHP).to_crs(crs)
    water = gpd.read_file(WATERBODY_GPKG).to_crs(crs)
    rules = leto_riparian.FLORIDA_FIRST_PASS_RULES
    rules = leto_riparian.BufferRules(flowline={**rules.flowline, **ARTIFICIAL_PATH_PROXY},
                                      waterbody=rules.waterbody, area=rules.area)
    footprint = leto_riparian.riparian_footprint(rules, flowlines=flow, waterbodies=water)
    units = leto_riparian.split_stands(parents, footprint)
    units.insert(0, "MU_ID", np.arange(1, len(units) + 1))
    print(f"riparian split: {time.time() - t0:,.0f} s; {len(units):,} management units")

    summary = parent_summary(seg, data, ownership, forest_type)
    units = units.merge(summary, on="SEG_ID", how="left", validate="many_to_one")
    units.to_file(P.WORK / "management_units.gpkg")
    units.drop(columns="geometry").rename(columns={"SEG_ID": "PARENT_SEG"}).to_csv(
        P.MU_SUMMARY_CSV, index=False)

    vat = pd.read_csv(P.VAT_CSV, dtype={"PLT_CN": str})
    weights = leto_donors.parent_weights(seg, data["tm"], dict(zip(vat["Value"].astype(np.int64),
                                                                    vat["PLT_CN"])))
    expanded = leto_donors.expand_to_units(
        weights, units[["MU_ID", "SEG_ID"]].rename(columns={"SEG_ID": "PARENT_SEG"}))
    leto_donors.filter_weights(expanded, key="MU_ID").to_csv(P.MU_DONOR_WEIGHTS_CSV, index=False)

    upland = units["RIPARIAN"] == leto_riparian.UPLAND
    seg_acres = np.bincount(seg[seg > 0])[1:] * P.viz.CELL_ACRES
    qa = {
        "inputs": {"treemap": P.TREEMAP_TIF.name, "nwos": P.NWOS_TIF.name,
                   "gate": P.IMPROVED.parent.name, "leto_cfg": "DEFAULT_CFG (LETO v1 Maine baseline)"},
        "valid_cells": int(valid.sum()),
        "forest_acres_cells": round(float(valid.sum() * P.viz.CELL_ACRES), 1),
        "management_unit_acres_geodesic": round(float(units["Acres"].sum()), 1),
        "parent_stands": int(seg.max()),
        "parent_stands_under_5_ac": int((seg_acres[seg_acres > 0] < 5).sum()),
        "parent_acre_median": round(float(np.median(seg_acres[seg_acres > 0])), 1),
        "management_units": int(len(units)),
        "riparian_units": int((~upland).sum()),
        "riparian_acres": round(float(units.loc[~upland, "Acres"].sum()), 1),
        "units_mostly_add_back": int((units["YOUNG_SHARE"] >= 0.5).sum()),
        "acres_by_owner_class": units.groupby("OWNER_CLASS")["Acres"].sum().round(0).to_dict(),
        "deviation_from_leto": "FCode 55800 buffered at 75 ft as an NHDArea proxy",
    }
    P.SEGMENTATION_QA_JSON.write_text(json.dumps(qa, indent=2))
    print(json.dumps(qa, indent=2))


if __name__ == "__main__":
    main()
