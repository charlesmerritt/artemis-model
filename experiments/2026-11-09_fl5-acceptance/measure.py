"""A leto run folder's measures against the FL5 golden run (2026-10-19), with #173's tolerances.

    uv run python experiments/2026-11-09_fl5-acceptance/measure.py <run_dir> [--json <out>]

Reads `stands.csv`, and `library.csv`, `plan.csv.gz` and `summary.json` when present. Prints a
table and optionally writes the measures as JSON. Exits 0 either way: misses are diagnosed by
hand, not gated.
"""

import argparse
import json
from pathlib import Path

import pandas as pd

from pipeline.ids import as_id_series

GOLDEN_UNKNOWN_ACRES = 17_340       # unknown-owner acres the golden run dropped, now scheduled
MEASURES = {                        # name: (golden, relative tolerance)
    "forest_acres": (1_174_862, 0.001),
    "young_plot_acres": (86_953, 0.01),
    "riparian_acres": (34_566, 0.05),
    "scheduled_acres": (1_157_293 + GOLDEN_UNKNOWN_ACRES, 0.01),
    "options_per_stand": (31.4, 0.10),
}
GOLDEN_UNITS, GOLDEN_OBJECTIVE = 114_727, 0.1033


def read_stands(run: Path) -> pd.DataFrame:
    stands = pd.read_csv(run / "stands.csv", dtype=str)
    stands["unit_id"] = as_id_series(stands["unit_id"])
    stands["acres"] = stands["acres"].astype(float)
    return stands


def measures(run: Path) -> dict:
    stands = read_stands(run)
    found = {
        "forest_acres": stands.acres.sum(),
        "young_plot_acres": stands.loc[stands.origin == "young_plot", "acres"].sum(),
        "riparian_acres": stands.loc[stands.unit_class == "riparian", "acres"].sum(),
        "units": len(stands),
        "unknown_owner_acres": stands.loc[stands.unknown_ownership == "True", "acres"].sum(),
    }
    if (run / "library.csv").exists():
        options = as_id_series(pd.read_csv(run / "library.csv", usecols=["unit_id"], dtype=str)["unit_id"])
        found["options"] = len(options)
        found["options_per_stand"] = len(options) / options.nunique()
    if (run / "plan.csv.gz").exists():
        plan = as_id_series(pd.read_csv(run / "plan.csv.gz", usecols=["unit_id"], dtype=str)["unit_id"])
        found["scheduled_acres"] = stands.loc[stands.unit_id.isin(set(plan)), "acres"].sum()
        found["objective"] = json.loads((run / "summary.json").read_text())["objective_best"]
    return found


def verdicts(found: dict) -> pd.DataFrame:
    rows = []
    for name, (golden, tolerance) in MEASURES.items():
        if name in found:
            deviation = found[name] / golden - 1
            rows.append({"measure": name, "run": round(found[name], 2), "golden": golden,
                         "deviation": f"{deviation:+.3%}", "tolerance": f"±{tolerance:.1%}",
                         "verdict": "pass" if abs(deviation) <= tolerance else "miss"})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    found = measures(args.run_dir)
    table = verdicts(found)
    print(table.to_string(index=False))
    print(f"units: {found['units']:,} (golden {GOLDEN_UNITS:,}; reported, not gated)")
    if "objective" in found:
        print(f"objective: {found['objective']:.4f} (golden {GOLDEN_OBJECTIVE}; for information)")
    if args.json:
        args.json.write_text(json.dumps({"measures": {k: float(v) for k, v in found.items()},
                                         "verdicts": table.to_dict("records")}, indent=2) + "\n")


if __name__ == "__main__":
    main()
