"""Step 2 — anneal every menu variant and rank them by distance from the TPO targets.

The search is 2026-09-14's, imported unchanged (`Landscape`, `Objective`, `greedy_seed`,
`anneal` out of `weekly-artifact/2026-09-14/make_annealed_plan.py`), at the cooling factor
2026-09-28 found converges best (0.98). Landscape, objective weights, TPO period
(`config/projection.yaml`, `config/tpo_targets.yaml`) and seeds are the same for every
variant; only the menu differs. So every gap between two rows of `menu_comparison.csv` is
attributable to the menu.

"Error" is reported three ways, all from the best plan's violation vector:

* `evenflow_cost` — the objective's weight-6 term, sum over (dimension, key, cycle) of the
  squared relative deviation from target. 0 means every county and owner group hits its
  TPO removals in every cycle. Comparable across variants, unlike the total objective,
  whose standing-volume term is normalised by each library's own maximum.
* `owner_mean_abs_dev_pct` / `county_mean_abs_dev_pct` — the same deviations as a plain
  mean of |%|, per dimension.
* `landscape_mean_dev_pct` — the five-county total against its target, signed, averaged
  over cycles: negative is under-harvest.

    uv run python experiments/2026-10-08_permissive-menus/02_schedule.py [--restarts 8]

Results, 2026-10-08 (11 restarts per variant, cooling 0.98, `outputs/menu_comparison.csv`):

    variant                     options/stand  even-flow cost  owner |dev|  landscape
    baseline                         4.5          10.51          29.5%       -33.3%
    all_owners                      10.5           5.64          21.6%       -23.9%
    all_owners_short_natural        12.3           5.63          21.6%       -23.8%
    repeat_rotations                12.3           1.37           8.1%        -8.4%
    repeat_offsets_25               17.8           1.28           7.4%        -7.9%
    repeat_offsets_25_any_type      21.9           1.23           7.1%        -7.7%

The baseline reproduces 2026-09-28 (objective 62.46 vs 62.57 published). Opening every
menu to every owner halves the error; repeating rotations inside the horizon is the other
large step, because a single-rotation keyfile cuts each stand at most once in 50 years.
Longer offsets and ignoring forest type add little. In the best plans Baker, Columbia,
Hamilton, Union and the federal group are within 2% of target in every cycle. The residual
is Suwannee (-36% early cycles) and Other public (-18%). `horizon_capacity.py` shows that
is timber, not menu: with every stand on its highest-volume option, Suwannee can supply
only 77% and Other public 97% of ten cycles of TPO removals.
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

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
sys.path[:0] = [str(EXP), str(REPO)]

import menus  # noqa: E402

log = logging.getLogger("permissive_schedule")

WORK = EXP / "work"
OUTPUTS = EXP / "outputs"
STANDS = REPO / "data/interim/timing_library/carved_stands.csv"
CYCLES = WORK / "fvs_cycles.csv.gz"
COOLING = 0.98                 # 2026-09-28's converged setting
ID_COLS = {"PLT_CN": str, "unit_id": str, "tm_id": str}


def load_annealer():
    path = REPO / "weekly-artifact/2026-09-14/make_annealed_plan.py"
    spec = importlib.util.spec_from_file_location("annealer_20260914", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


AP = load_annealer()
_STATE: dict = {}


def _search(seed: int) -> dict:
    t = time.perf_counter()
    res = AP.anneal(_STATE["land"], _STATE["obj"], _STATE["cfg"], seed, _STATE["greedy"])
    res["seconds"] = time.perf_counter() - t
    return res


def build(variant: str, stands: pd.DataFrame, cycles: pd.DataFrame, cfg: dict, caps: dict):
    lib = pd.read_csv(WORK / f"library_{variant}.csv", dtype={"PLT_CN": str, "unit_id": str})
    runs = lib.drop_duplicates(["PLT_CN", "prescription"])[["PLT_CN", "prescription", "run_hash"]]
    cyc = runs.merge(cycles, on=["run_hash", "PLT_CN"], how="inner")
    land = AP.Landscape(stands, lib[["unit_id", "prescription"]], cyc, cfg["n_cycles"])
    rip = land.verify_riparian_structural()
    if not rip["structurally_enforced"]:
        raise AssertionError(f"{variant}: riparian stands with a cutting option: {rip}")
    if land.dropped_no_trajectory:
        raise AssertionError(f"{variant}: {land.dropped_no_trajectory} stands lost every option")
    obj = AP.Objective(land, caps, cfg)
    greedy = AP.greedy_seed(land, stands, caps)
    return land, obj, greedy


def metrics(viol: pd.DataFrame, caps: dict) -> dict:
    d = viol["deviation_pct"]
    owner, county = viol["dimension"] == "owner_group", viol["dimension"] == "county"
    landscape = viol[county].groupby("cycle")["volume_cuft"].sum()
    target = caps[AP.hs.TOTAL][""]
    rel = (viol["volume_cuft"] - viol["target_cuft"]) / viol["target_cuft"]
    return {
        "evenflow_cost": float((rel ** 2).sum()),
        "owner_cost": float((rel[owner] ** 2).sum()),
        "county_cost": float((rel[county] ** 2).sum()),
        "owner_mean_abs_dev_pct": float(d[owner].abs().mean()),
        "county_mean_abs_dev_pct": float(d[county].abs().mean()),
        "worst_cell_abs_dev_pct": float(d.abs().max()),
        "landscape_mean_dev_pct": float((100 * (landscape - target) / target).mean()),
        "landscape_min_dev_pct": float((100 * (landscape - target) / target).min()),
        "landscape_max_dev_pct": float((100 * (landscape - target) / target).max()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restarts", type=int, default=8)
    ap.add_argument("--workers", type=int, default=11)
    ap.add_argument("--variants", default=",".join(v.name for v in menus.VARIANTS))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    OUTPUTS.mkdir(exist_ok=True)

    cfg = AP.load_config()
    cfg["anneal"] = {**cfg["anneal"], "cooling_factor": COOLING}
    caps = AP.tpo_caps(cfg["target_period"])
    stands = pd.read_csv(STANDS, dtype=ID_COLS)
    cycles = pd.read_csv(CYCLES, dtype={"PLT_CN": str})
    seeds = [cfg["seed"] + i for i in range(args.restarts)]

    rows, per_cycle, mix, seed_rows = [], [], [], []
    for name in args.variants.split(","):
        variant = next(v for v in menus.VARIANTS if v.name == name)
        land, obj, greedy = build(name, stands, cycles, cfg, caps)
        obj.reset(greedy)
        greedy_obj = obj.total()
        bound, _ = obj.relaxation_bound()
        _STATE.update(land=land, obj=obj, cfg=cfg, greedy=greedy)
        t = time.perf_counter()
        with mp.get_context("fork").Pool(processes=min(args.workers, len(seeds))) as pool:
            results = pool.map(_search, seeds, chunksize=1)
        wall = time.perf_counter() - t
        best = min(results, key=lambda r: r["objective"])
        viol = obj.violation_vector(best["choice"])
        m = metrics(viol, caps)
        rows.append({
            "variant": name, "description": variant.description,
            "owner_menu": variant.owner_menu.value, "rotations": variant.rotations.value,
            "forest_types": variant.forest_types.value,
            "offsets": "/".join(map(str, variant.offsets)),
            "options_per_stand": sum(len(o) for o in land.options) / land.n,
            "objective_best": best["objective"], "objective_greedy": greedy_obj,
            "relaxation_bound": bound, "best_seed": best["seed"],
            "seed_spread": max(r["objective"] for r in results) - best["objective"],
            "restarts": len(seeds), "wall_seconds": wall, **m,
        })
        log.info("%s: objective %.3f (greedy %.3f, bound %.3f), even-flow cost %.3f, owner "
                 "|dev| %.1f%%, landscape %.1f%%", name, best["objective"], greedy_obj, bound,
                 m["evenflow_cost"], m["owner_mean_abs_dev_pct"], m["landscape_mean_dev_pct"])
        per_cycle.append(viol.assign(variant=name))
        plan = AP.plan_frame(land, best["choice"], stands)
        mix.append(plan.groupby(["owner_group", "base_prescription"], as_index=False)
                   .agg(stands=("stand_id", "size"), acres=("acres", "sum"),
                        removed_cuft=("total_removed_cuft", "sum")).assign(variant=name))
        plan.drop(columns=[c for c in plan.columns if c.startswith("cuft_cycle_")]).to_csv(
            OUTPUTS / f"plan_{name}.csv.gz", index=False)
        seed_rows += [{"variant": name, "seed": r["seed"], "objective": r["objective"],
                       "levels": r["levels"], "seconds": r["seconds"]} for r in results]

        # Written after every variant, so a long sweep leaves usable partial results.
        table = pd.DataFrame(rows).sort_values("evenflow_cost")
        table.to_csv(OUTPUTS / "menu_comparison.csv", index=False)
        pd.concat(per_cycle).to_csv(OUTPUTS / "deviation_by_cycle.csv", index=False)
        pd.concat(mix).to_csv(OUTPUTS / "prescription_mix.csv", index=False)
        pd.DataFrame(seed_rows).to_csv(OUTPUTS / "seed_spread.csv", index=False)

    (OUTPUTS / "run_settings.json").write_text(json.dumps({
        "cooling_factor": COOLING, "seeds": seeds, "target_period": cfg["target_period"],
        "weights": {o["metric"]: o["weight"] for o in cfg["objectives"]},
        "dimensions": cfg["dimensions"],
    }, indent=2))
    cols = ["variant", "options_per_stand", "evenflow_cost", "owner_mean_abs_dev_pct",
            "county_mean_abs_dev_pct", "landscape_mean_dev_pct", "objective_best",
            "relaxation_bound"]
    print(pd.DataFrame(rows).sort_values("evenflow_cost")[cols].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
