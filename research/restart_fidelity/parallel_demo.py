"""Spin up multiple FVS instances at once — the parallelism demo.

Official FVS keeps stand state in global common blocks, so two stands cannot
share one process safely. Isolation is therefore per OS PROCESS: this launcher
gives each stand (or bundle of stands) its own keyfile and its own Rscript
worker, spawns them concurrently, and each writes its own SQLite output DB.

This demonstrates the *parallel spin-up* mechanism only. It is NOT the ARTEMIS
orchestrator: there is no work queue, no management policy, no 5-year barrier
coupling here — every worker runs its stand straight to the horizon. Those
pieces are designed (see the spec) but not built.

Run from the worktree root:

    uv run python -m research.restart_fidelity.parallel_demo

Then inspect results with DuckDB (see the printed hint, or README section 5).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from research.restart_fidelity import make_keyfiles as mk
from research.restart_fidelity import paths

WORKER_R = Path(__file__).parent / "parallel_worker.R"


def _worker_keyfile(name: str, stand_id: str, stand_cn: str, out_db: str) -> str:
    """A one-stand keyfile. `name` only labels the run in the keyfile title."""
    header = f"!!title: parallel_demo_{name}\n"
    block = mk._block(name, stand_id, stand_cn, mk.MULTI_INV_YEAR, out_db, num_cycle=4)
    return header + block + "Stop\n"


class RunDirNotStaged(RuntimeError):
    """Raised when the Windows-visible run dir or its input DB is missing."""


def launch(
    stands: tuple[tuple[str, str], ...] = mk.MULTI_STANDS,
    max_workers: int | None = None,
) -> list[dict]:
    """Generate one keyfile per stand and run all workers concurrently.

    Returns one dict per worker: name, output DB path, return code, seconds.

    `max_workers` caps concurrent FVS processes; it defaults to one per CPU.
    Every worker is a real OS process running a Fortran DLL, so an uncapped
    fan-out over a production-sized stand list would thrash the machine.

    Raises RunDirNotStaged rather than exiting: this is an importable library
    function, and a future orchestrator calling it deserves a catchable error.
    """
    if not paths.SPIKE_DIR_WSL.exists():
        raise RunDirNotStaged(
            f"run dir {paths.SPIKE_DIR_WSL} missing — stage it first:\n"
            f"  mkdir -p {paths.SPIKE_DIR_WSL}\n"
            f"  cp {paths.FVS_DATA_DB_SRC} {paths.SPIKE_DIR_WSL}/{paths.FVS_DATA_DB}"
        )
    if not (paths.SPIKE_DIR_WSL / paths.FVS_DATA_DB).exists():
        raise RunDirNotStaged(f"input DB missing: {paths.SPIKE_DIR_WSL / paths.FVS_DATA_DB}")

    jobs = []
    for i, (cn, sid) in enumerate(stands, start=1):
        name = f"pw{i}"
        key = f"{name}.key"
        db = f"{name}.db"
        (paths.SPIKE_DIR_WSL / key).write_text(_worker_keyfile(name, sid, cn, db))
        # Clear stale output so a re-run never appends to an old DB.
        (paths.SPIKE_DIR_WSL / db).unlink(missing_ok=True)
        jobs.append({"name": name, "key": key, "db": str(paths.SPIKE_DIR_WSL / db), "stand": sid})

    # Windows Rscript cannot see WSL paths, so stage the worker into the
    # Windows-visible run dir and reference it there.
    staged_worker = paths.SPIKE_DIR_WSL / WORKER_R.name
    shutil.copyfile(WORKER_R, staged_worker)
    worker_win = paths.to_windows(staged_worker)

    def run_one(job: dict) -> dict:
        t0 = time.monotonic()
        proc = subprocess.run(
            [str(paths.RSCRIPT_EXE), worker_win, job["key"], paths.SPIKE_DIR_WIN],
            capture_output=True,
            text=True,
        )
        job["seconds"] = round(time.monotonic() - t0, 2)
        job["returncode"] = proc.returncode
        # Exit status alone is not proof of a run: check FVS actually produced
        # its output DB. Otherwise a failed run reports ok, and the compare_arms
        # hint below points at a DB that does not exist -- surfacing later as a
        # confusing DuckDB error far from the real cause.
        out_db = Path(job["db"])
        job["db_bytes"] = out_db.stat().st_size if out_db.exists() else 0
        job["ok"] = proc.returncode == 0 and job["db_bytes"] > 0
        if not job["ok"]:
            job["stderr"] = proc.stderr.strip()[-400:]
            if proc.returncode == 0:
                job["failure"] = f"exit 0 but output DB is missing/empty: {out_db}"
        return job

    t0 = time.monotonic()
    # Cap fan-out at one process per core; `jobs` may be far longer than that.
    pool_size = min(len(jobs), max_workers or os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        results = list(pool.map(run_one, jobs))
    wall = round(time.monotonic() - t0, 2)

    print(f"launched {len(jobs)} FVS workers, {pool_size} at a time; wall-clock {wall}s")
    for r in results:
        status = "ok" if r["ok"] else f"FAILED rc={r['returncode']}"
        print(f"  {r['name']}  stand {r['stand']}  {r['seconds']}s  {status}")
        if not r["ok"]:
            if r.get("failure"):
                print(f"    {r['failure']}")
            print(f"    stderr: {r.get('stderr', '')}")

    slowest = max((r["seconds"] for r in results), default=0)
    print(
        f"\nsum of per-worker time {sum(r['seconds'] for r in results)}s vs "
        f"wall-clock {wall}s (slowest worker {slowest}s) — the gap is the parallel win.\n"
        f"These stands are tiny (~30-70 trees), so each worker is near-instant; the\n"
        f"real speedup shows at scale (hundreds of stands / bundles), not on {len(jobs)}."
    )
    dbs = ", ".join(f"'{r['name']}': '{r['db']}'" for r in results if r["ok"])
    print(
        "\nInspect the outputs:\n"
        "  uv run python -c \"import duckdb; from research.restart_fidelity import compare_arms; "
        f"con=duckdb.connect(); compare_arms.attach_arms(con, {{{dbs}}}); "
        "print(con.execute('SELECT StandID, Year, BA, Tpa, SDI FROM pw1.FVS_Summary2 ORDER BY Year').df())\""
    )
    return results


if __name__ == "__main__":
    # The entry point is where a staging problem becomes an exit code.
    try:
        launch()
    except RunDirNotStaged as exc:
        sys.exit(str(exc))
