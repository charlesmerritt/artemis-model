"""Run data-free examples of current policy and display checked-in research evidence.

Usage: uv run python notes/check.py
This is not end-to-end FVS/data validation or a new run of the archived experiments.
"""

from __future__ import annotations

import argparse
import csv
import doctest
import sys
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Direct script execution needs the repository root before local imports.
from pipeline import ids, spatial_ref  # noqa: E402
from pipeline.s3_management import owner_classes, regime_assignment  # noqa: E402
from pipeline.s4_fvs import regime_library, regime_templates  # noqa: E402


class Evidence(Enum):
    SUMMARY = "summary"
    FULL = "full"


def check_policy() -> None:
    """Exercise configured menus, defaults and geometric exclusions without input data."""
    config = regime_assignment.load_regimes_config()
    ownership = owner_classes.load_ownership_policy()
    regime_assignment.main()
    for owner, spec in config["owner_classes"].items():
        for branch in ("pine", "hardwood", "other"):
            menu = regime_assignment.eligible_prescriptions(owner, branch, config)
            if "no_management" not in menu or spec["default"][branch] not in menu:
                raise ValueError(f"{owner}/{branch}: missing unmanaged choice or ineligible default")
    override = config["overrides"]["riparian"]
    for spec in ownership["classes"].values():
        for code in spec["harris_values"]:
            if code in ownership["masked_harris_values"]:
                continue
            unit = {"OWN_CODE": code, override["field"]: override["min_value"]}
            prescription = regime_assignment.assign_prescription(unit, config=config)
            if prescription.template != "no_management" or prescription.regen_slot is not None:
                raise ValueError(f"riparian ownership code {code}: management was assigned")
            if regime_templates.build_thins(prescription.template, prescription.params):
                raise ValueError("riparian prescription rendered a harvest")
    library = regime_library.validate_library(regime_library.load_library())
    for name in regime_library.regime_names(library):
        regime_library.render_keyfile("example", "example", name, library=library)
    print(f"ok   rendered {len(library['regimes'])} configured harvest-library regimes")
    print(f"Library constraints (config/regimes.yaml): {library['constraints']}")
    spatial_ref.assert_projected_metres(spatial_ref.project_crs())
    print(f"Spatial reference: {spatial_ref.crs_label()}; resolution={spatial_ref.resolution_m()} m")
    print(f"Snap transform: {spatial_ref.snap_transform()}")


def show_evidence(mode: Evidence) -> None:
    """Read evidence in place; its dates and fixture limits remain attached to the results."""
    print("\nChecked-in archived evidence; not rerun, not landscape-wide validation:")
    restart = ROOT / "research/restart_fidelity/outputs"
    for path in sorted(restart.glob("*.txt")):
        record = path.read_text().strip()
        print(f"\n{path.relative_to(ROOT)}")
        print(record if mode is Evidence.FULL else record.splitlines()[0])
    path = ROOT / "research/fia_treemap_fortype/outputs/FL_state_level_scaling_comparison.csv"
    print(f"\n{path.relative_to(ROOT)} (state-level aggregate comparison only)")
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            print(f"{row['metric']}: raw difference={row['raw_pct_diff']}%; "
                  f"scaled difference={row['scaled_pct_diff']}%; "
                  f"absolute difference improved={row['abs_improved']}")
    print("Reproduction: research/restart_fidelity/compare_arms.py; "
          "research/fia_treemap_fortype/compare.R")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", action="store_const", const=Evidence.FULL,
                        default=Evidence.SUMMARY,
                        help="print complete dated restart records, including fixture limitations")
    args = parser.parse_args(argv)
    failures = 0
    for module in (ids, spatial_ref, owner_classes, regime_assignment, regime_templates, regime_library):
        result = doctest.testmod(module, optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
        print(f"{module.__name__}: {result.attempted} examples, {result.failed} failures")
        failures += result.failed
    try:
        check_policy()
        show_evidence(args.evidence)
    except (ValueError, KeyError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
