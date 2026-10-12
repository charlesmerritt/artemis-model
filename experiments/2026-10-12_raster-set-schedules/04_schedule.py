"""Step 4 — anneal each raster set's landscape exactly as the 2026-10-08 deck annealed its own.

Search, objective, TPO targets (2013-2024), cooling (0.98), restarts (22) and seeds are the
deck's (`experiments/2026-10-08_permissive-menus/02_schedule.py`'s `build`, `_search` and
`metrics`, and 2026-09-14's annealer); only the landscape and its library differ. On
`published` this is the deck's own run, so its numbers must come back.

Per set, writes to `outputs/<set>/`:

* `summary.json` — the deck's metrics for the best plan and for the greedy start;
* `deviation_by_cycle.csv` — planned vs TPO target per (dimension, key, cycle);
* `capacity.csv` — per county and owner group: the horizon maximum (every stand on its
  highest-volume option) and the plan, as % of ten cycles of target, and the standing
  merchantable volume and acres the landscape starts with;
* `plan.csv.gz`, `seeds.csv`.

    uv run python experiments/2026-10-12_raster-set-schedules/04_schedule.py --raster-sets gate-v4
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
PERMISSIVE = REPO / "experiments/2026-10-08_permissive-menus"
sys.path[:0] = [str(EXP), str(PERMISSIVE), str(REPO)]

import raster_sets as RS  # noqa: E402
from pipeline.ids import as_id_series  # noqa: E402

spec = importlib.util.spec_from_file_location("schedule_step", PERMISSIVE / "02_schedule.py")
S = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = S
spec.loader.exec_module(S)
AP = S.AP

VARIANT = "repeat_offsets_25"
RESTARTS = 22                      # the deck's plan: best of 22
log = logging.getLogger("raster_set_schedule")


def capacity(land, plan_viol: pd.DataFrame, caps: dict, n_cycles: int, stands: pd.DataFrame,
             cycles: pd.DataFrame) -> pd.DataFrame:
    """Horizon maximum and plan per key, as `horizon_capacity.py`, plus the starting inventory."""
    best = [max(sum(o) for o in opts) for opts in land.volumes]
    start = (cycles[cycles["cycle"] == 0].drop_duplicates("PLT_CN")
             .set_index("PLT_CN")["MCuFt"])
    s = stands.assign(start_cuft=stands["PLT_CN"].map(start) * stands["acres"],
                      tpo_county=stands["county"].map(AP.COUNTY_TO_TPO),
                      group=stands["owner_class"].map(AP.owner_group))
    rows = []
    for dim, keys, ix, targets, col in (
            ("county", land.counties, land.county_ix, caps[AP.hs.COUNTY], "tpo_county"),
            ("owner_group", land.owners, land.owner_ix, caps[AP.hs.OWNER], "group")):
        for k, key in enumerate(keys):
            need = targets[key] * n_cycles
            cap = sum(b for b, i in zip(best, ix) if i == k)
            got = plan_viol[plan_viol["key"] == key]["volume_cuft"].sum()
            mine = s[s[col] == key]
            rows.append({"dimension": dim, "key": key, "horizon_target_cuft": need,
                         "horizon_max_cuft": cap, "max_pct_of_target": 100 * cap / need,
                         "plan_cuft": got, "plan_pct_of_target": 100 * got / need,
                         "acres": mine["acres"].sum(),
                         "harvestable_acres": mine.loc[mine["unit_class"] == "managed", "acres"].sum(),
                         "start_merch_cuft": mine["start_cuft"].sum()})
    return pd.DataFrame(rows)


def schedule(rs: RS.RasterSet, cycles: pd.DataFrame, workers: int) -> dict:
    out = RS.output_dir(rs)
    out.mkdir(parents=True, exist_ok=True)
    cfg = AP.load_config()
    cfg["anneal"] = {**cfg["anneal"], "cooling_factor": S.COOLING}
    caps = AP.tpo_caps(cfg["target_period"])
    stands = pd.read_csv(RS.work_dir(rs) / "carved_stands.csv", dtype=S.ID_COLS)
    stands["PLT_CN"] = as_id_series(stands["PLT_CN"], column="PLT_CN")
    seeds = [cfg["seed"] + i for i in range(RESTARTS)]

    S.WORK = RS.work_dir(rs)              # `build` reads `library_<variant>.csv` from here
    land, obj, greedy = S.build(VARIANT, stands, cycles, cfg, caps)
    obj.reset(greedy)
    greedy_obj = obj.total()
    greedy_viol = obj.violation_vector(greedy)
    S._STATE.update(land=land, obj=obj, cfg=cfg, greedy=greedy)
    t = time.perf_counter()
    with mp.get_context("fork").Pool(processes=min(workers, len(seeds))) as pool:
        results = pool.map(S._search, seeds, chunksize=1)
    wall = time.perf_counter() - t
    best = min(results, key=lambda r: r["objective"])
    viol = obj.violation_vector(best["choice"])
    viol.to_csv(out / "deviation_by_cycle.csv", index=False)

    plan = AP.plan_frame(land, best["choice"], stands)
    plan.to_csv(out / "plan.csv.gz", index=False)
    cap = capacity(land, viol, caps, cfg["n_cycles"], stands, cycles)
    cap.to_csv(out / "capacity.csv", index=False)
    pd.DataFrame([{"seed": r["seed"], "objective": r["objective"], "levels": r["levels"],
                   "seconds": r["seconds"]} for r in results]).to_csv(out / "seeds.csv", index=False)
    summary = {
        "raster_set": rs.name, "label": rs.label, "variant": VARIANT, "restarts": len(seeds),
        "cooling_factor": S.COOLING, "wall_seconds": wall, "best_seed": best["seed"],
        "objective_best": best["objective"], "objective_greedy": greedy_obj,
        "objective_worst_seed": max(r["objective"] for r in results),
        "options_per_stand": sum(len(o) for o in land.options) / land.n, "stands": land.n,
        "acres": float(stands["acres"].sum()),
        "target_cuft_per_cycle": caps[AP.hs.TOTAL][""],
        "greedy": S.metrics(greedy_viol, caps), **S.metrics(viol, caps),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    log.info("%s: objective %.3f, even-flow cost %.3f, landscape %.1f%%, %.0fs", rs.name,
             best["objective"], summary["evenflow_cost"], summary["landscape_mean_dev_pct"], wall)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raster-sets", default=",".join(rs.name for rs in RS.ALL))
    ap.add_argument("--workers", type=int, default=RESTARTS)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cycles = pd.read_csv(RS.WORK / "fvs_cycles.csv.gz", dtype={"PLT_CN": str})
    for name in args.raster_sets.split(","):
        schedule(RS.BY_NAME[name], cycles, args.workers)


if __name__ == "__main__":
    main()
