"""Run reversible faults against the management audit's replacement assertions.

Run from the repository root:
    .venv/bin/python research/code_quality/management_rejection_probes.py

These are targeted rejection checks, separate from the mutmut CI score. Source
files remain untouched; each mock is restored before the next check.
"""

from copy import deepcopy
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BASE_REF = "066aee62e9f1aedf29177eda01b9655295a60443"


def _test_module(name, *, original=False):
    path = Path(__file__).resolve().parents[2] / "tests" / f"test_{name}.py"
    if original:
        source = subprocess.check_output(
            ["git", "show", f"{BASE_REF}:tests/test_{name}.py"], text=True,
        )
        module = ModuleType(f"original_{name}")
        module.__file__ = str(path)
        exec(compile(source, str(path), "exec"), module.__dict__)
        return module
    spec = importlib.util.spec_from_file_location(f"audit_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    sys.path.insert(0, str(ROOT))
    from pipeline.s1_initial_state import fia_volume_estimates
    from pipeline.s3_management import owner_classes, regime_assignment
    from pipeline.s6_outputs import age_class, owner_attribution

    assignment = _test_module("regime_assignment")
    library = _test_module("regime_library")
    fia = _test_module("fia_volume_estimates")
    ownership = _test_module("ownership_policy")
    outputs = _test_module("s6_fvs_outputs")
    original = {name: _test_module(name, original=True) for name in (
        "regime_assignment", "regime_library", "fia_volume_estimates",
        "ownership_policy", "s6_fvs_outputs",
    )}
    results = []

    def reject(name, fault, check, before):
        with fault:
            before()
            try:
                check()
            except AssertionError:
                results.append({"fault": name, "original": "survived", "replacement": "rejected"})
            else:
                raise AssertionError(f"replacement survived deliberate fault: {name}")
        check()

    config = deepcopy(regime_assignment.load_regimes_config())
    config["owner_classes"]["corporate"]["default"]["pine"] = "pine_plantation_long_rotation"
    reject("eligible alternative replaces corporate default",
           patch.object(regime_assignment, "load_regimes_config", return_value=config),
           assignment.test_corporate_pine_stand_takes_the_corporate_menu,
           original["regime_assignment"].test_corporate_pine_stand_takes_the_corporate_menu)
    reject("wrong Florida evaluation group",
           patch.object(fia_volume_estimates, "EVAL_GRP", "132022"),
           fia.test_query_hits_the_florida_2022_evaluation_group,
           original["fia_volume_estimates"].test_query_hits_the_florida_2022_evaluation_group)
    attributes = deepcopy(fia_volume_estimates.ATTRIBUTE_SNUMS)
    attributes[18] = "invented_inventory"
    reject("invented FIA attribute name",
           patch.object(fia_volume_estimates, "ATTRIBUTE_SNUMS", attributes),
           fia.test_estimates_carry_all_reported_attributes,
           original["fia_volume_estimates"].test_estimates_carry_all_reported_attributes)
    attributes = deepcopy(fia_volume_estimates.ATTRIBUTE_SNUMS)
    attributes[202], attributes[208] = attributes[208], attributes[202]
    reject("swapped FIA attribute numbers",
           patch.object(fia_volume_estimates, "ATTRIBUTE_SNUMS", attributes),
           fia.test_estimates_carry_all_reported_attributes,
           original["fia_volume_estimates"].test_estimates_carry_all_reported_attributes)
    reject("empty shipped prescription library",
           patch.object(library, "load_library", return_value={"regimes": {}}),
           library.test_shipped_library_names_only_harris_owner_classes,
           original["regime_library"].test_shipped_library_names_only_harris_owner_classes)
    regimes = {"one": {"owner_classes": []}}
    reject("prescription without any owner",
           patch.object(library, "load_library", return_value={"regimes": regimes}),
           library.test_shipped_library_names_only_harris_owner_classes,
           original["regime_library"].test_shipped_library_names_only_harris_owner_classes)
    reject("self-consistent changed masked label",
           patch.object(owner_classes, "MASKED", "water_is_an_owner"),
           lambda: ownership.test_masked_values_are_excluded_from_the_fvs_pipeline(
               owner_classes.load_ownership_policy()),
           lambda: original["ownership_policy"].test_masked_values_are_excluded_from_the_fvs_pipeline(
               owner_classes.load_ownership_policy()))

    with tempfile.TemporaryDirectory(prefix="management-audit-") as directory:
        folder = Path(directory)
        database = outputs.fvs_out.__wrapped__(folder)
        crosswalk = outputs.crosswalk.__wrapped__(folder)
        reject("self-consistent changed sampling-weight label",
               patch.object(age_class, "SAMPLING_WEIGHT", "invented_weight"),
               lambda: outputs.test_every_table_is_acre_weighted_and_foots_to_the_run_total(database),
               lambda: original["s6_fvs_outputs"].test_every_table_is_acre_weighted_and_foots_to_the_run_total(database))
        overall = age_class.overall
        reject("empty overall distribution",
               patch.object(age_class, "overall", side_effect=lambda *a: overall(*a).iloc[:0]),
               lambda: outputs.test_every_table_is_acre_weighted_and_foots_to_the_run_total(database),
               lambda: original["s6_fvs_outputs"].test_every_table_is_acre_weighted_and_foots_to_the_run_total(database))
        by_type = age_class.by_forest_type
        reject("empty forest-type shares",
               patch.object(age_class, "by_forest_type", side_effect=lambda *a: by_type(*a).iloc[:0]),
               lambda: outputs.test_shares_are_computed_within_a_cut_not_across_the_run(database),
               lambda: original["s6_fvs_outputs"].test_shares_are_computed_within_a_cut_not_across_the_run(database))
        shares = owner_attribution.load_owner_shares
        reject("missing owner plot shares",
               patch.object(owner_attribution, "load_owner_shares",
                            side_effect=lambda *a: shares(*a).query("Stand_CN != '1002'")),
               lambda: outputs.test_owner_shares_sum_to_one_within_a_plot(crosswalk),
               lambda: original["s6_fvs_outputs"].test_owner_shares_sum_to_one_within_a_plot(crosswalk))
        apportion = owner_attribution.apportion

        @contextmanager
        def wrong_owner_basis():
            with patch.object(age_class, "OWNER_ACRES", "invented_owner_acres"), patch.object(
                owner_attribution, "apportion", side_effect=lambda *a: apportion(*a).rename(
                    columns={"owner_acres": "invented_owner_acres"}),
            ):
                yield

        reject("self-consistent changed owner-acre label",
               wrong_owner_basis(),
               lambda: outputs.test_owner_tables_carry_the_owner_acre_basis_not_the_sampling_weight(
                   database, crosswalk),
               lambda: original["s6_fvs_outputs"].test_owner_tables_carry_the_owner_acre_basis_not_the_sampling_weight(
                   database, crosswalk))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
