"""FIA's own forest area, growing stock, growth and removals per pilot county (EVALIDator).

The Suwannee question: TPO credits Suwannee with the largest removals of the five counties,
yet every schedule runs it short. These are FIA's design-based estimates for the same
counties, from the API `pipeline.s1_initial_state.verify_fia_evalidator.query` already uses,
with evaluation group 122022 (2022 current inventory; growth and removals over the 2011-2018 to
2016-2022 remeasurement). Rows are per county, each with its own sampling error; county
variances must not be added in quadrature (they share strata).

Writes `outputs/fia_by_county.csv`.

    uv run python experiments/2026-10-12_raster-set-schedules/fia_by_county.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

EXP = Path(__file__).resolve().parent
sys.path[:0] = [str(EXP), str(EXP.parents[1])]

import raster_sets as RS  # noqa: E402
from pipeline.s1_initial_state.verify_fia_evalidator import COUNTIES, EVAL_GRP, query  # noqa: E402

ATTRIBUTES = {
    2: "forest_area_ac",
    15: "growing_stock_cuft",
    202: "net_growth_cuft_per_yr",
    226: "removals_cuft_per_yr",
    238: "harvest_removals_cuft_per_yr",
}


def by_county(snum: int) -> pd.DataFrame:
    out = query({"snum": snum, "wc": EVAL_GRP, "rselected": "County code and name",
                 "cselected": "All live stocking",
                 "wf": f"PLOT.COUNTYCD IN ({', '.join(str(c) for c in COUNTIES)})",
                 "outputFormat": "JSON"})
    rows = []
    for row in out["row"]:
        if row["content"] == "Total":
            continue
        cell = next(c for c in row["column"] if c["content"] == "Total")
        rows.append({"county": row["content"].split(" FL ")[-1], "snum": snum,
                     "estimate": cell["cellValueNumerator"], "se_pct": cell["cellSE"],
                     "plots": int(cell["cellPlotNumerator"])})
    return pd.DataFrame(rows)


def main() -> None:
    long = pd.concat([by_county(s).assign(attribute=name) for s, name in ATTRIBUTES.items()])
    wide = long.pivot_table(index="county", columns="attribute", values=["estimate", "se_pct"])
    wide.columns = [f"{a}" if s == "estimate" else f"{a}_se_pct" for s, a in wide.columns]
    wide["plots"] = long[long["snum"] == 2].set_index("county")["plots"]
    RS.OUTPUTS.mkdir(parents=True, exist_ok=True)
    wide.reset_index().to_csv(RS.OUTPUTS / "fia_by_county.csv", index=False)
    print(wide.round(1).to_string())


if __name__ == "__main__":
    main()
