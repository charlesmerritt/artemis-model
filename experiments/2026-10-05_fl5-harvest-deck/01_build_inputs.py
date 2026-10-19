"""LETO stage 1 on the improved FL5 rasters (``pipeline.leto_attributes``), add-back re-plotted.

Attributes follow LETO v1 exactly: the dominant FIA condition gives STDAGE/FORTYPCD/BALIVE,
its live FIA trees give TPA/QMD, the TreeMap RAT is a fallback, and remaining gaps take the
nearest cell's value. Values are stored as LETO stores them (tenths for BALIVE/QMD/TPA).

Provenance-4 ("added back young") pixels are an ARTEMIS extension LETO does not have. Each
takes a young FIA plot (``young_plots.match_young_plot``) under a new raster VALUE above
TreeMap's range, one per (neighbour TM value, young plot, stand age). That record carries the
young plot's FIA attributes, with STDAGE set to the drawn age.

Outputs: work/attributes.npz, work/vat_extended.csv, outputs/young_plot_matches.csv,
outputs/stage1_qa.json
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deck_paths as P  # noqa: E402
from pipeline import leto_attributes as la  # noqa: E402
from pipeline.ids import as_id_series  # noqa: E402


def read_fia() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    con = sqlite3.connect(P.viz.FIA_DB)
    cond = pd.read_sql("""SELECT CAST(PLT_CN AS TEXT) AS PLT_CN, CONDID, COND_STATUS_CD,
                                 CONDPROP_UNADJ, STDAGE, FORTYPCD, BALIVE FROM COND""", con)
    trees = pd.read_sql("""SELECT CAST(PLT_CN AS TEXT) AS PLT_CN, CONDID, STATUSCD, DIA,
                                  TPA_UNADJ FROM TREE""", con)
    groups = pd.read_sql("SELECT VALUE AS FORTYPCD, TYPGRPCD FROM REF_FOREST_TYPE", con)
    con.close()
    return cond, trees, groups


def main() -> None:
    with rasterio.open(P.TREEMAP_TIF) as src:
        tm = src.read(1).astype(np.int64)
    with rasterio.open(P.TREEMAP_PROVENANCE_TIF) as src:
        provenance = src.read(1)
    valid = tm != P.viz.TREEMAP_NODATA
    young = valid & (provenance == P.PROVENANCE_ADDED_BACK_YOUNG)

    vat = P.viz_attributes.read_vat(P.viz.TREEMAP_VAT_DBF)
    vat["PLT_CN"] = as_id_series(vat["PLT_CN"], column="PLT_CN")
    vat["Value"] = vat["Value"].astype(np.int64)
    cond, trees, groups = read_fia()
    lookup = la.plot_lookup(cond, trees)
    records = la.value_records(vat[vat["Value"].isin(np.unique(tm[valid]))], lookup)

    # Young-plot pool: FIA plots with a dominant-condition age and a TreeMap row.
    pool = (lookup.dropna(subset=["STDAGE", "FORTYPCD"])
            .merge(groups, on="FORTYPCD", how="left")
            .merge(vat.sort_values("Value").drop_duplicates("PLT_CN")[["PLT_CN", "Value"]],
                   on="PLT_CN", how="inner"))
    group_of_type = groups.set_index("FORTYPCD")["TYPGRPCD"].to_dict()

    ages = P.young_plots.patch_ages(young, seed=P.AGE_SEED)
    pairs = (pd.DataFrame({"NEIGHBOUR_VALUE": tm[young], "STDAGE": ages[young]})
               .value_counts().rename("PIXELS").reset_index())
    pairs["FORTYPCD"] = records["FORTYPCD"].reindex(pairs["NEIGHBOUR_VALUE"]).to_numpy()
    matches = [P.young_plots.match_young_plot(int(r.FORTYPCD), int(r.STDAGE), pool,
                                              type_group=group_of_type.get(int(r.FORTYPCD)))
               for r in pairs.itertuples()]
    pairs["YOUNG_PLT_CN"] = [m.plt_cn for m in matches]
    pairs["MATCH_RUNG"] = [str(m.rung) for m in matches]
    pairs["AGE_GAP"] = [m.age_gap for m in matches]
    pairs = pairs[pairs["YOUNG_PLT_CN"].notna()].reset_index(drop=True)
    pairs["VALUE"] = int(vat["Value"].max()) + 1 + np.arange(len(pairs))

    young_plots = (pool.set_index("PLT_CN")
                       .reindex(pairs["YOUNG_PLT_CN"])[["FORTYPCD", "BALIVE", "TPA", "QMD"]])
    young_records = pd.DataFrame({"PLT_CN": pairs["YOUNG_PLT_CN"].to_numpy(),
                                  "STDAGE": pairs["STDAGE"].to_numpy(dtype=float),
                                  **{f: young_plots[f].to_numpy(dtype=float)
                                     for f in ("FORTYPCD", "BALIVE", "TPA", "QMD")}},
                                 index=pd.Index(pairs["VALUE"].to_numpy(), name="Value"))
    for field in la.FIELDS:
        young_records[f"SOURCE_{field}"] = la.Provenance.FIA

    key = pd.MultiIndex.from_arrays([tm[young], ages[young]])
    lookup_value = pd.Series(pairs["VALUE"].to_numpy(),
                             index=pd.MultiIndex.from_arrays([pairs["NEIGHBOUR_VALUE"], pairs["STDAGE"]]))
    new_values = lookup_value.reindex(key).to_numpy()
    tm_out = tm.copy()
    tm_out[young] = np.where(np.isnan(new_values), P.viz.TREEMAP_NODATA, new_values).astype(np.int64)
    valid = tm_out != P.viz.TREEMAP_NODATA
    young &= valid

    all_records = pd.concat([records, young_records])
    grids, sources = la.attribute_rasters(tm_out, valid, all_records)

    P.WORK.mkdir(exist_ok=True)
    P.OUTPUTS.mkdir(exist_ok=True)
    np.savez_compressed(P.ATTRIBUTES_NPZ, tm=tm_out, valid=valid, young=young, **grids)
    all_records.reset_index().to_csv(P.VAT_CSV, index=False)
    pairs.to_csv(P.YOUNG_MATCHES_CSV, index=False)

    cells = int(valid.sum())
    qa = {"valid_cells": cells,
          "provenance_share": {f: {la.Provenance(k).name: round(float((s[valid] == k).sum()) / cells, 4)
                                   for k in np.unique(s[valid])} for f, s in sources.items()},
          "add_back_young_acres": round(float(young.sum() * P.viz.CELL_ACRES), 0),
          "young_match_rungs_acres": (pairs.groupby("MATCH_RUNG")["PIXELS"].sum()
                                      * P.viz.CELL_ACRES).round(0).to_dict()}
    (P.OUTPUTS / "stage1_qa.json").write_text(json.dumps(qa, indent=2))
    print(json.dumps(qa, indent=2))
    for name in ("STDAGE", "BALIVE", "QMD", "TPA"):
        g = grids[name]
        print(f"{name:7s} ordinary median {np.nanmedian(g[valid & ~young]):7.1f}   "
              f"add-back median {np.nanmedian(g[young]):7.1f}")


if __name__ == "__main__":
    main()
