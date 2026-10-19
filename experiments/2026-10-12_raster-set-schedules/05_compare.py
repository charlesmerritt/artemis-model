"""Step 5 — one row per landscape: what the repaired rasters change about supply and the plan.

Reads each set's `work/<set>/landscape_summary.json` and `outputs/<set>/{summary.json,
capacity.csv}`, and `outputs/fia_by_county.csv`. Writes `outputs/comparison.csv` and
`outputs/suwannee.csv` (Suwannee in every landscape beside FIA's own estimates and TPO).

    uv run python experiments/2026-10-12_raster-set-schedules/05_compare.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import yaml

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
sys.path[:0] = [str(EXP), str(REPO)]

import raster_sets as RS  # noqa: E402

TPO = yaml.safe_load((REPO / "config/tpo_targets.yaml").read_text())
SUWANNEE_TPO = "Suwanee"          # the TPO workbook's spelling


def row(rs: RS.RasterSet) -> dict:
    land = json.loads((RS.work_dir(rs) / "landscape_summary.json").read_text())
    plan = json.loads((RS.output_dir(rs) / "summary.json").read_text())
    cap = pd.read_csv(RS.output_dir(rs) / "capacity.csv").set_index("key")
    out = {
        "raster_set": rs.name, "label": rs.label, "stands": land["stands"],
        "forest_acres": land["acres"], "added_back_young_acres": land["scaled_young_acres"],
        "suwannee_acres": land["acres_by_county"]["Suwannee"],
        "suwannee_young_acres": land["scaled_young_acres_by_county"].get("Suwannee", 0.0),
        "options_per_stand": plan["options_per_stand"], "objective_best": plan["objective_best"],
        "evenflow_cost": plan["evenflow_cost"],
        "landscape_mean_dev_pct": plan["landscape_mean_dev_pct"],
        "owner_mean_abs_dev_pct": plan["owner_mean_abs_dev_pct"],
        "county_mean_abs_dev_pct": plan["county_mean_abs_dev_pct"],
    }
    for key, tag in ((SUWANNEE_TPO, "suwannee"), ("Other public", "other_public")):
        out[f"{tag}_max_pct_of_tpo"] = cap.at[key, "max_pct_of_target"]
        out[f"{tag}_plan_pct_of_tpo"] = cap.at[key, "plan_pct_of_target"]
    out["suwannee_start_merch_cuft"] = cap.at[SUWANNEE_TPO, "start_merch_cuft"]
    return out


def main() -> None:
    table = pd.DataFrame([row(rs) for rs in RS.ALL])
    table.to_csv(RS.OUTPUTS / "comparison.csv", index=False)

    fia = pd.read_csv(RS.OUTPUTS / "fia_by_county.csv").set_index("county").loc["Suwannee"]
    tpo = TPO["by_county"][SUWANNEE_TPO]
    suw = table[["label", "suwannee_acres", "suwannee_young_acres", "suwannee_start_merch_cuft",
                 "suwannee_max_pct_of_tpo", "suwannee_plan_pct_of_tpo"]].copy()
    suw["fia_forest_acres"] = fia["forest_area_ac"]
    suw["fia_growing_stock_cuft"] = fia["growing_stock_cuft"]
    suw["fia_net_growth_cuft_per_yr"] = fia["net_growth_cuft_per_yr"]
    suw["fia_removals_cuft_per_yr"] = fia["removals_cuft_per_yr"]
    suw["tpo_2013_2024_cuft_per_yr"] = tpo["2013_2024"]
    suw["tpo_pre_2015_cuft_per_yr"] = tpo["pre_2015"]
    suw.to_csv(RS.OUTPUTS / "suwannee.csv", index=False)
    cols = ["label", "forest_acres", "added_back_young_acres", "suwannee_acres", "evenflow_cost",
            "landscape_mean_dev_pct", "suwannee_max_pct_of_tpo", "suwannee_plan_pct_of_tpo",
            "other_public_plan_pct_of_tpo", "objective_best"]
    print(table[cols].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
