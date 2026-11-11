"""#167 demo: the T3 county (Union, 12125) from the v4 raster run, speckle KEEP vs MERGE.

    PYTHONPATH=. uv run python experiments/2026-11-09_speckle-merge/demo.py \\
        --repair <v4 aoi_5county run> --data-root <data/r2_cache> --region-dir <data/regions/fl> \\
        --out <scratch dir>

Runs the ``leto`` stage twice, once with ``speckle: keep`` (LETO's own units) and once with
the default ``merge``, and writes ``size_distribution.csv`` (scheduled units, i.e. not masked,
by acre class) and ``speckle_qa.csv`` (the merge run's ``qa/leto/speckle.csv``) beside this file.
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import geopandas as gpd
import pandas as pd

from pipeline.data_access import data_paths, under_data_root
from pipeline.leto_speckle import SpeckleAction
from pipeline.leto_stage import QA_DIR, SPECKLE, UNITS_GPKG, UNITS_LAYER, load_settings, run_leto
from pipeline.s1_initial_state.add_back_ids import read_vat
from pipeline.s3_management.owner_classes import MASKED

HERE = Path(__file__).resolve().parent
COUNTY = "12125"
CELL_ACRES = 900 / 4046.8564224
BINS = [0, CELL_ACRES, 1, 5, 20, 100, float("inf")]
LABELS = ["< 1 cell", "1 cell - 1 ac", "1-5 ac", "5-20 ac", "20-100 ac", ">= 100 ac"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--region-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    raw = data_paths()["raw"]
    vat = read_vat(under_data_root(raw["treemap_2022"]["tif"] + ".vat.dbf", args.data_root))
    counties = under_data_root(raw["counties"]["p010g_shp"], args.data_root)
    base = load_settings(COUNTY)
    rows = []
    for action in (SpeckleAction.KEEP, SpeckleAction.MERGE, SpeckleAction.MERGE_DELETE):
        settings = dataclasses.replace(base, speckle=dataclasses.replace(base.speckle, action=action))
        run_dir = args.out / action.value
        run_leto(run_dir, repair=args.repair, fips=[COUNTY], vat=vat, counties=counties,
                 region_dir=args.region_dir, settings=settings)
        units = gpd.read_file(run_dir / UNITS_GPKG, layer=UNITS_LAYER)
        units = units[units["owner_class"] != MASKED]
        size = pd.cut(units["Acres"], BINS, labels=LABELS, right=False)
        table = units.groupby(size, observed=False)["Acres"].agg(["count", "sum"])
        for label, (count, acres) in table.iterrows():
            rows.append({"speckle": action.value, "size": label, "units": int(count),
                         "acres": round(float(acres), 1)})
        rows.append({"speckle": action.value, "size": "all", "units": len(units),
                     "acres": round(float(units["Acres"].sum()), 1)})
    pd.DataFrame(rows).to_csv(HERE / "size_distribution.csv", index=False)
    pd.concat([pd.read_csv(args.out / a / QA_DIR / SPECKLE).assign(speckle=a)
               for a in (SpeckleAction.MERGE, SpeckleAction.MERGE_DELETE)]
              ).to_csv(HERE / "speckle_qa.csv", index=False)
    print(pd.DataFrame(rows).pivot(index="size", columns="speckle", values=["units", "acres"]))


if __name__ == "__main__":
    main()
