"""LETO cellular-automata stand segmentation over the AOI (LETO stage 2).

The CA algorithm itself lives in `pipeline.leto_ca` — a pure-array port of
LETO `scripts/Cellular_automata/02_segment_treemap.py` (v3,
boundary-vectorized) whose `DEFAULT_CFG` carries this experiment's validated
constants (five-feature weights, 100-iteration / 0.1% convergence, raw-age
similar-stand merge). This script is the driver: it stages the attribute
rasters in, runs `leto_ca.segment`, splits the riparian management units, and
writes the summary/crosswalk outputs.

Outputs (work/):
    segmentation.npz      mu_labels (management-unit raster), seg_labels
                          (parent stands), riparian mask
    mu_summary.csv        one row per management unit: acreage, owner class,
                          management class, attribute means, dominant type
    mu_donor_weights.csv  MU_ID x PLT_CN pixel-share donor weights (LETO
                          stage 3 crosswalk)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CELL_ACRES,
    FLOWLINE_BUFFER_RULES_FT,
    FT_TO_M,
    HARRIS_TO_OWNER_CLASS,
    STREAMS_SHP,
    region_paths,
)
from pipeline import leto_ca  # noqa: E402
from pipeline.leto_ca import (  # noqa: E402
    RIPARIAN_MANAGEMENT_CODE,
    UPLAND_MANAGEMENT_CODE,
    segment_categorical_mode,
)

CONTINUOUS = ["STDAGE", "BALIVE", "QMD", "TPA"]


def rasterize_riparian(shape, transform):
    """Buffer NHD flowlines by the LETO FCode rules and burn to the AOI grid."""
    import geopandas as gpd
    from rasterio.features import rasterize

    streams = gpd.read_file(STREAMS_SHP).to_crs("EPSG:5070")
    shapes = []
    for _, row in streams.iterrows():
        rule = FLOWLINE_BUFFER_RULES_FT.get(int(row["fcode"]))
        if rule is None or rule[1] <= 0:
            continue
        shapes.append(row.geometry.buffer(rule[1] * FT_TO_M))
    if not shapes:
        return np.zeros(shape, dtype=bool)
    burned = rasterize(((g, 1) for g in shapes), out_shape=shape,
                       transform=transform, fill=0, dtype="uint8")
    return burned.astype(bool)


def main() -> None:
    import argparse
    import json

    import rasterio

    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=("aoi", "full"), default="aoi")
    args = parser.parse_args()
    paths = region_paths(args.region)

    data = np.load(paths.attributes_npz)
    valid = data["valid"]

    if args.region == "full":
        # The five counties are not a rectangle; the staged raster covers
        # their bounding box, so cells outside the actual county polygons
        # (river slivers of neighbouring counties, mostly) must drop out of
        # segmentation the same way non-forest already does.
        with rasterio.open(paths.county_mask_tif) as src:
            in_county = src.read(1).astype(bool)
        before = int(valid.sum())
        valid = valid & in_county
        print(f"county mask: {before:,} forested cells -> {int(valid.sum()):,} "
              f"inside the five-county pilot boundary")

    forest_type = np.where(valid, np.nan_to_num(data["FORTYPCD"], nan=0), 0).astype(np.int32)
    features_raw = {n: data[n] for n in CONTINUOUS}
    raw = {n: np.nan_to_num(data[n], nan=0.0) for n in CONTINUOUS}

    with rasterio.open(paths.ownership_tif) as src:
        harris = src.read(1)
        transform = src.transform
    # CA ownership hard-boundary codes: forest owner classes stay distinct,
    # non-forest/water/nodata on forested TreeMap cells fall to unknown (0).
    ownership = np.where(np.isin(harris, list(HARRIS_TO_OWNER_CLASS)), harris, 0).astype(np.int16)
    ownership[~valid] = -1

    # DEFAULT_CFG is this experiment's validated configuration; the similar-
    # stand merge gates on raw (unstandardized) stand age, per LETO.
    info: dict = {}
    seg_labels = leto_ca.segment(features_raw, forest_type, ownership, valid, CELL_ACRES,
                                 similarity_raw={"STDAGE": raw["STDAGE"]}, info=info)
    n_segments = int(seg_labels.max())

    # Riparian split: buffered NHD flowlines become their own management
    # units within each parent stand (LETO ManagementUnits_Final).
    riparian = rasterize_riparian(seg_labels.shape, transform) & valid
    print(f"riparian cells: {int(riparian.sum()):,} "
          f"({riparian.sum() * CELL_ACRES:.0f} acres)")
    mu_labels = leto_ca.split_management_units(seg_labels, riparian)
    print(f"management units (MU_ID): {int(mu_labels.max()):,}")

    # Save the rasters as soon as they exist, before the (cheaper but not
    # free) summary/donor-weight bookkeeping below — so a bug in that later
    # bookkeeping doesn't cost rerunning the CA loop itself, which is the
    # expensive part at full-region scale.
    np.savez_compressed(paths.segmentation_npz, mu_labels=mu_labels,
                        seg_labels=seg_labels, riparian=riparian)

    # Per-MU summary
    valid_mu = mu_labels > 0
    ids = mu_labels[valid_mu].astype(np.int64)
    max_id = int(ids.max())
    counts = np.bincount(ids, minlength=max_id + 1)
    mu_ids = np.flatnonzero(counts > 0)
    mu_ids = mu_ids[mu_ids > 0]
    # Every cell of a management unit carries the same parent stand by
    # construction, so this is a direct lookup, not a categorical mode.
    parent = leto_ca.uniform_label_lookup(mu_labels, seg_labels)
    owner_mode = segment_categorical_mode(mu_labels, np.maximum(ownership, 0))
    mgmt = segment_categorical_mode(mu_labels, riparian.astype(np.int32))
    dom_type = segment_categorical_mode(mu_labels, forest_type)
    rows = {"MU_ID": mu_ids, "PARENT_SEG": parent[mu_ids],
            "PIXEL_COUNT": counts[mu_ids], "ACRES": counts[mu_ids] * CELL_ACRES,
            "OWN_CODE": owner_mode[mu_ids],
            "OWNER_CLASS": [HARRIS_TO_OWNER_CLASS.get(int(c), "unknown")
                            for c in owner_mode[mu_ids]],
            "MGMT_CLASS": np.where(mgmt[mu_ids] > 0, RIPARIAN_MANAGEMENT_CODE,
                                   UPLAND_MANAGEMENT_CODE),
            "FORTYPCD_DOM": dom_type[mu_ids]}
    for name in CONTINUOUS:
        sums = np.bincount(ids, weights=raw[name][valid_mu], minlength=max_id + 1)
        rows[f"{name}_MEAN"] = np.divide(sums, counts, out=np.zeros(max_id + 1),
                                         where=counts > 0)[mu_ids]
    summary = pd.DataFrame(rows)
    summary.to_csv(paths.mu_summary_csv, index=False)

    # Donor weights: pixel share of each TreeMap plot (PLT_CN) within each MU
    # — LETO stage 3's segment/plot crosswalk.
    tm = data["tm"]
    vat = pd.read_csv(paths.vat_csv, dtype={"PLT_CN": str})
    value_to_plt = dict(zip(vat["Value"].astype(np.int64), vat["PLT_CN"]))
    pairs = pd.DataFrame({"MU_ID": mu_labels[valid_mu], "VALUE": tm[valid_mu]})
    weights = (pairs.groupby(["MU_ID", "VALUE"]).size().rename("CELLS").reset_index())
    weights["PLT_CN"] = weights["VALUE"].map(value_to_plt)
    weights["WEIGHT"] = weights["CELLS"] / weights.groupby("MU_ID")["CELLS"].transform("sum")
    weights.to_csv(paths.mu_donor_weights_csv, index=False)

    qa = {
        "valid_cells": int(valid.sum()),
        "parent_segments": n_segments,
        "management_units": int(len(mu_ids)),
        "riparian_units": int((summary["MGMT_CLASS"] == RIPARIAN_MANAGEMENT_CODE).sum()),
        "total_acres": float(valid.sum() * CELL_ACRES),
        "acres_by_owner_class": summary.groupby("OWNER_CLASS")["ACRES"].sum().round(1).to_dict(),
        "homogeneity_scales": {k: float(v) for k, v in info["homogeneity_scales"].items()},
    }
    paths.segmentation_qa_json.write_text(json.dumps(qa, indent=2))
    print(json.dumps(qa, indent=2))


if __name__ == "__main__":
    main()
