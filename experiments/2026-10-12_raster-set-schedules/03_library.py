"""Step 3 — the 2026-10-08 deck's menu over every raster set's stands, priced with FVS.

The menu is the deck's `repeat_offsets_25`: every owner may take every cutting prescription,
forest type still filters, rotations repeat inside the horizon, offsets 0-25 years, read from
`config/management_regimes.yaml`. The deck's own open-menu config was never committed; the
repository's is a reconstruction (2026-10-12) that reproduces the deck's library sizes exactly
for all six 2026-10-08 variants on the published landscape. The enumeration and the FVS batch are 2026-10-08's own `enumerate_variant` and `run_missing`
(`experiments/2026-10-08_permissive-menus/01_fvs_library.py`), pointed at this experiment's
stands, input database and run cache. Keyfiles are deduplicated by content hash across sets,
so a run two landscapes share is run once.

Writes `work/<set>/library_repeat_offsets_25.csv`, `work/fvs_cycles.csv.gz` (the run cache) and
`outputs/library_size.csv`.

    uv run python experiments/2026-10-12_raster-set-schedules/03_library.py --workers 30
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
from pathlib import Path

import pandas as pd

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
PERMISSIVE = REPO / "experiments/2026-10-08_permissive-menus"
sys.path[:0] = [str(EXP), str(PERMISSIVE), str(REPO)]

import raster_sets as RS  # noqa: E402

spec = importlib.util.spec_from_file_location("permissive_library", PERMISSIVE / "01_fvs_library.py")
FL = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = FL
spec.loader.exec_module(FL)
menus = FL.menus

VARIANT = "repeat_offsets_25"
log = logging.getLogger("raster_set_library")


def point_at_this_experiment() -> None:
    FL.WORK = RS.WORK
    FL.CYCLES = RS.WORK / "fvs_cycles.csv.gz"
    FL.KEYFILES = RS.WORK / "keyfiles"
    FL.FAILURES = RS.WORK / "fvs_failures.csv"
    FL.FVS_DATA_DB = RS.WORK / "FVS_Data.db"
    FL.FVS_BIN = Path(os.environ.get("FVSSN_BIN", REPO / "fvs/bin/FVSsn"))


def library_path(rs: RS.RasterSet) -> Path:
    return RS.work_dir(rs) / f"library_{VARIANT}.csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--raster-sets", default=",".join(rs.name for rs in RS.ALL))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("pipeline.s4_fvs.regime_templates").setLevel(logging.ERROR)
    point_at_this_experiment()

    variant = next(v for v in menus.VARIANTS if v.name == VARIANT)
    sdi = FL.plot_sdi()
    keyfiles: dict[str, str] = {}
    entry_cache: dict = {}
    libs = {}
    for name in args.raster_sets.split(","):
        rs = RS.BY_NAME[name]
        stands = pd.read_csv(RS.work_dir(rs) / "carved_stands.csv", dtype=FL.ID_COLS)
        libs[rs] = FL.enumerate_variant(variant, stands, sdi, keyfiles, entry_cache)

    plt_of = {}
    for lib in libs.values():
        plt_of.update(zip(lib["run_hash"], lib["PLT_CN"]))
    FL.run_missing(keyfiles, plt_of, args.workers)

    failed = set(pd.read_csv(FL.FAILURES)["run_hash"]) if FL.FAILURES.exists() else set()
    rows = []
    for rs, lib in libs.items():
        lost = lib["run_hash"].isin(failed)
        if lost.any():
            log.warning("%s: %d options have no FVS trajectory", rs.name, int(lost.sum()))
        lib.to_csv(library_path(rs), index=False)
        rows.append({"raster_set": rs.name, "stands": lib["unit_id"].nunique(),
                     "options": len(lib), "options_per_stand": len(lib) / lib["unit_id"].nunique(),
                     "distinct_runs": lib["run_hash"].nunique(), "options_without_run": int(lost.sum())})
    RS.OUTPUTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(RS.OUTPUTS / "library_size.csv", index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
