"""One FVS run in an isolated directory. Its own module so forkserver workers can import it.

Fails closed exactly as `weekly-artifact/2026-09-14/make_timing_library.py::_run_one`: a run
killed by a signal or ending on an unexpected stop code is a failure however many summary
rows it wrote.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

FVS_OK_RETURNCODES = frozenset({0, 10})


def run_one(args: tuple[str, str, str, str]) -> tuple[str, str | None, list[dict]]:
    run_hash, keyfile, data_db, fvs_bin = args
    tmp = Path(tempfile.mkdtemp(prefix="fvs_"))
    try:
        shutil.copy(keyfile, tmp / "run.key")
        os.symlink(data_db, tmp / "FVS_Data.db")
        proc = subprocess.run([fvs_bin, "--keywordfile=run.key"], cwd=tmp,
                              capture_output=True, text=True, timeout=900)
        if proc.returncode not in FVS_OK_RETURNCODES:
            return run_hash, f"FVS stop code {proc.returncode}", []
        out_db = tmp / "FVS_Out.db"
        if not out_db.exists():
            return run_hash, f"no FVS_Out.db (rc={proc.returncode})", []
        con = sqlite3.connect(out_db)
        try:
            rows = pd.read_sql("SELECT * FROM FVS_Summary2", con).to_dict("records")
        finally:
            con.close()
        if not rows:
            return run_hash, "empty FVS_Summary2", []
        return run_hash, None, rows
    except Exception as exc:  # noqa: BLE001 - reported per run, never silently dropped
        return run_hash, f"{type(exc).__name__}: {exc}", []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
