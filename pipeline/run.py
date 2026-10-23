"""
The ARTEMIS runner: a run directory's stands, through the trajectory library, to a plan.

    uv run python -m pipeline.run library  <run_dir> [--workers N] [--inventory plot|leto]
    uv run python -m pipeline.run schedule <run_dir> [--restarts N] [--workers N]

A run directory holds one landscape. Upstream stages write its inputs:

* ``stands.csv`` — one row per management unit, with the columns in :data:`STAND_COLUMNS`.
  ``PLT_CN`` names the FVS stand the unit's keyfiles read in ``FVS_Data.db``: a donor plot
  shared by many units (``--inventory plot``), or the unit's own LETO inventory stand
  (``--inventory leto``, `pipeline.leto_database`). ``unit_class`` is ``riparian`` for a
  unit that may only grow.
* ``FVS_Data.db`` — the FVS input database those stands live in.

The raster repair (`pipeline.s1_initial_state.county_improvement`) and the LETO stages
(`pipeline.leto_ca`, `leto_riparian`, `leto_donors`, `leto_inventory`, `leto_database`) produce
them; their FL5 driver is still `experiments/2026-10-19_fl5-leto-stands` steps 1-2 and 8.

``library`` enumerates every stand's open-menu options (`trajectory_library.OPEN_MENU`) and runs
the FVS keyfiles not yet cached. ``schedule`` selects one trajectory per stand against the TPO
even-flow targets by simulated annealing (`pipeline.s3_management.annealer`), best of
``--restarts``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from enum import Enum
from pathlib import Path

import pandas as pd

from pipeline.ids import as_id_series
from pipeline.s3_management import annealer
from pipeline.s4_fvs import trajectory_library as tl

STANDS = "stands.csv"
FVS_DATA = "FVS_Data.db"
LIBRARY = "library.csv"
CYCLES = "fvs_cycles.csv.gz"
FAILURES = "fvs_failures.csv"
PLAN = "plan.csv.gz"
DEVIATION = "deviation_by_cycle.csv"
SEEDS = "seeds.csv"
SUMMARY = "summary.json"

STAND_COLUMNS = ("unit_id", "PLT_CN", "county", "owner_class", "OWN_CODE", "FORTYPCD",
                 "forest_branch", "stand_age", "acres", "unit_class")
ID_DTYPES = {"unit_id": str, "PLT_CN": str}
COOLING = 0.98                 # 2026-09-28's converged setting, which every schedule since ran

log = logging.getLogger("artemis")


class InventoryLayout(Enum):
    """Which tables a run's ``FVS_Data.db`` holds its stands in."""
    PLOT = "plot"              # FIA per-plot StandInit/TreeInit, shared by every unit on a plot
    LETO = "leto"              # LETO v1 stage 5: one stand per management unit

    @property
    def tables(self) -> tl.FvsTables:
        return tl.PLOT_TABLES if self is InventoryLayout.PLOT else tl.LETO_TABLES


def read_stands(run_dir: Path) -> pd.DataFrame:
    stands = pd.read_csv(run_dir / STANDS, dtype=ID_DTYPES)
    missing = [c for c in STAND_COLUMNS if c not in stands.columns]
    if missing:
        raise ValueError(f"{run_dir / STANDS} lacks {missing}")
    stands["PLT_CN"] = as_id_series(stands["PLT_CN"], column="PLT_CN")
    if not stands["unit_id"].is_unique:
        raise ValueError(f"{run_dir / STANDS} repeats a unit_id")
    return stands


def build_library(run_dir: Path, workers: int, layout: InventoryLayout, fvs_bin: Path) -> None:
    stands = read_stands(run_dir)
    with sqlite3.connect(run_dir / FVS_DATA) as con:
        trees = pd.read_sql(f"SELECT * FROM {layout.tables.tree}", con)
    sdi = tl.stand_sdi_tables(trees)
    keyfiles: dict[str, str] = {}
    lib = tl.enumerate_library(tl.OPEN_MENU, stands, sdi, keyfiles, tables=layout.tables)
    lib.to_csv(run_dir / LIBRARY, index=False)
    plt_of = dict(zip(lib["run_hash"], lib["PLT_CN"]))
    paths = tl.RunPaths(work=run_dir, data_db=run_dir / FVS_DATA, fvs_bin=fvs_bin)
    tl.run_fvs(keyfiles, plt_of, paths, workers)


def schedule(run_dir: Path, restarts: int, workers: int, cooling: float) -> dict:
    stands = read_stands(run_dir)
    library = pd.read_csv(run_dir / LIBRARY, dtype=ID_DTYPES)
    cycles = pd.read_csv(run_dir / CYCLES, dtype={"PLT_CN": str})
    cfg = annealer.load_config()
    cfg["anneal"] = {**cfg["anneal"], "cooling_factor": cooling}
    caps = annealer.tpo_caps(cfg["target_period"])

    land, obj, greedy = annealer.build_landscape(stands, library, cycles, cfg, caps)
    obj.reset(greedy)
    greedy_objective = obj.total()
    seeds = [cfg["seed"] + i for i in range(restarts)]
    t = time.perf_counter()
    best, results = annealer.search(land, obj, cfg, greedy, seeds, workers)
    viol = obj.violation_vector(best["choice"])

    annealer.plan_frame(land, best["choice"], stands).to_csv(run_dir / PLAN, index=False)
    viol.to_csv(run_dir / DEVIATION, index=False)
    pd.DataFrame([{k: r[k] for k in ("seed", "objective", "levels", "seconds")}
                  for r in results]).to_csv(run_dir / SEEDS, index=False)
    summary = {
        "stands": land.n, "acres": sum(land.acres), "restarts": restarts,
        "cooling_factor": cooling, "wall_seconds": time.perf_counter() - t,
        "options_per_stand": sum(map(len, land.options)) / land.n,
        "best_seed": best["seed"], "objective_best": best["objective"],
        "objective_greedy": greedy_objective, "evenflow_cost": annealer.evenflow_cost(viol),
        "target_period": cfg["target_period"],
    }
    (run_dir / SUMMARY).write_text(json.dumps(summary, indent=2))
    log.info("plan: objective %.4f (greedy %.4f), even-flow cost %.4f over %d stands",
             summary["objective_best"], greedy_objective, summary["evenflow_cost"], land.n)
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="artemis", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    stages = parser.add_subparsers(dest="stage", required=True)

    lib = stages.add_parser("library", help="enumerate options and run FVS")
    lib.add_argument("run_dir", type=Path)
    lib.add_argument("--workers", type=int, default=4)
    lib.add_argument("--inventory", type=InventoryLayout, default=InventoryLayout.PLOT,
                     choices=list(InventoryLayout))
    lib.add_argument("--fvs-bin", type=Path, default=tl.RunPaths.fvs_bin)

    sch = stages.add_parser("schedule", help="anneal one trajectory per stand")
    sch.add_argument("run_dir", type=Path)
    sch.add_argument("--restarts", type=int, default=11)
    sch.add_argument("--workers", type=int, default=11)
    sch.add_argument("--cooling", type=float, default=COOLING)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.stage == "library":
        build_library(args.run_dir, args.workers, args.inventory, args.fvs_bin)
    else:
        schedule(args.run_dir, args.restarts, args.workers, args.cooling)


if __name__ == "__main__":
    main()
