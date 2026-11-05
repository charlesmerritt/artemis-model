"""Compare Fortran FVS_Summary2 with FVSjl FVS_Summary per regime, and enforce the Phase 0 gates.

    PYTHONPATH=. uv run python -m research.fvsjl_engine_differential.compare [--work DIR]

Fortran rows are restricted to RmvCode 0/1 (pre-cut) because FVSjl writes one row per
cycle. "ghost" counts FVSjl rows with BA <= 5 and TCuFt > 500 (spec blocker B4).

A stand FVSsn never wrote is "missing"; one it wrote but not to the terminal year (a SIGFPE
after some cycles) is "partial". Final-year totals use only stands that reached the terminal
year, so a partial trajectory never stands in for a 50-year endpoint.

Exits 1 when any gate in the design spec's Phase 0 fails (section 7).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
COLS = "StandID,Year,Tpa,BA,SDI,TCuFt,RTCuFt"
METRICS = ("Tpa", "BA", "TCuFt", "RTCuFt")


class Regime(StrEnum):
    NONE = "none"
    THIN = "thin"
    PLANT = "plant"


# Phase 0: unmanaged and thin carry the fidelity gates; the ghost tripwire covers every regime.
FIDELITY_GATED = {Regime.NONE, Regime.THIN}
MIN_WITHIN_1PCT = 0.95
GATED_METRICS = ("BA", "TCuFt")
MAX_FINAL_TOTAL_DIFF = 0.002


class RegimeComparison(NamedTuple):
    merged_rows: int
    within_1pct: dict[str, float]
    over_5pct: dict[str, float]
    final_tcuft: tuple[float, float]  # FVSsn, FVSjl, over stands that reached the terminal year
    missing_stands: int
    partial_stands: int
    ghost_stands: int


def compare_regime(fortran: pd.DataFrame, fvsjl: pd.DataFrame, cns: set[str]) -> RegimeComparison:
    m = fortran.merge(fvsjl, on=["StandID", "Year"], suffixes=("_f", "_j"))
    within, over = {}, {}
    for c in METRICS:
        rel = (m[c + "_j"] - m[c + "_f"]).abs() / m[c + "_f"].abs().clip(lower=10)
        within[c], over[c] = float(np.mean(rel <= 0.01)), float(np.mean(rel > 0.05))

    terminal = fortran.Year.max()
    reached = fortran.groupby("StandID").Year.max()
    complete = set(reached[reached == terminal].index)
    last = m[(m.Year == terminal) & m.StandID.isin(complete)]
    ghost = fvsjl[(fvsjl.BA <= 5) & (fvsjl.TCuFt > 500)]
    return RegimeComparison(
        merged_rows=len(m), within_1pct=within, over_5pct=over,
        final_tcuft=(float(last.TCuFt_f.sum()), float(last.TCuFt_j.sum())),
        missing_stands=len(cns - set(fortran.StandID)),
        partial_stands=len(reached) - len(complete),
        ghost_stands=ghost.StandID.nunique())


def gate_failures(regime: Regime, r: RegimeComparison) -> list[str]:
    failures = []
    if regime in FIDELITY_GATED:
        failures += [f"{c} within 1% {r.within_1pct[c]:.3f} < {MIN_WITHIN_1PCT}"
                     for c in GATED_METRICS if r.within_1pct[c] < MIN_WITHIN_1PCT]
        f, j = r.final_tcuft
        diff = abs(j - f) / f if f else 0.0
        if diff > MAX_FINAL_TOTAL_DIFF:
            failures.append(f"final-year TCuFt differs by {diff:.2%} > {MAX_FINAL_TOTAL_DIFF:.1%}")
    if r.ghost_stands:
        failures.append(f"{r.ghost_stands} FVSjl ghost-volume stands")
    return failures


def report(regime: Regime, r: RegimeComparison) -> str:
    parts = [f"{c}: <=1%={r.within_1pct[c]:.3f} >5%={r.over_5pct[c]:.3f}" for c in METRICS]
    f, j = r.final_tcuft
    return (f"{regime}: merged rows {r.merged_rows} | " + " | ".join(parts)
            + f" | final TCuFt sum f/j {round(f)}/{round(j)}"
            + f" | fortran stands missing {r.missing_stands} partial {r.partial_stands}"
            + f" | jl ghost-volume stands {r.ghost_stands}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default=str(HERE / "work"))
    work = Path(ap.parse_args().work)
    cns = set((work / "cns.txt").read_text().split())
    failed = False
    for regime in Regime:
        f = pd.read_sql(f"SELECT {COLS} FROM FVS_Summary2 WHERE RmvCode IN (0,1)",
                        sqlite3.connect(work / f"{regime}_f" / "out.db"))
        j = pd.read_sql(f"SELECT {COLS} FROM FVS_Summary",
                        sqlite3.connect(work / f"{regime}_jl" / "out.db"))
        r = compare_regime(f, j, cns)
        print(report(regime, r))
        for failure in gate_failures(regime, r):
            print(f"  GATE FAILED {regime}: {failure}")
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
