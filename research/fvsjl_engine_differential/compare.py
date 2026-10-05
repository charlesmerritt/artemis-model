"""Compare Fortran FVS_Summary2 with FVSjl FVS_Summary per regime.

    PYTHONPATH=. uv run python -m research.fvsjl_engine_differential.compare [--work DIR]

Fortran rows are restricted to RmvCode 0/1 (pre-cut) because FVSjl writes one row per
cycle. "ghost" counts FVSjl rows with BA <= 5 and TCuFt > 500 (spec blocker B1).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
COLS = "StandID,Year,Tpa,BA,SDI,TCuFt,RTCuFt"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default=str(HERE / "work"))
    work = Path(ap.parse_args().work)
    cns = set((work / "cns.txt").read_text().split())
    for t in ("none", "thin", "plant"):
        f = pd.read_sql(f"SELECT {COLS} FROM FVS_Summary2 WHERE RmvCode IN (0,1)",
                        sqlite3.connect(work / f"{t}_f" / "out.db"))
        j = pd.read_sql(f"SELECT {COLS} FROM FVS_Summary", sqlite3.connect(work / f"{t}_jl" / "out.db"))
        m = f.merge(j, on=["StandID", "Year"], suffixes=("_f", "_j"))
        parts = []
        for c in ("Tpa", "BA", "TCuFt", "RTCuFt"):
            rel = (m[c + "_j"] - m[c + "_f"]).abs() / m[c + "_f"].abs().clip(lower=10)
            parts.append(f"{c}: <=1%={np.mean(rel <= 0.01):.3f} >5%={np.mean(rel > 0.05):.3f}")
        last = m[m.Year == m.groupby("StandID").Year.transform("max")]
        ghost = j[(j.BA <= 5) & (j.TCuFt > 500)]
        print(f"{t}: merged rows {len(m)} | " + " | ".join(parts)
              + f" | final TCuFt sum f/j {round(last.TCuFt_f.sum())}/{round(last.TCuFt_j.sum())}"
              + f" | fortran stands missing {len(cns - set(f.StandID))}"
              + f" | jl ghost-volume stands {ghost.StandID.nunique()}")


if __name__ == "__main__":
    main()
