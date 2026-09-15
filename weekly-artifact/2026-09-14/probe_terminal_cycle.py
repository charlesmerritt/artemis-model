"""Why the library needs an eleventh FVS cycle to reach the tenth.

2026-08-31 reported that nothing in the trajectory library harvested in cycle 10 (2072) and
read that as a gap in the enumerated decision space — something a timing-offset grid would
fill. It is not a gap in the library. It is a property of the projection, and this is the FVS
run that shows it.

Three runs of the *same* stand and the *same* committed keyfile renderer:

  1. a clearcut scheduled in **2067** under a ten-cycle projection — reported, as expected;
  2. the same clearcut in **2072**, the horizon's final year, under ten cycles — **no removal
     at all**: the activity never executes, because 2072 is where the projection stops;
  3. the same clearcut in 2072 under **eleven** cycles — executed, and reported in the 2072
     row exactly as (1) is reported in 2067.

And the check that makes (3) usable: every summary row from 2022 to 2072 is identical between
a ten-cycle and an eleven-cycle run of the same prescription. The extra cycle carries the
final year's activity; it does not perturb the horizon it extends.

So cycle 10 was never reachable by any selection from any library under a ten-cycle
projection, and no timing offset could have made it reachable. `make_timing_library.py` runs
eleven cycles for this reason, scores ten, and `check_base_library` verifies the same
non-interference across all 3,000-odd offset-0 runs rather than only the two here.

Writes `terminal_cycle_probe.csv` (every summary row of all five runs) and prints the
verdict. Run `make_timing_library.py` first — this reads the FVS input database it builds.

Usage:
    uv run python weekly-artifact/2026-09-14/probe_terminal_cycle.py
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.s4_fvs.regime_templates import render_keyfile  # noqa: E402

log = logging.getLogger("probe")

OUT_DIR = Path(__file__).resolve().parent
WORK = REPO / "data/interim/timing_library"
FVS_DATA_DB = WORK / "FVS_Data.db"
FVS_BIN = Path(os.environ.get("FVSSN_BIN", REPO / "fvs/bin/FVSsn"))
INV_YEAR = 2022
CYCLE_YEARS = 5
LAST_ENTRY_YEAR = 2072

# One donor plot, taken from the committed library rather than chosen by hand: the first
# plot enumerated for `hardwood_clearcut_regen`, the prescription whose entry year moves with
# stand age and therefore the one that lands in 2072 for real stands.
LIB = REPO / "weekly-artifact/2026-08-17/trajectory_library.csv"

STATE_COLS = ["Year", "RmvCode", "Age", "BA", "Tpa", "SDI", "QMD", "TCuFt", "MCuFt",
              "RTpa", "RMCuFt"]


def probe_plot() -> str:
    lib = pd.read_csv(LIB, dtype={"PLT_CN": str})
    return lib.loc[lib["prescription"] == "hardwood_clearcut_regen", "PLT_CN"].iloc[0]


def run_fvs(plot: str, regime: str, params: dict, num_cycle: int) -> pd.DataFrame:
    """One FVS run of the repository's own rendered keyfile; its FVS_Summary2 rows."""
    key = render_keyfile(stand_id=f"S{plot}", stand_cn=plot, regime=regime, params=params,
                         inv_year=INV_YEAR, cycle_years=CYCLE_YEARS, num_cycle=num_cycle)
    tmp = Path(tempfile.mkdtemp(prefix="probe_"))
    try:
        (tmp / "run.key").write_text(key)
        os.symlink(FVS_DATA_DB, tmp / "FVS_Data.db")
        proc = subprocess.run([str(FVS_BIN), "--keywordfile=run.key"], cwd=tmp,
                              capture_output=True, text=True, timeout=900)
        if proc.returncode < 0:
            raise SystemExit(f"FVS killed by signal {-proc.returncode}")
        con = sqlite3.connect(tmp / "FVS_Out.db")
        try:
            return pd.read_sql(f"SELECT {', '.join(STATE_COLS)} FROM FVS_Summary2", con)
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not FVS_BIN.exists():
        raise SystemExit(f"FVSsn not found at {FVS_BIN}; build it (see the artifact README)")
    if not FVS_DATA_DB.exists():
        raise SystemExit(f"no FVS input database at {FVS_DATA_DB}; run make_timing_library.py "
                         f"first — this probe deliberately reuses the batch's own input")

    plot = probe_plot()
    log.info("Probe stand: donor plot %s (first plot enumerated for hardwood_clearcut_regen)",
             plot)

    cases = [
        ("clearcut", {"year": 2067}, 10, "entry one cycle inside the horizon"),
        ("clearcut", {"year": LAST_ENTRY_YEAR}, 10, "entry in the horizon's final year"),
        ("clearcut", {"year": LAST_ENTRY_YEAR}, 11, "the same, with a carrier cycle"),
        ("clearcut", {"year": 2062}, 10, "non-interference check, ten cycles"),
        ("clearcut", {"year": 2062}, 11, "non-interference check, eleven cycles"),
    ]
    frames, results = [], {}
    for regime, params, num_cycle, label in cases:
        df = run_fvs(plot, regime, params, num_cycle)
        results[(params["year"], num_cycle)] = df
        frames.append(df.assign(entry_year=params["year"], num_cycle=num_cycle,
                                case=label, PLT_CN=plot))
    out = pd.concat(frames, ignore_index=True)
    out = out[["PLT_CN", "case", "entry_year", "num_cycle", *STATE_COLS]]
    out.to_csv(OUT_DIR / "terminal_cycle_probe.csv", index=False)

    def removed_in(df: pd.DataFrame, year: int) -> float:
        return float(df.loc[df["Year"] == year, "RMCuFt"].max())

    r_2067 = removed_in(results[(2067, 10)], 2067)
    r_2072_10 = removed_in(results[(LAST_ENTRY_YEAR, 10)], LAST_ENTRY_YEAR)
    r_2072_11 = removed_in(results[(LAST_ENTRY_YEAR, 11)], LAST_ENTRY_YEAR)

    ten = results[(2062, 10)].reset_index(drop=True)
    eleven = results[(2062, 11)]
    inside = eleven[eleven["Year"] <= LAST_ENTRY_YEAR].reset_index(drop=True)
    identical = ten.equals(inside)

    log.info("Removal reported for a clearcut in 2067, ten cycles : %10.2f cuft/ac", r_2067)
    log.info("Removal reported for a clearcut in 2072, ten cycles : %10.2f cuft/ac", r_2072_10)
    log.info("Removal reported for a clearcut in 2072, eleven     : %10.2f cuft/ac", r_2072_11)
    log.info("2022-2072 rows identical between ten and eleven cycles: %s", identical)

    # These are the two claims the artifact makes from this probe. A probe that cannot
    # demonstrate them should fail rather than write a file that looks like evidence.
    if not (r_2067 > 0 and r_2072_10 == 0 and r_2072_11 > 0):
        raise AssertionError(
            f"the terminal-cycle behaviour is not what this artifact reports: 2067/10cyc="
            f"{r_2067}, 2072/10cyc={r_2072_10}, 2072/11cyc={r_2072_11}"
        )
    if not identical:
        raise AssertionError(
            "an eleventh cycle changed rows inside the horizon; the carrier cycle is not "
            "safe and the library must not be built on it:\n"
            + ten.compare(inside).to_string()
        )
    log.info("Verdict: an entry in %d is executed only when the projection runs past it, "
             "and the extra cycle leaves the horizon untouched.", LAST_ENTRY_YEAR)


if __name__ == "__main__":
    main()
