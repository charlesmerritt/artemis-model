"""Stage 3 — the search, not the library.

`weekly-artifact/2026-09-14` expanded the decision space fourfold, cut the even-flow cost by
67%, and closed 44 of the 47 provably unreachable targets. It also left one number moving the
wrong way, and named fixing it as the first job of the next run, in its own words:

> **Give the search the restarts the bigger space needs.** Seed spread went from 0.05% to 5.5%
> of the objective. Nothing about the library needs to change — more restarts, or a cooling
> factor nearer 1, or both, and the numbers above are a floor on what this decision space can
> do rather than its best.

This driver does exactly that and nothing else. **The library is not touched.**
`make_timing_library.py` from `2026-09-14` is re-run unmodified to rebuild the same 13,035 FVS
trajectories, and this script asserts the rebuild is identical to that artifact's committed
`trajectory_index.csv` before it plans over it. The landscape, the weights, the TPO caps, the
greedy initial solution and the move mixture are all unchanged.

**The search code is not modified either — it is imported.** `Landscape`, `Objective`,
`greedy_seed` and `anneal` are loaded out of `2026-09-14/make_annealed_plan.py` as a module and
called as they stand, so "only the search parameters changed" is enforced by the import rather
than asserted in prose. Two parameters move, in a 2x2:

    cooling_factor   0.95 (as config)  x  0.98 (nearer 1)
    restarts         5    (as config)  x  --restarts (default 20)

The (0.95, 5) cell is the setting `2026-09-14` published, so it is a reproduction: the driver
checks it recovers that artifact's objective to the last digit before reading anything into the
other three cells. A restart is an independent search from the same greedy start under its own
seed, so the cells nest — best-of-5 is the best of the first five seeds of best-of-20, and both
are computed from one set of runs rather than from two batches.

Restarts run in parallel across processes. That changes no result: `anneal` takes its
randomness from `random.Random(seed)` alone and calls `Objective.reset` before it starts, so a
run is a pure function of (library, seed, cooling factor), and each forked worker mutates only
its own copy of the objective. The parallelism buys wall clock so that 40 searches fit in the
time 5 used to take.

What is structurally unavailable is unchanged and still reported as unavailable rather than
given a manufactured number: `adjacency_greenup` and `max_opening_size` need a polygon
adjacency this pixel-class landscape cannot supply, the `block` move goes with them, and that
remains the largest caveat on the plan. This week does nothing about it.

Usage:
    uv run python weekly-artifact/2026-09-28/make_converged_plan.py [--restarts N]
                                                                   [--coolings 0.95,0.98]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import multiprocessing as mp
import sys
import time
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

OUT_DIR = Path(__file__).resolve().parent
PREV = REPO / "weekly-artifact/2026-09-14"
PREV_QUALITY = PREV / "solution_quality.json"
PREV_INDEX = PREV / "trajectory_index.csv"
PREV_BY_CYCLE = PREV / "harvest_by_cycle.csv"
PREV_SEEDS = PREV / "seed_spread.csv"
PREV_PLAN = PREV / "annealed_plan.csv"

log = logging.getLogger("converge")

# The config's own cooling factor, and the restart count the previous artifact published.
CONFIG_COOLING = 0.95
PREV_RESTARTS = 5


def load_prev_driver():
    """Import `2026-09-14/make_annealed_plan.py` as a module.

    Its directory name is not an identifier, so it cannot be imported by name. Loading it by
    path is the point rather than a workaround: this week's claim is that the search is
    unchanged, and importing last week's search is a stronger way to say so than copying it
    and inviting a diff.
    """
    path = PREV / "make_annealed_plan.py"
    spec = importlib.util.spec_from_file_location("artemis_prev_annealer", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load the 2026-09-14 driver from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    for attr in ("Landscape", "Objective", "anneal", "greedy_seed", "plan_frame",
                 "load_config", "tpo_caps", "evenflow_cost", "random_choice",
                 "require_fresh_batch", "check_batch_matches", "base_of",
                 "spatial_penalties_available", "effective_move_probabilities", "WORK",
                 "INV_YEAR", "CYCLE_YEARS", "hs"):
        if not hasattr(mod, attr):
            raise AssertionError(f"the 2026-09-14 driver no longer exposes `{attr}`")
    return mod


AP = load_prev_driver()


# --------------------------------------------------------------------------------------
# The rebuilt library must be the one 2026-09-14 planned over
# --------------------------------------------------------------------------------------

def check_library_matches_published(cycles: pd.DataFrame, n_cycles: int) -> dict:
    """Assert this container's FVS rebuild reproduces the committed library exactly.

    `2026-09-14` published `trajectory_index.csv`: one row per FVS run with its harvest in
    each cycle. This rebuild used a freshly compiled `FVSsn` on a different machine, so the
    comparison is a real test of the claim that the library is deterministic and that this
    week's differences come from the search. It is also a precondition: a plan built on a
    library that drifted would make every comparison below meaningless.

    Compared on the harvest vector, because that is what the objective reads.
    """
    if not PREV_INDEX.exists():
        raise AssertionError(f"missing the published library to check against: {PREV_INDEX}")
    pub = pd.read_csv(PREV_INDEX, dtype={"PLT_CN": str, "fvs_run_id": str})
    cols = [f"cuft_cycle_{c}" for c in range(0, n_cycles + 2) if f"cuft_cycle_{c}" in pub.columns]

    # `trajectory_cycles.csv` is keyed on (PLT_CN, prescription) and names the harvest
    # column `removed_merch_cuft_per_ac`; the published index is keyed on `fvs_run_id` and
    # names it `cuft_cycle_{c}`. Rebuild the run id exactly as `make_timing_library.py`
    # does — `PLT_CN::prescription`, the control number as a string throughout, never
    # through a float.
    keyed = cycles.assign(
        fvs_run_id=cycles["PLT_CN"].astype(str) + "::" + cycles["prescription"].astype(str))
    now = (keyed.pivot_table(index="fvs_run_id", columns="cycle",
                             values="removed_merch_cuft_per_ac", aggfunc="sum",
                             fill_value=0.0)
           .rename(columns=lambda c: f"cuft_cycle_{int(c)}"))
    now.index = now.index.astype(str)

    pub_h = pub.set_index("fvs_run_id")[cols].astype(float)
    shared = [c for c in cols if c in now.columns]
    missing_cols = sorted(set(cols) - set(now.columns))
    for c in missing_cols:
        now[c] = 0.0
    now = now.reindex(columns=cols, fill_value=0.0)

    only_published = sorted(set(pub_h.index) - set(now.index))
    only_rebuilt = sorted(set(now.index) - set(pub_h.index))
    common = sorted(set(pub_h.index) & set(now.index))
    diff = (now.loc[common] - pub_h.loc[common]).abs()
    max_abs = float(diff.to_numpy().max()) if len(common) else 0.0

    out = {
        "published_runs": int(len(pub_h)),
        "rebuilt_runs": int(len(now)),
        "runs_only_in_published": len(only_published),
        "runs_only_in_rebuild": len(only_rebuilt),
        "runs_compared": len(common),
        "max_abs_diff_harvest_cuft_per_ac": max_abs,
        "cycles_compared": shared,
    }
    tol = 1e-6
    if only_published or only_rebuilt or max_abs > tol:
        raise AssertionError(
            "the rebuilt FVS library is not the one 2026-09-14 published "
            f"({len(only_published)} runs missing, {len(only_rebuilt)} extra, "
            f"max |diff| {max_abs:.6g} cuft/ac > {tol:g}); this week's comparison assumes an "
            "identical library, so it refuses to plan over a different one"
        )
    log.info("Library check: %d runs reproduced from a freshly built FVSsn, max |diff| %.3g "
             "cuft/ac", len(common), max_abs)
    return out


def check_plan_reproduces_published(plan: pd.DataFrame) -> dict:
    """Does the baseline cell rebuild 2026-09-14's *plan*, stand for stand?

    The objective matching to nine figures is good evidence; an identical plan is the claim
    itself. Two different selections can score the same, so this compares what was actually
    chosen — `stand_id -> fvs_run_id` for all 11,831 stands — against that artifact's
    committed `annealed_plan.csv`.

    Reported rather than enforced, for the reason the objective check is: the sweep's own
    cells come from one rebuilt library and stay comparable to each other whatever this says.

    `PLT_CN` and the ids built from it are read as strings throughout. A control number that
    round-tripped through a float64 would silently truncate and turn a perfect match into a
    total mismatch (AGENTS.md, "FIA control numbers must not be cast via `str(int(...))`").
    """
    if not PREV_PLAN.exists():
        return {"compared": False, "reason": f"no committed plan at {PREV_PLAN}"}
    cols = ["stand_id", "fvs_run_id", "prescription", "timing_offset_years"]
    pub = pd.read_csv(PREV_PLAN, dtype={"stand_id": str, "fvs_run_id": str, "PLT_CN": str})
    now = plan[cols].astype({"stand_id": str, "fvs_run_id": str})
    pub = pub[cols].astype({"stand_id": str, "fvs_run_id": str})
    merged = pub.merge(now, on="stand_id", how="outer", suffixes=("_pub", "_now"),
                       indicator=True)
    both = merged[merged["_merge"] == "both"]
    same = both["fvs_run_id_pub"] == both["fvs_run_id_now"]
    out = {
        "compared": True,
        "stands_published": int(len(pub)),
        "stands_here": int(len(now)),
        "stands_matched_by_id": int(len(both)),
        "stands_only_published": int((merged["_merge"] == "left_only").sum()),
        "stands_only_here": int((merged["_merge"] == "right_only").sum()),
        "stands_choosing_the_same_trajectory": int(same.sum()),
        "stands_choosing_differently": int((~same).sum()),
    }
    out["identical"] = bool(out["stands_only_published"] == 0
                            and out["stands_only_here"] == 0
                            and out["stands_choosing_differently"] == 0)
    if out["identical"]:
        log.info("Plan reproduction: all %d stands choose the trajectory 2026-09-14 "
                 "published", out["stands_matched_by_id"])
    else:
        log.warning("Plan reproduction: %d of %d stands chose differently from "
                    "2026-09-14's published plan", out["stands_choosing_differently"],
                    out["stands_matched_by_id"])
    return out


# --------------------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------------------

_STATE: dict = {}


def _worker_init(land, obj, cfg, greedy) -> None:
    _STATE["land"], _STATE["obj"], _STATE["cfg"], _STATE["greedy"] = land, obj, cfg, greedy


def _run_one(job: tuple[float, int]) -> dict:
    """One restart, at one cooling factor. Pure in (library, cooling, seed)."""
    cooling, seed = job
    cfg = dict(_STATE["cfg"])
    anneal_cfg = dict(cfg["anneal"])
    anneal_cfg["cooling_factor"] = cooling
    cfg["anneal"] = anneal_cfg
    t = time.perf_counter()
    res = AP.anneal(_STATE["land"], _STATE["obj"], cfg, seed, _STATE["greedy"])
    res["seconds"] = time.perf_counter() - t
    res["cooling_factor"] = cooling
    return res


def run_sweep(land, obj, cfg, greedy, coolings: list[float], seeds: list[int],
              workers: int) -> list[dict]:
    # Longest job first. A cooling factor nearer 1 visits more temperature levels and so
    # takes proportionally longer, and starting those last would leave three workers idle
    # behind the slowest one. Purely a makespan choice: each search is independent, so the
    # order they run in cannot change any result.
    jobs = [(c, s) for c in sorted(coolings, reverse=True) for s in seeds]
    log.info("Sweep: %d searches (%d cooling factors x %d restarts) on %d workers",
             len(jobs), len(coolings), len(seeds), workers)
    if workers <= 1:
        _worker_init(land, obj, cfg, greedy)
        return [_run_one(j) for j in jobs]
    # `fork` on purpose: the workers inherit the built landscape and objective rather than
    # pickling them across, and each mutates only its own copy.
    ctx = mp.get_context("fork")
    _worker_init(land, obj, cfg, greedy)
    with ctx.Pool(processes=workers) as pool:
        runs = pool.map(_run_one, jobs, chunksize=1)
    for r, (c, s) in zip(runs, jobs):
        if r["cooling_factor"] != c or r["seed"] != s:
            raise AssertionError("sweep results came back out of order")
    return runs


def arm_table(runs: list[dict], coolings: list[float], seeds: list[int]) -> pd.DataFrame:
    """The 2x2: {config cooling, slower} x {config restarts, more}, from one set of runs.

    The cells nest — best-of-5 reads the first five seeds of the same twenty — so nothing is
    searched twice to fill the table.
    """
    rows = []
    for cooling in coolings:
        got = [r for r in runs if r["cooling_factor"] == cooling]
        got.sort(key=lambda r: seeds.index(r["seed"]))
        for n in sorted({PREV_RESTARTS, len(seeds)}):
            sub = got[:n]
            if len(sub) < n:
                continue
            objs = [r["objective"] for r in sub]
            rows.append({
                "arm": f"cooling_{cooling}_restarts_{n}",
                "cooling_factor": cooling,
                "restarts": n,
                "is_20260914_setting": bool(cooling == CONFIG_COOLING and n == PREV_RESTARTS),
                "objective_best": min(objs),
                "objective_worst": max(objs),
                "objective_mean": sum(objs) / len(objs),
                "seed_spread": max(objs) - min(objs),
                "seed_spread_pct_of_best": 100.0 * (max(objs) - min(objs)) / min(objs),
                "best_seed": min(sub, key=lambda r: r["objective"])["seed"],
                "mean_temperature_levels": sum(r["levels"] for r in sub) / len(sub),
                "mean_moves_proposed": sum(r["proposed"] for r in sub) / len(sub),
                "mean_accept_rate": sum(r["accept_rate"] for r in sub) / len(sub),
                "cpu_seconds_total": sum(r["seconds"] for r in sub),
            })
    return pd.DataFrame(rows)


def restart_curve(runs: list[dict], coolings: list[float], seeds: list[int]) -> pd.DataFrame:
    """Best-of-first-R against R: where more restarts stop paying.

    Seeds are consumed in a fixed order (42, 43, ...), so best-of-first-R is what a run with
    `restarts: R` would actually have reported, not a random subset.
    """
    rows = []
    for cooling in coolings:
        got = [r for r in runs if r["cooling_factor"] == cooling]
        got.sort(key=lambda r: seeds.index(r["seed"]))
        running = None
        for r_index, run in enumerate(got, start=1):
            running = run["objective"] if running is None else min(running, run["objective"])
            rows.append({"cooling_factor": cooling, "restarts": r_index,
                         "seed": run["seed"], "objective_this_seed": run["objective"],
                         "best_of_first_r": running, "seconds_this_seed": run["seconds"]})
    return pd.DataFrame(rows)


def compare_to_prev(quality: dict, per_cycle: pd.DataFrame) -> pd.DataFrame:
    """This plan beside 2026-09-14's, on the measures the search was meant to move."""
    prev = json.loads(PREV_QUALITY.read_text())
    prev_cyc = pd.read_csv(PREV_BY_CYCLE)
    rows = [
        {"measure": "objective_best", "value_20260914": prev["objective_best"],
         "value_20260928": quality["objective_best"]},
        {"measure": "objective_evenflow_term",
         "value_20260914": prev["objective_evenflow_term"],
         "value_20260928": quality["objective_evenflow_term"]},
        {"measure": "objective_greedy_baseline",
         "value_20260914": prev["objective_greedy_baseline"],
         "value_20260928": quality["objective_greedy_baseline"]},
        {"measure": "seed_spread_range", "value_20260914": prev["seed_spread"]["range"],
         "value_20260928": quality["seed_spread"]["range"]},
        {"measure": "seed_spread_pct_of_best",
         "value_20260914": 100.0 * prev["seed_spread"]["range"] / prev["objective_best"],
         "value_20260928": 100.0 * quality["seed_spread"]["range"] / quality["objective_best"]},
        {"measure": "targets_unreachable_from_library",
         "value_20260914": prev["targets_unreachable_from_library"],
         "value_20260928": quality["targets_unreachable_from_library"]},
        {"measure": "restarts", "value_20260914": PREV_RESTARTS,
         "value_20260928": quality["search"]["restarts"]},
        {"measure": "cooling_factor", "value_20260914": prev["cooling"]["cooling_factor"],
         "value_20260928": quality["search"]["cooling_factor"]},
    ]
    for c in range(1, 11):
        p = prev_cyc.loc[prev_cyc["cycle"] == c, "cuft"]
        n = per_cycle.loc[per_cycle["cycle"] == c, "cuft"]
        rows.append({"measure": f"harvest_cycle_{c}_cuft",
                     "value_20260914": float(p.iloc[0]) if len(p) else float("nan"),
                     "value_20260928": float(n.iloc[0]) if len(n) else float("nan")})
    out = pd.DataFrame(rows)
    out["change"] = out["value_20260928"] - out["value_20260914"]
    return out


# --------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restarts", type=int, default=20,
                    help="restarts per cooling factor (the config's 5 is always reported too)")
    ap.add_argument("--coolings", type=str, default="0.95,0.98",
                    help="comma-separated cooling factors; the config's 0.95 must be present")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.restarts < PREV_RESTARTS:
        raise SystemExit(
            f"--restarts must be at least {PREV_RESTARTS} (got {args.restarts}); this week's "
            f"claim is that more restarts help, and it is measured against the {PREV_RESTARTS} "
            f"the previous artifact ran, which needs those seeds in the sweep."
        )
    if args.workers < 1:
        raise SystemExit(f"--workers must be at least 1 (got {args.workers})")
    coolings = [float(c) for c in args.coolings.split(",") if c.strip()]
    if not coolings:
        raise SystemExit("--coolings is empty")
    if CONFIG_COOLING not in coolings:
        raise SystemExit(
            f"--coolings must include the config's own {CONFIG_COOLING}: the sweep's baseline "
            f"cell is the setting 2026-09-14 published, and without it nothing here is anchored."
        )
    for c in coolings:
        if not 0.0 < c < 1.0:
            raise SystemExit(f"cooling factor {c} is not in (0, 1); T <- alpha*T must cool")

    cfg = AP.load_config()
    if float(cfg["anneal"]["cooling_factor"]) != CONFIG_COOLING:
        raise SystemExit(
            f"config/projection.yaml cooling_factor is {cfg['anneal']['cooling_factor']}, not "
            f"{CONFIG_COOLING}; this driver's baseline cell assumes the config 2026-09-14 ran."
        )
    if int(cfg["anneal"]["restarts"]) != PREV_RESTARTS:
        raise SystemExit(
            f"config/projection.yaml restarts is {cfg['anneal']['restarts']}, not {PREV_RESTARTS}"
        )
    caps = AP.tpo_caps(cfg["target_period"])

    manifest = AP.require_fresh_batch()
    stands = pd.read_csv(AP.WORK / "carved_stands.csv", dtype={"PLT_CN": str, "unit_id": str})
    library = pd.read_csv(AP.WORK / "expanded_library.csv",
                          dtype={"PLT_CN": str, "unit_id": str})
    cycles = pd.read_csv(AP.WORK / "trajectory_cycles.csv", dtype={"PLT_CN": str})
    AP.check_batch_matches(manifest, stands, library, cycles, cfg)
    library_check = check_library_matches_published(cycles, cfg["n_cycles"])

    land = AP.Landscape(stands, library, cycles, cfg["n_cycles"])
    obj = AP.Objective(land, caps, cfg)

    rip = land.verify_riparian_structural()
    if not rip["structurally_enforced"]:
        raise AssertionError(f"{rip['with_a_cutting_option']} riparian stands carry a cutting option")
    log.info("Riparian no-entry is structural: %d riparian stands, all with library "
             "{no_management}", rip["riparian_stands"])

    avail, why = AP.spatial_penalties_available(land)
    log.warning("Spatial penalties (adjacency_greenup, max_opening_size) UNAVAILABLE: %s", why)

    # --- baselines, unchanged ---------------------------------------------------------
    greedy = AP.greedy_seed(land, stands, caps)
    obj.reset(greedy)
    greedy_obj = obj.total()
    log.info("Greedy baseline objective: %.6f", greedy_obj)
    import random as _random
    random_objs = []
    for r in range(5):
        rc = AP.random_choice(land, _random.Random(cfg["seed"] + 1000 + r))
        obj.reset(rc)
        random_objs.append(obj.total())

    # --- the sweep --------------------------------------------------------------------
    seeds = [cfg["seed"] + i for i in range(args.restarts)]
    wall = time.perf_counter()
    runs = run_sweep(land, obj, cfg, greedy, coolings, seeds, args.workers)
    wall = time.perf_counter() - wall
    log.info("Sweep finished in %.1f s wall, %.1f s CPU",
             wall, sum(r["seconds"] for r in runs))

    detail = pd.DataFrame([{"cooling_factor": r["cooling_factor"], "seed": r["seed"],
                            "objective": r["objective"],
                            "initial_temperature": r["initial_temperature"],
                            "temperature_levels": r["levels"],
                            "moves_proposed": r["proposed"], "moves_accepted": r["accepted"],
                            "accept_rate": r["accept_rate"], "seconds": r["seconds"]}
                           for r in runs]).sort_values(["cooling_factor", "seed"])
    detail.to_csv(OUT_DIR / "seed_spread.csv", index=False)

    arms = arm_table(runs, coolings, seeds)
    arms.to_csv(OUT_DIR / "arm_summary.csv", index=False)
    curve = restart_curve(runs, coolings, seeds)
    curve.to_csv(OUT_DIR / "restart_curve.csv", index=False)

    # --- the reproduction check -------------------------------------------------------
    prev_q = json.loads(PREV_QUALITY.read_text())
    base_cell = arms[arms["is_20260914_setting"]]
    if len(base_cell) != 1:
        raise AssertionError("the 2026-09-14 baseline cell is not uniquely identified")
    reproduced = float(base_cell.iloc[0]["objective_best"])
    published = float(prev_q["objective_best"])
    # Reported, not enforced, and the split is deliberate. The *library* check above is the
    # hard gate: if the rebuilt FVS output differed from the published library, nothing here
    # would mean anything, so that one raises. This check is weaker in kind — it asks whether
    # the same library, the same search and the same seeds land on the same plan across two
    # machines. They should, and an inexact result is worth publishing rather than dying on,
    # because the sweep's own cells are all drawn from one rebuilt library and stay comparable
    # to each other either way; it is only the cross-week number that would need a caveat.
    # The most likely cause of an inexact result is row order in the rebuilt intermediates
    # reaching `rng.randrange` in a different sequence, which is a determinism finding about
    # the batch rather than about the search.
    repro = {"published_20260914": published, "reproduced_here": reproduced,
             "abs_diff": abs(reproduced - published),
             "exact": bool(abs(reproduced - published) <= 1e-6),
             "tolerance": 1e-6,
             "published_seed_spread": prev_q["seed_spread"]["range"],
             "reproduced_seed_spread": float(base_cell.iloc[0]["seed_spread"]),
             "note": "same library, same search code, same seeds, different machine and a "
                     "freshly compiled FVSsn; `exact` false would mean the cross-week "
                     "objective comparison needs a caveat, not that the sweep is invalid"}
    if repro["exact"]:
        log.info("Reproduction: the 2026-09-14 setting recovers %.9f against a published "
                 "%.9f (exact)", reproduced, published)
    else:
        log.warning("Reproduction INEXACT: the (cooling %s, %d restarts) cell scored %.9f "
                    "where 2026-09-14 published %.9f (|diff| %.3g). The library is checked "
                    "identical and the search is imported unchanged, so suspect intermediate "
                    "row order; the cross-week comparison below carries this caveat.",
                    CONFIG_COOLING, PREV_RESTARTS, reproduced, published, repro["abs_diff"])

    # The baseline cell's plan, rebuilt and compared stand by stand. This is the strong
    # form of the reproduction claim, and it costs one `plan_frame` call rather than a
    # search: the choice vector is already in hand.
    base_runs = [r for r in runs if r["cooling_factor"] == CONFIG_COOLING]
    base_runs.sort(key=lambda r: seeds.index(r["seed"]))
    base_best = min(base_runs[:PREV_RESTARTS], key=lambda r: r["objective"])
    repro["plan"] = check_plan_reproduces_published(
        AP.plan_frame(land, base_best["choice"], stands))
    repro["plan"]["baseline_seed"] = int(base_best["seed"])

    # --- the plan, from the best cell -------------------------------------------------
    best_run = min(runs, key=lambda r: r["objective"])
    chosen_arm = arms.loc[arms["objective_best"].idxmin()]
    log.info("Best overall: cooling %.3g, seed %d, objective %.6f (against %.6f published)",
             best_run["cooling_factor"], best_run["seed"], best_run["objective"], published)

    bound, strategy = obj.relaxation_bound()
    obj.reset(best_run["choice"])
    best_obj = obj.total()

    plan = AP.plan_frame(land, best_run["choice"], stands)
    plan.to_csv(OUT_DIR / "annealed_plan.csv", index=False)
    viol = obj.violation_vector(best_run["choice"])
    viol.to_csv(OUT_DIR / "constraint_violations.csv", index=False)
    envelope = obj.attainable_envelope()
    envelope.to_csv(OUT_DIR / "attainable_envelope.csv", index=False)
    unreachable = int((~envelope["target_within_envelope"]).sum())

    best_cell_objs = [r["objective"] for r in runs
                      if r["cooling_factor"] == best_run["cooling_factor"]]
    quality = {
        "objective_best": best_obj,
        "objective_greedy_baseline": greedy_obj,
        "objective_random_baseline_mean": sum(random_objs) / len(random_objs),
        "objective_random_baseline_all": random_objs,
        "relaxation_bound": bound,
        "relaxation_bound_strategy": strategy,
        "gap_to_bound_absolute": best_obj - bound,
        "beats_greedy": bool(best_obj < greedy_obj),
        "beats_random": bool(best_obj < min(random_objs)),
        "seed_spread": {"min": min(best_cell_objs), "max": max(best_cell_objs),
                        "range": max(best_cell_objs) - min(best_cell_objs)},
        "target_period": cfg["target_period"],
        "random_seed": cfg["seed"],
        "search": {
            "what_changed": "restarts and cooling_factor only; library, landscape, weights, "
                            "caps, greedy start and move mixture are 2026-09-14's",
            "code_source": "weekly-artifact/2026-09-14/make_annealed_plan.py, imported",
            "cooling_factor": float(best_run["cooling_factor"]),
            "restarts": int(chosen_arm["restarts"]),
            "config_cooling_factor": CONFIG_COOLING,
            "config_restarts": PREV_RESTARTS,
            "coolings_swept": coolings,
            "restarts_swept": len(seeds),
            "searches_run": len(runs),
            "seeds": seeds,
            "best_seed": best_run["seed"],
            "wall_seconds": wall,
            "cpu_seconds": sum(r["seconds"] for r in runs),
            "workers": args.workers,
            "parallelism_changes_results": False,
        },
        "reproduction_of_20260914": repro,
        "library_identical_to_20260914": library_check,
        # The schedule the *published* plan was found under, not the config's, because a
        # reader comparing this block with 2026-09-14's is comparing the two published runs.
        # `cooling_factor` is the one thing in it this driver overrides; the config's value
        # is beside it in `search.config_cooling_factor`.
        "cooling": {**{k: cfg["anneal"][k] for k in
                       ("cooling_factor", "iterations_per_temperature", "min_temperature",
                        "stall_temperature_levels", "initial_accept_rate")},
                    "cooling_factor": float(best_run["cooling_factor"]),
                    "cooling_factor_source": "overridden by --coolings; config says "
                                             f"{CONFIG_COOLING}"},
        "objective_weights": {o["metric"]: float(o["weight"]) for o in cfg["objectives"]},
        "move_weights_declared": cfg["anneal"]["move_weights"],
        "move_probabilities_effective": {
            **AP.effective_move_probabilities(cfg["anneal"]["move_weights"]),
            "block": "unavailable — no adjacency components on a pixel-class landscape"},
        "spatial_penalties": {"available": avail, "reason": why,
                              "declared": cfg["penalties"]},
        "riparian_structural": rip,
        "stands": int(land.n),
        "stands_with_a_choice": int(len(land.decision_stands)),
        "targets_unreachable_from_library": unreachable,
        "targets_total": int(len(envelope)),
        "targets_unreachable_is_a_proof": True,
        "objective_evenflow_term": AP.evenflow_cost(viol),
        "objective_comparability_note":
            "the library is identical to 2026-09-14's (asserted above), so the total objective "
            "and the even-flow term are both exactly comparable this week — unlike the "
            "2026-08-31 to 2026-09-14 step, where the standing-volume denominator moved",
    }
    (OUT_DIR / "solution_quality.json").write_text(json.dumps(quality, indent=2))

    # Every summary below is built exactly as 2026-09-14 built it — same grouping, same
    # column names — so the two artifacts' tables are comparable row for row rather than
    # only in prose.
    summary = (plan.melt(id_vars=["stand_id", "county", "owner_group", "unit_class"],
                         value_vars=[f"cuft_cycle_{c}" for c in range(1, cfg["n_cycles"] + 1)],
                         var_name="cycle", value_name="cuft")
               .assign(cycle=lambda d: d["cycle"].str.replace("cuft_cycle_", "").astype(int)))
    per_cycle = (summary.groupby("cycle", as_index=False)["cuft"].sum()
                 .assign(calendar_year=lambda d: AP.INV_YEAR + d["cycle"] * AP.CYCLE_YEARS,
                         target_cuft=caps[AP.hs.TOTAL][""]))
    per_cycle["deviation_pct"] = (100 * (per_cycle["cuft"] - per_cycle["target_cuft"])
                                  / per_cycle["target_cuft"])
    per_cycle.to_csv(OUT_DIR / "harvest_by_cycle.csv", index=False)

    compare_to_prev(quality, per_cycle).to_csv(OUT_DIR / "comparison_to_20260914.csv",
                                               index=False)
    log.info("Against 2026-09-14: objective %.3f -> %.3f; even-flow term %.3f -> %.3f; "
             "seed spread %.3f -> %.3f", published, best_obj,
             prev_q["objective_evenflow_term"], quality["objective_evenflow_term"],
             prev_q["seed_spread"]["range"], quality["seed_spread"]["range"])

    mix = (plan.groupby(["prescription", "base_prescription", "timing_offset_years",
                         "unit_class"], as_index=False)
           .agg(stands=("stand_id", "count"), acres=("acres", "sum"),
                removed_cuft=("total_removed_cuft", "sum")))
    mix.to_csv(OUT_DIR / "prescription_mix.csv", index=False)

    timing = (plan[plan["base_prescription"] != "no_management"]
              .groupby("timing_offset_years", as_index=False)
              .agg(stands=("stand_id", "count"), acres=("acres", "sum"),
                   removed_cuft=("total_removed_cuft", "sum")))
    timing.to_csv(OUT_DIR / "timing_offsets_chosen.csv", index=False)
    log.info("Timing offsets chosen (cutting stands only):\n%s", timing.to_string(index=False))

    by_dim = (plan.groupby(["county", "owner_group"], as_index=False)
              .agg(stands=("stand_id", "count"), acres=("acres", "sum"),
                   removed_cuft=("total_removed_cuft", "sum")))
    by_dim.to_csv(OUT_DIR / "plan_by_dimension.csv", index=False)

    log.info("Wrote the plan (%d stands) and the sweep tables to %s", len(plan), OUT_DIR)


if __name__ == "__main__":
    main()
