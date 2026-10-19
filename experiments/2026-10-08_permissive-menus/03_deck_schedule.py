"""Step 4 — the deck's plan: the open menu, annealed harder, with every cut it schedules.

The menu is `repeat_offsets_25`: every owner class may take every cutting prescription in
`config/management_regimes.yaml`, forest type still filters (no pine plantation on a
hardwood plot), rotations repeat inside the 50-year horizon and offsets run 0-25 years.
Library, landscape, objective and cooling are step 2's; only the restart count is larger,
because step 2's 11 seeds for this variant spread from 7.36 to 8.29.

Writes `outputs/deck/`:

* `plan.csv.gz` — one row per stand: the chosen option and its per-cycle removals;
* `deviation_by_cycle.csv` — planned vs TPO target per (dimension, key, cycle);
* `landscape_by_cycle.csv` — the five-county total per cycle, with its error;
* `events.csv.gz` — one row per scheduled cut: stand, cycle, year, `clearcut` or `partial`,
  acres and ft³ removed. The cut type is read from the operations `menus.build_entries`
  rendered into that stand's keyfile, not inferred from FVS output;
* `seeds.csv`, `summary.json`.

    uv run python experiments/2026-10-08_permissive-menus/03_deck_schedule.py [--restarts 22]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import multiprocessing as mp
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

EXP = Path(__file__).resolve().parent
sys.path[:0] = [str(EXP), str(EXP.parents[1])]

import menus  # noqa: E402
from pipeline.ids import as_id_series  # noqa: E402

spec = importlib.util.spec_from_file_location("schedule_step", EXP / "02_schedule.py")
S = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = S
spec.loader.exec_module(S)
AP = S.AP

log = logging.getLogger("deck_schedule")

VARIANT = "repeat_offsets_25"
OUT = S.OUTPUTS / "deck"
N_CYCLES = 10


def plot_sdi() -> dict:
    """Step 1's per-plot SDI tables, which size the replanting after a clearcut."""
    spec = importlib.util.spec_from_file_location(
        "timing_library_20260914", menus.REPO / "weekly-artifact/2026-09-14/make_timing_library.py")
    tl = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = tl
    spec.loader.exec_module(tl)
    con = sqlite3.connect(menus.REPO / "data/interim/timing_library/FVS_Data.db")
    trees = pd.read_sql("SELECT * FROM FVS_TreeInit_Plot", con)
    con.close()
    return tl.stand_sdi_tables(trees)


def cut_events(plan: pd.DataFrame, config: dict, sdi: dict, plot_age: pd.Series) -> pd.DataFrame:
    """Every harvest entry of every stand's chosen option, typed by its rendered operation."""
    variant = next(v for v in menus.VARIANTS if v.name == VARIANT)
    cache: dict = {}
    rows = []
    for p in plan.itertuples(index=False):
        if p.base_prescription == "no_management":
            continue
        key = (p.PLT_CN, p.base_prescription)
        if key not in cache:
            cache[key] = menus.build_entries(config, p.base_prescription,
                                             float(plot_age[p.PLT_CN]), variant.rotations, sdi.get(p.PLT_CN))
        for e in menus.shift_entries(cache[key], int(p.timing_offset_years)):
            cycle = -(-(e.thin.year - menus.INV_YEAR) // menus.CYCLE_YEARS)
            if not 1 <= cycle <= N_CYCLES:
                continue
            rows.append({"stand_id": p.stand_id, "cycle": int(cycle), "entry_year": e.thin.year,
                         "cut": "clearcut" if e.thin.proportion >= 1.0 else "partial",
                         "base_prescription": p.base_prescription, "county": p.county,
                         "owner_group": p.owner_group, "owner_class": p.owner_class,
                         "acres": p.acres})
    ev = pd.DataFrame(rows)
    # Volume per cut comes from FVS: the stand's removals in that cycle.
    vol = plan.melt(id_vars="stand_id", value_vars=[f"cuft_cycle_{k}" for k in range(1, N_CYCLES + 1)],
                    var_name="c", value_name="removed_cuft")
    vol["cycle"] = vol["c"].str.removeprefix("cuft_cycle_").astype(int)
    ev = ev.merge(vol.drop(columns="c"), on=["stand_id", "cycle"], how="left")
    # Two entries in one cycle share that cycle's FVS removal; count it once.
    ev["removed_cuft"] = ev["removed_cuft"].where(~ev.duplicated(["stand_id", "cycle"]), 0.0)
    return ev


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restarts", type=int, default=22)
    ap.add_argument("--workers", type=int, default=11)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("pipeline.s4_fvs.regime_templates").setLevel(logging.ERROR)
    OUT.mkdir(parents=True, exist_ok=True)

    cfg = AP.load_config()
    cfg["anneal"] = {**cfg["anneal"], "cooling_factor": S.COOLING}
    caps = AP.tpo_caps(cfg["target_period"])
    stands = pd.read_csv(S.STANDS, dtype=S.ID_COLS)
    stands["PLT_CN"] = as_id_series(stands["PLT_CN"], column="PLT_CN")
    cycles = pd.read_csv(S.CYCLES, dtype={"PLT_CN": str})
    seeds = [cfg["seed"] + i for i in range(args.restarts)]

    land, obj, greedy = S.build(VARIANT, stands, cycles, cfg, caps)
    obj.reset(greedy)
    greedy_obj = obj.total()
    greedy_viol = obj.violation_vector(greedy)
    S._STATE.update(land=land, obj=obj, cfg=cfg, greedy=greedy)
    t = time.perf_counter()
    with mp.get_context("fork").Pool(processes=min(args.workers, len(seeds))) as pool:
        results = pool.map(S._search, seeds, chunksize=1)
    wall = time.perf_counter() - t
    best = min(results, key=lambda r: r["objective"])
    viol = obj.violation_vector(best["choice"])
    viol.to_csv(OUT / "deviation_by_cycle.csv", index=False)

    target = caps[AP.hs.TOTAL][""]
    land_cyc = (viol[viol["dimension"] == "county"].groupby("cycle", as_index=False)["volume_cuft"]
                .sum().assign(target_cuft=target))
    land_cyc["deviation_pct"] = 100 * (land_cyc["volume_cuft"] - target) / target
    land_cyc.to_csv(OUT / "landscape_by_cycle.csv", index=False)

    plan = AP.plan_frame(land, best["choice"], stands)
    plan["PLT_CN"] = as_id_series(plan["PLT_CN"], column="PLT_CN")
    plan = plan.merge(stands[["unit_id", "stand_age", "forest_branch"]],
                      left_on="stand_id", right_on="unit_id").drop(columns="unit_id")
    plan.to_csv(OUT / "plan.csv.gz", index=False)

    # Step 1 resolved each plot's operations at its first stand's age; so must this.
    plot_age = stands.drop_duplicates("PLT_CN").set_index("PLT_CN")["stand_age"]
    config = menus.load_variant_config(next(v for v in menus.VARIANTS if v.name == VARIANT))
    events = cut_events(plan, config, plot_sdi(), plot_age)
    events.to_csv(OUT / "events.csv.gz", index=False)

    pd.DataFrame([{"seed": r["seed"], "objective": r["objective"], "levels": r["levels"],
                   "seconds": r["seconds"]} for r in results]).to_csv(OUT / "seeds.csv", index=False)
    summary = {
        "variant": VARIANT, "restarts": len(seeds), "cooling_factor": S.COOLING,
        "wall_seconds": wall, "best_seed": best["seed"], "objective_best": best["objective"],
        "objective_greedy": greedy_obj,
        "objective_worst_seed": max(r["objective"] for r in results),
        "options_per_stand": sum(len(o) for o in land.options) / land.n,
        "stands": land.n, "target_cuft_per_cycle": target,
        "greedy": S.metrics(greedy_viol, caps), **S.metrics(viol, caps),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    log.info("best objective %.3f (greedy %.3f), even-flow cost %.3f, landscape %.1f%%",
             best["objective"], greedy_obj, summary["evenflow_cost"],
             summary["landscape_mean_dev_pct"])


if __name__ == "__main__":
    main()
