"""Step 6: merge the per-county LETO outputs into one Maine stand layer.

``MU_ID`` and ``SEG_ID`` are renumbered statewide; the county-local ids stay as
``COUNTY_MU_ID`` and ``COUNTY_SEG_ID``. Donor weights follow the new ``MU_ID``.

Outputs: outputs/me_management_units.gpkg, outputs/me_mu_donor_weights.csv.gz,
outputs/me_statewide_qa.json
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

HERE = Path(__file__).resolve().parent
COUNTY_DIR = HERE / "work/counties"
OUTPUTS = HERE / "outputs"


def main() -> None:
    OUTPUTS.mkdir(exist_ok=True)
    qas = [json.loads(p.read_text()) for p in sorted(COUNTY_DIR.glob("*_qa.json"))]
    bad = [q["fips"] for q in qas if q["status"] not in ("ok", "no_forest")]
    if bad:
        raise SystemExit(f"counties not finished: {bad}")
    units, weights = [], []
    mu_offset = seg_offset = 0
    for qa in (q for q in qas if q["status"] == "ok"):
        fips = qa["fips"]
        u = gpd.read_file(COUNTY_DIR / f"{fips}.gpkg", layer="management_units")
        w = pd.read_csv(COUNTY_DIR / f"{fips}_weights.csv", dtype={"PLT_CN": str})
        u = u.rename(columns={"MU_ID": "COUNTY_MU_ID", "SEG_ID": "COUNTY_SEG_ID"})
        u.insert(1, "MU_ID", u["COUNTY_MU_ID"] + mu_offset)
        u.insert(2, "SEG_ID", u["COUNTY_SEG_ID"] + seg_offset)
        w = w.rename(columns={"MU_ID": "COUNTY_MU_ID", "PARENT_SEG": "COUNTY_SEG_ID"})
        w.insert(0, "MU_ID", w["COUNTY_MU_ID"] + mu_offset)
        w.insert(1, "PARENT_SEG", w["COUNTY_SEG_ID"] + seg_offset)
        mu_offset += int(u["COUNTY_MU_ID"].max())
        seg_offset += int(qa["parent_stands"])
        units.append(u)
        weights.append(w)
    units = gpd.GeoDataFrame(pd.concat(units, ignore_index=True), crs=units[0].crs)
    weights = pd.concat(weights, ignore_index=True)
    units.to_file(OUTPUTS / "me_management_units.gpkg", layer="management_units")
    weights.to_csv(OUTPUTS / "me_mu_donor_weights.csv.gz", index=False)

    ok = [q for q in qas if q["status"] == "ok"]
    parents = units.groupby("SEG_ID").agg(Acres=("Acres", "sum"), OWNER_CLASS=("OWNER_CLASS", "first"))
    riparian = units["RIPARIAN"] == 1
    total = lambda key: round(sum(q[key] for q in ok), 1)  # noqa: E731
    qa = {
        "inputs": {"treemap": "work/rasters/v4/maine/young_plots/treemap2022_improved.tif (v4, bookends only)",
                   "ownership": "work/rasters/v4/maine/nwos2022_improved.tif",
                   "leto_cfg": "DEFAULT_CFG (LETO v1 Maine baseline)", "riparian": "MAINE_V1_RULES on NHDPlus HR",
                   "study_area": "each county separately"},
        "counties_run": len(ok),
        "counties_without_forest": [q["fips"] for q in qas if q["status"] == "no_forest"],
        "valid_cells": int(sum(q["valid_cells"] for q in ok)),
        "value_zero_cells": int(sum(q["value_zero_cells"] for q in qas)),
        "forest_acres_cells": total("forest_acres_cells"),
        "management_unit_acres_geodesic": round(float(units["Acres"].sum()), 1),
        "parent_stands": int(len(parents)),
        "parent_stands_under_5_ac": int((parents["Acres"] < 5).sum()),
        "parent_acres_under_5_ac": round(float(parents.loc[parents["Acres"] < 5, "Acres"].sum()), 1),
        "parent_acre_median": round(float(parents["Acres"].median()), 2),
        "management_units": int(len(units)),
        "riparian_units": int(riparian.sum()),
        "riparian_acres": round(float(units.loc[riparian, "Acres"].sum()), 1),
        "added_back_acres": total("added_back_acres"),
        "neighbour_kept_acres": total("neighbour_kept_acres"),
        "leto_parent_stands": int(sum(q["leto_parent_stands"] for q in ok)),
        "speckle_absorbed_stands": int(sum(q["speckle_absorbed_stands"] for q in ok)),
        "speckle_absorbed_acres": total("speckle_absorbed_acres"),
        "isolated_small_stands": int(sum(q["isolated_small_stands"] for q in ok)),
        "unknown_owner_acres": total("unknown_owner_acres"),
        "harris_nonforest_or_water_acres": total("harris_nonforest_or_water_acres"),
        "unknown_owner_parent_share": round(float((parents["OWNER_CLASS"] == "unknown").mean()), 4),
        "acres_by_owner_class": units.groupby("OWNER_CLASS")["Acres"].sum().round(0).to_dict(),
        "donor_rows": int(len(weights)),
        "distinct_donor_plots": int(weights["PLT_CN"].nunique()),
        "slowest_counties": sorted(({"fips": q["fips"], "county": q["county"], **q["timings"]}
                                    for q in ok), key=lambda t: -t["ca_s"])[:5],
    }
    (OUTPUTS / "me_statewide_qa.json").write_text(json.dumps(qa, indent=2))
    print(json.dumps(qa, indent=2))


if __name__ == "__main__":
    main()
