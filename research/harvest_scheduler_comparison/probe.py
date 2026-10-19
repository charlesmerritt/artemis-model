"""Probes behind ``docs/harvest-scheduler-comparison/presentation.html``.

    uv run python research/harvest_scheduler_comparison/probe.py [--ecotrust PATH]

Every probe runs, or reads, the code its claim is about and raises when the claim stops
holding. Ecotrust's scheduler is Python 2; its ``print`` statements are rewritten to calls on
load and nothing else is touched. ``--ecotrust`` is a clone of
https://github.com/Ecotrust/harvest-scheduler at ``bf4861f`` (the deleted C scheduler, BLM
driver and graph scheduler are read from that clone's history). ARTEMIS's side is the
``weekly-artifact/2026-09-14`` annealer, imported by path as ``2026-09-28`` does.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import importlib.util
import inspect
import io
import json
import math
import random
import re
import signal
import subprocess
import sys
import tokenize
import types
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

ECOTRUST_HEAD = "bf4861f"
ECOTRUST_MASTER_SCHEDULER = (ECOTRUST_HEAD, "harvestscheduler/_scheduler.py")
ECOTRUST_GRAPH_SCHEDULER = ("8df4969^", "scheduler/scheduler_graph.py")
ECOTRUST_BLM_DRIVER = ("fcc2e80^", "examples/example_schedule_blm.py")
ECOTRUST_C_SCHEDULER = ("6979128^", "alt_c/schedule.c")
ECOTRUST_C_HEADER = ("6979128^", "alt_c/schedule.h")

ARTEMIS_ANNEALER = REPO / "weekly-artifact/2026-09-14/make_annealed_plan.py"
ARTEMIS_QUALITY = REPO / "weekly-artifact/2026-09-14/solution_quality.json"
ARTEMIS_SEEDS = REPO / "weekly-artifact/2026-09-14/seed_spread.csv"
ARTEMIS_DRIVERS_CLAIMING_FOUR_FORMS = (
    REPO / "weekly-artifact/2026-08-31/make_annealed_plan.py",
    ARTEMIS_ANNEALER,
)

QUIET = {"report_interval": 10**9}


class ClaimFailed(AssertionError):
    """A deck claim no longer matches the code it was made about."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ClaimFailed(message)


# ---- loading Ecotrust's code -----------------------------------------------------------------

def git_show(repo: Path, rev: str, path: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{path}"],
                          capture_output=True, text=True, check=True).stdout


def as_python3(source: str) -> str:
    """Rewrite Python 2 ``print`` statements as calls, token by token.

    >>> as_python3('print "a", 1\\nprint\\n')
    'print( "a", 1)\\nprint()\\n'
    """
    out, open_calls, at_statement = [], 0, True
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.NAME and tok.string == "print" and at_statement:
            out.append(tok._replace(string="print("))
            open_calls += 1
            at_statement = False
            continue
        if tok.type == tokenize.NEWLINE and open_calls:
            out.append(tokenize.TokenInfo(tokenize.OP, ")", tok.start, tok.start, tok.line))
            open_calls -= 1
        out.append(tok)
        at_statement = tok.type in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                                    tokenize.DEDENT, tokenize.COMMENT)
    return tokenize.untokenize(out)


def load_ecotrust_scheduler(repo: Path, rev: str, path: str) -> types.ModuleType:
    sys.modules.setdefault("redis", types.SimpleNamespace(Redis=lambda: None))
    module = types.ModuleType(f"ecotrust_{rev}_{Path(path).stem}")
    exec(compile(as_python3(git_show(repo, rev, path)), f"{rev}:{path}", "exec"),
         module.__dict__)
    return module


def run_quiet(schedule, *args, seed: int, **kwargs):
    random.seed(seed)
    np.random.seed(seed)
    with contextlib.redirect_stdout(io.StringIO()):
        return schedule(*args, **QUIET, **kwargs)


def one_variable(strategy: str, **extra) -> dict:
    return {"variables": [{"name": "v", "strategy": strategy, "weight": 1.0, **extra}]}


def random_library(stands: int, mgmts: int, periods: int, variables: int, seed: int) -> np.ndarray:
    return np.random.RandomState(seed).randint(1, 50, size=(stands, mgmts, periods,
                                                             variables)).astype(float)


# ---- Ecotrust probes -------------------------------------------------------------------------

def warm_start_discarded(schedule) -> dict:
    """``starting_mgmts`` is overwritten by each stand's first valid management."""
    stands, mgmts = 20, 4
    data = random_library(stands, mgmts, 5, 1, seed=1)
    start = [mgmts - 1] * stands
    _, best, _ = run_quiet(schedule, data, one_variable("cumulative_maximize"),
                           [list(range(mgmts))] * stands, seed=0, steps=1,
                           starting_mgmts=start)
    reset = sum(m == 0 for m in best)
    check(reset >= stands - 1, f"warm start survived: {best}")
    return {"stands_given_last_mgmt": stands, "stands_returned_at_first_mgmt": reset}


def target_list_misaligned(schedule) -> dict:
    """An ``evenflow_target`` after a ``within_bounds`` variable reads a ``None`` target."""
    periods = 5
    data = random_library(10, 3, periods, 2, seed=2)
    bounds = {"name": "b", "strategy": "within_bounds", "weight": 1.0,
              "targets": ([0.0] * periods, [1e9] * periods)}
    target = {"name": "t", "strategy": "evenflow_target", "weight": 1.0, "targets": 100.0}
    valid = [[0, 1, 2]] * 10
    run_quiet(schedule, data, {"variables": [target, bounds]}, valid, seed=0, steps=5)
    try:
        run_quiet(schedule, data, {"variables": [bounds, target]}, valid, seed=0, steps=5)
    except TypeError as err:
        return {"order_target_then_bounds": "runs", "order_bounds_then_target": repr(err)}
    raise ClaimFailed("within_bounds before evenflow_target no longer raises")


def returned_totals_alias(schedule, seeds: int = 10) -> dict:
    """The per-period totals returned are not the totals of the returned best plan."""
    stands = 40
    mismatched = 0
    for seed in range(seeds):
        data = random_library(stands, 5, 6, 2, seed=100 + seed)
        variables = {"variables": [
            {"name": "a", "strategy": "cumulative_maximize", "weight": 1.0},
            {"name": "b", "strategy": "evenflow", "weight": 1.0}]}
        _, best, totals = run_quiet(schedule, data, variables, [list(range(5))] * stands,
                                    seed=seed, steps=3000, temp_min=0.01, temp_max=50)
        mismatched += not np.allclose(totals, data[np.arange(stands), best].sum(axis=0))
    check(mismatched > 0, "returned totals now match the returned plan on every seed")
    return {"seeds": seeds, "seeds_where_totals_differ_from_plan": mismatched}


def evenflow_scales_with_units(schedule) -> dict:
    """``evenflow`` is variance / range, so it grows with the unit; the other forms do not."""
    stands, periods = 12, 5
    data = random_library(stands, 3, periods, 1, seed=3)
    valid = [[0, 1, 2]] * stands
    ratio = {}
    for strategy, extra in (("evenflow", {}), ("cumulative_maximize", {}),
                            ("evenflow_target", {"targets": 60.0})):
        metrics = []
        for scale in (1.0, 10.0):
            scaled = {k: v * scale for k, v in extra.items()}
            best, _, _ = run_quiet(schedule, data * scale, one_variable(strategy, **scaled),
                                   valid, seed=7, steps=1)
            metrics.append(best)
        ratio[strategy] = metrics[1] / metrics[0]
    check(math.isclose(ratio["evenflow"], 10.0, rel_tol=1e-9), f"evenflow ratio {ratio}")
    check(math.isclose(ratio["cumulative_maximize"], 1.0, rel_tol=1e-9), f"{ratio}")
    check(math.isclose(ratio["evenflow_target"], 1.0, rel_tol=1e-9), f"{ratio}")
    return {"metric_ratio_when_data_x10": ratio}


def phantom_options_set_bounds(schedule) -> dict:
    """Zero-filled options a stand may not take still widen the 0-100 normalisation.

    Stand B may only grow (option 0), as a riparian stand's ``valid_mgmts`` says; prep fills
    its other option with zeros. The one move puts stand A on its low option either way.
    """
    a, b = [10.0, 2.0], 6.0
    clean = np.array([[[[a[0]]] * 5, [[a[1]]] * 5], [[[b]] * 5, [[b]] * 5]])
    phantom = clean.copy()
    phantom[1, 1] = 0.0
    valid = [[0, 1], [0]]
    variables = one_variable("cumulative_maximize")
    with_phantom, _, _ = run_quiet(schedule, phantom, variables, valid, seed=5, steps=1)
    without, _, _ = run_quiet(schedule, clean, variables, valid, seed=5, steps=1)
    check(math.isclose(without, 100.0) and with_phantom < without,
          f"phantom {with_phantom} vs clean {without}")
    return {"metric_with_zero_filled_option": with_phantom,
            "metric_same_feasible_set_no_phantom": without}


def hangs_without_a_choice(schedule) -> dict:
    """With no stand holding two valid managements the move loop never exits."""
    data = random_library(3, 2, 4, 1, seed=6)

    def timeout(*_):
        raise TimeoutError

    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(2)
    try:
        run_quiet(schedule, data, one_variable("cumulative_maximize"), [[0]] * 3,
                  seed=0, steps=10)
    except TimeoutError:
        return {"still_proposing_after_s": 2}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    raise ClaimFailed("schedule returned although no stand had a choice")


def linear_target_blind_to_spread(schedule) -> dict:
    """Below target, the linear forms score (5, 5) and (2, 8) the same; even flow does not."""
    target = 10.0
    metrics = {}
    for strategy, extra in (("evenflow_target", {"targets": target}),
                            ("within_bounds", {"targets": (target, 1e9)}),
                            ("evenflow", {})):
        for label, chosen in (("even", [5.0, 5.0]), ("uneven", [2.0, 8.0])):
            data = np.array([[[[0.0], [0.0]], [[v] for v in chosen]]])
            best, _, _ = run_quiet(schedule, data, one_variable(strategy, **extra),
                                   [[0, 1]], seed=0, steps=1)
            metrics[f"{strategy}:{label}"] = best
    for strategy in ("evenflow_target", "within_bounds"):
        check(math.isclose(metrics[f"{strategy}:even"], metrics[f"{strategy}:uneven"]),
              f"{strategy} now separates the spreads: {metrics}")
    check(metrics["evenflow:even"] < metrics["evenflow:uneven"], f"{metrics}")
    return metrics


def takes_no_seed(schedule) -> dict:
    """The schedule draws from the global ``random``/``np.random`` state; no seed argument."""
    params = list(inspect.signature(schedule).parameters)
    check("seed" not in params and "random_seed" not in params, f"{params}")
    return {"parameters": params}


# ---- Ecotrust history, read rather than run --------------------------------------------------

def blm_driver_configuration(repo: Path) -> dict:
    """The committed BLM driver: 502 mmbf at a 4.77% subset, a 6x band, a warm start."""
    src = git_show(repo, *ECOTRUST_BLM_DRIVER)
    check("from scheduler.scheduler_graph import schedule" in src, "driver import changed")
    check("mmbf_target = 502" in src and "mmbf_target*1000*0.0477*5" in src, "target changed")
    timber = re.search(r"'name': 'timber',(.*?)'weight': ([\d.]+)", src, re.S)
    band = (r"^\s*'strategy': 'within_bounds', 'targets': "
            r"\(\[period_target \* 0\.99\] \* 20, \[period_target \* 1\.05\] \* 20\),$")
    check(timber is not None and "#'strategy': 'evenflow_target'" in timber.group(1)
          and re.search(band, timber.group(1), re.M) is not None, "timber strategy changed")
    fire = re.search(r"'name': 'fire risk',(.*?)'weight'", src, re.S)
    check(fire is not None and "#'strategy': 'cumulative_minimize'" in fire.group(1)
          and "'targets': ([0] * 20, [15000] * 20)" in fire.group(1), "fire strategy changed")
    check("starting_mgmts=best_mgmts" in src, "warm start removed")
    steps = [int(s) for s in re.findall(r"steps=(\d+)", src)]
    return {"timber": "within_bounds [0.99T, 1.05T], weight " + timber.group(2),
            "fire_risk": "within_bounds [0, 15000]", "steps": steps}


def c_scheduler_design(repo: Path) -> dict:
    """The deleted C scheduler (adapted from John Sessions, OSU): what it optimised."""
    src = git_show(repo, *ECOTRUST_C_SCHEDULER)
    head = git_show(repo, *ECOTRUST_C_HEADER)
    const = {k: float(v) for k, v in re.findall(
        r"const (?:int|double|float) (\w+) = ([\d.]+);", head)}
    check("adapted from work led by John Sessions at OSU" in src, "provenance line moved")
    check("M1*pow(fabs(target_vol[tp]-vol_per_period[tp]),OBJ_EXPONENT)" in src
          and const["OBJ_EXPONENT"] == 2, "objective is no longer squared deviation")
    check("if (strcmp(own_type[cand_poly],ownership)!=0)" in src
          and 'Owner type must be PI, STATE, FED, or PNI' in src, "per-ownership run gone")
    check("offset=rand() % NOFF;" in src and "px=poly_biz_px[cand_poly];" in src,
          "the move no longer changes the offset alone")
    check(re.search(r"x_cutbfvol\[st_key\]\[px\]\[offset\]\[tp\] > CLEARCUT_TEST_THRESHHOLD\)"
                    r" &&\s*\(x_cutbfvol\[sta_key\]\[pxa\]\[offadj\]\[tp\] > "
                    r"CLEARCUT_TEST_THRESHHOLD\)\)\s*\{\s*goto try_nu_poly;", src) is not None,
          "adjacency is no longer a hard same-period rejection")
    levels = math.ceil(math.log(const["SA_MINIMUM_TEMP"] / const["SA_START_TEMPERATURE"])
                       / math.log(const["SA_TEMP_DECAY_PER_ITER"]))
    return {"adjacency_threshold_bdft_per_ac": const["CLEARCUT_TEST_THRESHHOLD"],
            "adjacency_min_acres": const["MIN_ACREAGE_FOR_ADJ_CHECK"],
            "min_advanced_age_pct": const["MIN_ADVANCED_PCT"],
            "cooling": (const["SA_START_TEMPERATURE"], const["SA_TEMP_DECAY_PER_ITER"],
                        const["SA_MINIMUM_TEMP"]),
            "proposals": levels * int(const["SA_POLY_TESTS_PER_TEMP"])}


# ---- ARTEMIS probes --------------------------------------------------------------------------

def load_artemis_annealer() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("artemis_annealer_20260914", ARTEMIS_ANNEALER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def toy_landscape(ap, per_cycle: dict[str, tuple[str, float, float]], n_cycles: int):
    """One stand per owner class, options ``on`` (volume = v) and ``short`` (v - dv)."""
    stands, library, cycles = [], [], []
    for i, (owner_class, (county, v, dv)) in enumerate(per_cycle.items()):
        plt = str(1000 + i)
        stands.append({"unit_id": f"u{i}", "PLT_CN": plt, "acres": 1.0, "county": county,
                       "owner_class": owner_class, "unit_class": "upland"})
        for presc, vol in (("on", v), ("short", v - dv)):
            library.append({"unit_id": f"u{i}", "prescription": presc})
            cycles += [{"PLT_CN": plt, "prescription": presc, "cycle": c,
                        "removed_merch_cuft_per_ac": vol, "MCuFt": 0.0}
                       for c in range(1, n_cycles + 1)]
    return ap.Landscape(pd.DataFrame(stands), pd.DataFrame(library), pd.DataFrame(cycles),
                        n_cycles)


def keeps_warm_start_and_best(ap) -> dict:
    """ARTEMIS's counterpart to E1 and E3: the seed survives and the best is a copy."""
    cfg = ap.load_config()
    caps = ap.tpo_caps(cfg["target_period"])
    land = toy_landscape(ap, {"federal": ("Baker", 2e6, 5e5), "family": ("Union", 3e6, 1e6)},
                         cfg["n_cycles"])
    obj = ap.Objective(land, caps, cfg)
    frozen = copy.deepcopy(cfg)
    frozen["anneal"].update(initial_temperature=1.0, min_temperature=2.0)
    kept = ap.anneal(land, obj, frozen, seed=0, initial=[1, 0])["choice"]
    check(kept == [1, 0], f"initial solution replaced: {kept}")
    searched = copy.deepcopy(cfg)
    searched["anneal"].update(initial_temperature=1.0, min_temperature=1e-3)
    run = ap.anneal(land, obj, searched, seed=0, initial=[1, 1])
    obj.reset(run["choice"])
    check(math.isclose(obj.total(), run["objective"], rel_tol=1e-12, abs_tol=1e-12),
          f"returned objective {run['objective']} != plan's {obj.total()}")
    return {"initial_kept_when_no_level_runs": kept, "best_objective_recomputes": True}


def owner_keys_weighted_by_inverse_square_target(ap) -> dict:
    """An equal shortfall costs (t_Private / t_Federal)^2 times more on Federal (NF)."""
    cfg = ap.load_config()
    cfg["dimensions"] = [ap.hs.OWNER]
    caps = ap.tpo_caps(cfg["target_period"])
    t_fed, t_pri = caps[ap.hs.OWNER]["Federal (NF)"], caps[ap.hs.OWNER]["Private"]
    shortfall = 0.25 * t_fed
    land = toy_landscape(ap, {"federal": ("Baker", t_fed, shortfall),
                              "family": ("Baker", t_pri, shortfall)}, cfg["n_cycles"])
    obj = ap.Objective(land, caps, cfg)
    obj.reset([0, 0])
    cost = {land.owners[land.owner_ix[i]]: obj.delta_and_apply(i, 0, 1, apply=False)
            for i in range(land.n)}
    ratio = cost["Federal (NF)"] / cost["Private"]
    check(math.isclose(ratio, (t_pri / t_fed) ** 2, rel_tol=1e-9), f"{ratio}")
    return {"per_cycle_target_cuft": {"Federal (NF)": t_fed, "Private": t_pri},
            "same_shortfall_cost_ratio_federal_over_private": ratio}


def quadratic_target_prefers_even_spread(ap) -> dict:
    """ARTEMIS's counterpart to E8: the same total shortfall costs more when uneven."""
    cfg = ap.load_config()
    cfg["dimensions"] = [ap.hs.OWNER]
    caps = ap.tpo_caps(cfg["target_period"])
    t = caps[ap.hs.OWNER]["Federal (NF)"]
    n = cfg["n_cycles"]
    land = toy_landscape(ap, {"federal": ("Baker", 0.0, 0.0)}, n)
    land.volumes = [[[0.5 * t] * n, [(0.2 if c % 2 else 0.8) * t for c in range(n)]]]
    land.standing = [[0.0, 0.0]]
    obj = ap.Objective(land, caps, cfg)
    cost = {}
    for label, k in (("even", 0), ("uneven", 1)):
        obj.reset([k])
        cost[label] = obj.total()
    check(cost["even"] < cost["uneven"], f"{cost}")
    return {"objective_even_half_shortfall": cost["even"],
            "objective_same_total_alternating": cost["uneven"]}


def configured_forms_are_not_read(ap) -> dict:
    """Changing a form or adding an objective in config leaves the objective untouched."""
    cfg = ap.load_config()
    caps = ap.tpo_caps(cfg["target_period"])
    land = toy_landscape(ap, {"federal": ("Baker", 1e6, 1e5), "family": ("Union", 3e6, 1e5)},
                         cfg["n_cycles"])
    land.standing = [[5.0, 9.0], [2.0, 4.0]]
    edited = copy.deepcopy(cfg)
    for objective in edited["objectives"]:
        if objective["metric"] == "standing_volume":
            objective["form"] = "minimize"
    edited["objectives"].append({"metric": "carbon_storage", "form": "maximize",
                                 "weight": 100.0})
    totals = []
    for config in (cfg, edited):
        obj = ap.Objective(land, caps, config)
        obj.reset([1, 0])
        totals.append(obj.total())
    check(totals[0] == totals[1], f"objective now reads forms: {totals}")
    claims = [p.name for p in ARTEMIS_DRIVERS_CLAIMING_FOUR_FORMS
              if "All four" in p.read_text() and "forms are implemented" in p.read_text()]
    penalty_defs = [m.group(0) for p in ARTEMIS_DRIVERS_CLAIMING_FOUR_FORMS
                    for m in re.finditer(r"def \w*(adjac|green|opening)\w*", p.read_text())]
    check(not penalty_defs, f"a spatial penalty function now exists: {penalty_defs}")
    return {"total_as_configured": totals[0], "total_after_edit": totals[1],
            "drivers_claiming_all_four_forms": len(claims),
            "spatial_penalty_functions_defined": 0}


def standing_term_is_a_tiebreak(ap) -> dict:
    """The standing term's whole range is w_standing; even flow moves by hundreds."""
    q = json.loads(ARTEMIS_QUALITY.read_text())
    w = q["objective_weights"]
    standing = q["objective_best"] - w["harvest_volume"] * q["objective_evenflow_term"]
    check(-w["standing_volume"] <= standing <= 0, f"standing term {standing}")
    worth = w["standing_volume"] / w["harvest_volume"]
    return {"standing_term_best": standing,
            "evenflow_term_weighted_best": w["harvest_volume"] * q["objective_evenflow_term"],
            "greedy_minus_best": q["objective_greedy_baseline"] - q["objective_best"],
            "whole_standing_range_in_sum_sq_rel_dev": worth,
            "one_target_miss_with_that_cost_pct": 100 * math.sqrt(worth)}


def iterations_count_every_stand(ap) -> dict:
    """Proposals per level are 10 x all stands, riparian single-option stands included."""
    q = json.loads(ARTEMIS_QUALITY.read_text())
    seeds = pd.read_csv(ARTEMIS_SEEDS)
    per_level = seeds["moves_proposed"] / seeds["temperature_levels"]
    per = q["cooling"]["iterations_per_temperature"]
    check(bool((per_level == per * q["stands"]).all()), f"{per_level.tolist()}")
    return {"proposals_per_level": int(per_level.iloc[0]), "stands": q["stands"],
            "stands_with_a_choice": q["stands_with_a_choice"],
            "proposals_per_decision_stand_per_level":
                per_level.iloc[0] / q["stands_with_a_choice"]}


# ---- runner ----------------------------------------------------------------------------------

def artemis_probes() -> list[tuple[str, callable]]:
    """Need only committed files, so they run anywhere the repository does."""
    ap = load_artemis_annealer()
    return [
        ("A0 warm start and best are kept", lambda: keeps_warm_start_and_best(ap)),
        ("A1 owner keys weighted 1/t^2", lambda: owner_keys_weighted_by_inverse_square_target(ap)),
        ("A2 configured forms not read", lambda: configured_forms_are_not_read(ap)),
        ("A3 standing term is a tie-break", lambda: standing_term_is_a_tiebreak(ap)),
        ("A4 iterations count every stand", lambda: iterations_count_every_stand(ap)),
        ("A5 quadratic target prefers an even spread",
         lambda: quadratic_target_prefers_even_spread(ap)),
    ]


def ecotrust_probes(ecotrust: Path) -> list[tuple[str, callable]]:
    master = load_ecotrust_scheduler(ecotrust, *ECOTRUST_MASTER_SCHEDULER).schedule
    graph = load_ecotrust_scheduler(ecotrust, *ECOTRUST_GRAPH_SCHEDULER).schedule
    return [
        ("E1 warm start discarded (master)", lambda: warm_start_discarded(master)),
        ("E1 warm start discarded (BLM-era graph scheduler)", lambda: warm_start_discarded(graph)),
        ("E2 target list misaligned", lambda: target_list_misaligned(master)),
        ("E3 returned totals alias a later state", lambda: returned_totals_alias(master)),
        ("E4 evenflow scales with units", lambda: evenflow_scales_with_units(master)),
        ("E5 phantom options set the bounds", lambda: phantom_options_set_bounds(master)),
        ("E6 no choice, no exit", lambda: hangs_without_a_choice(master)),
        ("E7 no seed parameter", lambda: takes_no_seed(master)),
        ("E8 linear targets blind to spread", lambda: linear_target_blind_to_spread(master)),
        ("H1 BLM driver configuration", lambda: blm_driver_configuration(ecotrust)),
        ("H2 C scheduler design", lambda: c_scheduler_design(ecotrust)),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ecotrust", type=Path,
                        default=Path.home() / "projects/harvest-scheduler",
                        help="clone of Ecotrust/harvest-scheduler with its history")
    args = parser.parse_args()
    head = subprocess.run(["git", "-C", str(args.ecotrust), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    if head != ECOTRUST_HEAD:
        print(f"note: {args.ecotrust} is at {head}, probes were written against {ECOTRUST_HEAD}")
    failed = 0
    for name, probe in ecotrust_probes(args.ecotrust) + artemis_probes():
        try:
            result = probe()
        except ClaimFailed as err:
            failed += 1
            print(f"FAIL {name}: {err}")
            continue
        print(f"ok   {name}: {json.dumps(result, default=float)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
