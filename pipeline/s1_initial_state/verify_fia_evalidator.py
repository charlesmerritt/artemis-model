"""Verify the FIA forest-area figure against the public EVALIDator API.

The FIA reconciliation in the report is the only genuinely independent check on
total corrected forest area, so the number behind it must not rest on one
hand-written SQL query against a local FIADB copy. This module re-derives it from
the USDA Forest Service FIADB-API (EVALIDator) and compares.

It also fetches the **domain** estimate rather than summing per-county rows.
That matters: county estimates within an evaluation share strata, so adding
their variances in quadrature assumes an independence that does not hold. Asking
EVALIDator for the five counties as a single domain returns the correct sampling
error, which turns out to be large enough to change what the reconciliation can
legitimately claim.

Requires network access; no Earth Engine or /mnt/d dependency.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data/interim/treemap_holes"

API = "https://apps.fs.usda.gov/fiadb-api/fullreport"
EVAL_GRP = "122022"  # Florida, 2022 evaluation group (EVALID 122201 = EXPCURR)
ATTRIBUTE_AREA_FOREST = 2  # "Area of forest land, in acres"
COUNTIES = (3, 23, 47, 121, 125)  # Baker, Columbia, Hamilton, Suwannee, Union

# From the pipeline; see docs/treemap_holes/README.md section 6.2.
TREEMAP_FOREST_ACRES = 1_094_685.7152
ADD_BACK_ACRES = 75_792.3632
LOCAL_SQL_ESTIMATE = 1_255_424.0  # what the local FIADB SQLite query returned


def query(params: dict) -> dict:
    url = f"{API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=300) as response:
        payload = response.read().decode()
    if "Error Page" in payload[:2000]:
        raise RuntimeError(f"EVALIDator returned an error page for {params}")
    return json.loads(payload)["EVALIDatorOutput"]


def total_cell(output: dict) -> tuple[float, float, int]:
    """(estimate acres, SE percent, contributing plots) from the Total/Total cell."""
    cell = next(c for c in output["row"][0]["column"] if c["content"] == "Total")
    return cell["cellValueNumerator"], cell["cellSE"], int(cell["cellPlotNumerator"])


def domain_estimate() -> tuple[float, float, int]:
    """Five counties as ONE domain, so the sampling error is computed correctly."""
    counties = ",".join(str(c) for c in COUNTIES)
    return total_cell(query({
        "snum": ATTRIBUTE_AREA_FOREST,
        "wc": EVAL_GRP,
        "rselected": "All live stocking",
        "cselected": "All live stocking",
        # The API requires a table-qualified column here; bare COUNTYCD errors.
        "wf": f"PLOT.COUNTYCD IN ({counties})",
        "outputFormat": "JSON",
    }))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", type=Path, default=DATA / "fia_evalidator_check.json")
    args = parser.parse_args()

    estimate, se_pct, plots = domain_estimate()
    se = se_pct / 100 * estimate
    ci_lo, ci_hi = estimate - 1.96 * se, estimate + 1.96 * se

    corrected = TREEMAP_FOREST_ACRES + ADD_BACK_ACRES
    shortfall = estimate - TREEMAP_FOREST_ACRES
    remaining = shortfall - ADD_BACK_ACRES
    delta = LOCAL_SQL_ESTIMATE - estimate

    print(f"EVALIDator 5-county domain : {estimate:>12,.0f} ac  "
          f"SE {se_pct:.3f}% (± {se:,.0f} ac), {plots} plots")
    print(f"local FIADB SQL query      : {LOCAL_SQL_ESTIMATE:>12,.0f} ac  "
          f"-> difference {delta:+,.2f} ac")
    print(f"95% CI                     : {ci_lo:,.0f} .. {ci_hi:,.0f} ac")
    print()
    print(f"TreeMap 2022, as published : {TREEMAP_FOREST_ACRES:>12,.0f} ac  "
          f"{'OUTSIDE' if TREEMAP_FOREST_ACRES < ci_lo else 'inside'} the 95% CI")
    print(f"corrected                  : {corrected:>12,.0f} ac  "
          f"{'inside' if ci_lo <= corrected <= ci_hi else 'OUTSIDE'} the 95% CI")
    print(f"shortfall                  : {shortfall:>12,.0f} ac  = {shortfall / se:.2f} SE  "
          f"({'significant' if shortfall > 1.96 * se else 'NOT significant'} at 95%)")
    print(f"remaining after correction : {remaining:>12,.0f} ac  = {remaining / se:.2f} SE  "
          f"({'significant' if remaining > 1.96 * se else 'NOT distinguishable from zero'})")
    print(f"overshoot                  : {'NO' if corrected < estimate else 'YES'}")

    result = {
        "evalidator_estimate_acres": estimate, "se_percent": se_pct, "se_acres": se,
        "plots": plots, "ci95_low": ci_lo, "ci95_high": ci_hi,
        "local_sql_estimate_acres": LOCAL_SQL_ESTIMATE, "difference_acres": delta,
        "treemap_forest_acres": TREEMAP_FOREST_ACRES, "corrected_acres": corrected,
        "shortfall_acres": shortfall, "shortfall_se_multiples": shortfall / se,
        "remaining_acres": remaining, "remaining_se_multiples": remaining / se,
        "treemap_outside_ci": bool(TREEMAP_FOREST_ACRES < ci_lo),
        "corrected_inside_ci": bool(ci_lo <= corrected <= ci_hi),
        "overshoot": bool(corrected > estimate),
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2))
    print(f"\nwrote {args.out_json}")


if __name__ == "__main__":
    main()
