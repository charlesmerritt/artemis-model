"""Five-county FIA volume estimates — the PER-21 scale-up gate.

PER-21 ("Generate volume estimates for the five county area") and PER-23
("Check estimates for five county work before scaling up") are the team's own
declared precondition to running ARTEMIS at the state scale. This module
produces the numbers to share with AA and CM:

- standing growing-stock inventory, forest land and timberland (ft³)
- average annual net growth of that volume (ft³/yr)
- average annual removals and harvest removals (ft³/yr) — the direct
  comparators for the annealed plan's even-flow harvest

All five counties are requested as ONE domain (Baker 3, Columbia 23, Hamilton
47, Suwannee 121, Union 125) so the sampling error is computed correctly —
county rows share strata and their variances may not be added in quadrature.
Same lesson, same machinery as :mod:`verify_fia_evalidator`; that module owns
the API plumbing (:func:`query`, :func:`total_cell`) and is reused here.

Requires network access; no Earth Engine or /mnt/d dependency.

Usage
-----
    uv run python -m pipeline.s1_initial_state.fia_volume_estimates
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
from typing import Callable

from pipeline.s1_initial_state.verify_fia_evalidator import (
    COUNTIES,
    DATA,
    EVAL_GRP,
    query as evalidator_query,
    total_cell,
)

REPO = Path(__file__).resolve().parents[2]
PLAN_CSV = REPO / "weekly-artifact/2026-09-14/harvest_by_cycle.csv"
OUT_JSON = DATA / "fia_volume_estimates.json"

# EVALIDator snum -> short name (probed against wc=122022, Florida EXPCURR 2022).
SNUM_INVENTORY_FORESTLAND = 15    # net growing-stock volume, ft³, forest land
SNUM_INVENTORY_TIMBERLAND = 18    # net growing-stock volume, ft³, timberland
SNUM_NET_GROWTH_FORESTLAND = 202  # avg annual net growth, ft³/yr, forest land
SNUM_NET_GROWTH_TIMBERLAND = 208  # avg annual net growth, ft³/yr, timberland
SNUM_REMOVALS_FORESTLAND = 226    # avg annual removals, ft³/yr, forest land
SNUM_REMOVALS_TIMBERLAND = 232    # avg annual removals, ft³/yr, timberland
SNUM_HARVEST_FORESTLAND = 238     # avg annual harvest removals, ft³/yr, forest land

ATTRIBUTE_SNUMS = {
    SNUM_INVENTORY_FORESTLAND: "growing_stock_inventory_forestland_cuft",
    SNUM_INVENTORY_TIMBERLAND: "growing_stock_inventory_timberland_cuft",
    SNUM_NET_GROWTH_FORESTLAND: "net_growth_forestland_cuft_per_year",
    SNUM_NET_GROWTH_TIMBERLAND: "net_growth_timberland_cuft_per_year",
    SNUM_REMOVALS_FORESTLAND: "removals_forestland_cuft_per_year",
    SNUM_REMOVALS_TIMBERLAND: "removals_timberland_cuft_per_year",
    SNUM_HARVEST_FORESTLAND: "harvest_removals_forestland_cuft_per_year",
}


def five_county_estimates(
    query: Callable[[dict], dict] = evalidator_query,
    fetch_plan: bool = True,
) -> dict:
    """Estimate every ATTRIBUTE_SNUMS attribute over the five-county domain."""
    calls: list[dict] = []
    result: dict = {}
    for snum, name in ATTRIBUTE_SNUMS.items():
        params = {
            "snum": snum,
            "wc": EVAL_GRP,
            "rselected": "All live stocking",
            "cselected": "All live stocking",
            "wf": f"PLOT.COUNTYCD IN ({', '.join(str(c) for c in COUNTIES)})",
            "outputFormat": "JSON",
        }
        calls.append(params)
        value, se_pct, plots = total_cell(query(params))
        result[name] = {
            "snum": snum, "estimate": value, "se_pct": se_pct,
            "se": se_pct / 100 * value, "plots": plots,
        }
    if fetch_plan:
        result["plan_annual_harvest"] = plan_harvest_by_cycle()
    result["fetch_params_note"] = {
        "eval_grp": EVAL_GRP, "counties": list(COUNTIES),
        "domain_filter": calls[0]["wf"], "first_params": calls[0],
    }
    return result


def plan_harvest_by_cycle(_csv: str | None = None) -> list[dict]:
    """The annealed plan's harvest per 5-year cycle, annualized."""
    text = _csv if _csv is not None else PLAN_CSV.read_text()
    rows: list[dict] = []
    for row in csv.DictReader(io.StringIO(text)):
        cycle_cuft = float(row["cuft"])
        rows.append({
            "cycle": int(row["cycle"]),
            "calendar_year": int(row["calendar_year"]),
            "cycle_cuft": cycle_cuft,
            "annual_cuft": cycle_cuft / 5.0,
        })
    return rows


def removal_comparison(estimates: dict, plan: list[dict]) -> dict:
    """Frame the even-flow gate: plan's annual harvest vs FIA growth and removals."""
    removals = estimates["removals_timberland_cuft_per_year"]
    growth = estimates["net_growth_timberland_cuft_per_year"]
    plan_annual = max(row["annual_cuft"] for row in plan)
    lo = removals["estimate"] - 1.96 * removals["se"]
    hi = removals["estimate"] + 1.96 * removals["se"]
    return {
        "plan_annual_cuft": plan_annual,
        "fia_removals_cuft_per_year": removals["estimate"],
        "fia_removals_ci95": [lo, hi],
        "plan_over_fia_removals": plan_annual / removals["estimate"],
        "plan_within_fia_removals_ci": lo <= plan_annual <= hi,
        "fia_growth_cuft_per_year": growth["estimate"],
        "fia_growth_over_plan": growth["estimate"] / plan_annual,
        "plan_exceeds_growth": plan_annual > growth["estimate"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    result = five_county_estimates()
    comparison = removal_comparison(result, result["plan_annual_harvest"])
    result["even_flow_gate"] = comparison

    header = f"{'attribute':<44} {'estimate':>16} {'SE %':>7} {'plots':>7}"
    print(header)
    print("-" * len(header))
    for name in ATTRIBUTE_SNUMS.values():
        row = result[name]
        print(f"{name:<44} {row['estimate']:>16,.0f} {row['se_pct']:>6.2f}% {row['plots']:>7}")

    print()
    print(f"plan (2026-09-14) max annual harvest      : "
          f"{comparison['plan_annual_cuft']:>14,.0f} ft³/yr")
    print(f"FIA avg annual removals  (timberland)     : "
          f"{comparison['fia_removals_cuft_per_year']:>14,.0f} ft³/yr "
          f"(95% CI {comparison['fia_removals_ci95'][0]:,.0f} .. {comparison['fia_removals_ci95'][1]:,.0f})")
    print(f"plan / FIA removals                       : "
          f"{comparison['plan_over_fia_removals']:>14.2f}  "
          f"({'inside' if comparison['plan_within_fia_removals_ci'] else 'OUTSIDE'} the FIA CI)")
    print(f"FIA growth / plan                         : "
          f"{comparison['fia_growth_over_plan']:>14.2f}  "
          f"({'plan SUSTAINABLE vs growth' if not comparison['plan_exceeds_growth'] else 'plan EXCEEDS growth'})")

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2))
    print(f"\nwrote {args.out_json}")


if __name__ == "__main__":
    main()
