"""Step 1 — every menu variant's trajectory library, priced with FVS.

For each variant in `menus.VARIANTS`, every managed stand of the 2026-09-14 carved landscape
gets its owner-and-type menu, every prescription on it gets every timing offset, and each
`(plot, prescription, offset)` renders to a keyfile. Keyfiles are deduplicated **by content
hash** across variants, so a run shared by two menus is run once, and the FVS cache in
`work/` is reused across invocations: only hashes it lacks are run. Riparian stands take
`no_management` only, in every variant.

Inputs are 2026-09-14's, unchanged: `data/interim/timing_library/carved_stands.csv` (11,831
stands) and that batch's `FVS_Data.db` (the 676 donor plots, anchored to 2022).

Writes `work/library_<variant>.csv` (unit -> option -> run hash), `work/fvs_cycles.csv.gz`
(the run cache, per run hash and cycle) and `outputs/library_size.csv`.

    uv run python experiments/2026-10-08_permissive-menus/01_fvs_library.py [--workers N]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import logging
import os
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
sys.path[:0] = [str(EXP), str(REPO)]

import fvs_runner  # noqa: E402
import menus  # noqa: E402
from pipeline.ids import as_id_series  # noqa: E402

log = logging.getLogger("permissive_library")

WORK = EXP / "work"
OUTPUTS = EXP / "outputs"
KEYFILES = WORK / "keyfiles"
CYCLES = WORK / "fvs_cycles.csv.gz"
FAILURES = WORK / "fvs_failures.csv"

PREV_WORK = REPO / "data/interim/timing_library"
STANDS = PREV_WORK / "carved_stands.csv"
FVS_DATA_DB = PREV_WORK / "FVS_Data.db"
PREV_CYCLES = PREV_WORK / "trajectory_cycles.csv"
FVS_BIN = Path(os.environ.get("FVSSN_BIN", REPO / "fvs/bin/FVSsn"))
ID_COLS = {"PLT_CN": str, "unit_id": str, "tm_id": str}


def load_prev(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TL = load_prev("timing_library_20260914", REPO / "weekly-artifact/2026-09-14/make_timing_library.py")


def load_stands() -> pd.DataFrame:
    stands = pd.read_csv(STANDS, dtype=ID_COLS)
    stands["PLT_CN"] = as_id_series(stands["PLT_CN"], column="PLT_CN")
    if len(stands) != 11831:
        raise AssertionError(f"expected 2026-09-14's 11,831 carved stands, got {len(stands)}")
    return stands


def plot_sdi() -> dict[str, dict[str, float]]:
    con = sqlite3.connect(FVS_DATA_DB)
    trees = pd.read_sql("SELECT * FROM FVS_TreeInit_Plot", con)
    con.close()
    return TL.stand_sdi_tables(trees)


def enumerate_variant(variant: menus.MenuVariant, stands: pd.DataFrame, sdi: dict,
                      keyfiles: dict[str, str], entry_cache: dict) -> pd.DataFrame:
    """One row per (stand, option): the label the scheduler sees and the run it reads."""
    config = menus.load_variant_config(variant)
    plots = stands.drop_duplicates("PLT_CN").set_index("PLT_CN")

    # Per plot, the union of what its stands may take; per (plot, prescription), its runs.
    runs: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def runs_for(plt: str, prescription: str) -> list[tuple[str, str]]:
        key = (plt, prescription)
        if key in runs:
            return runs[key]
        age = float(plots.at[plt, "stand_age"])
        ekey = (variant.owner_menu, variant.rotations, prescription, plt)
        if ekey not in entry_cache:
            template, _ = menus.resolve_first_rotation(config, prescription, age)
            entry_cache[ekey] = (template, menus.build_entries(
                config, prescription, age, variant.rotations, sdi.get(plt)))
        template, entries = entry_cache[ekey]
        out = []
        offsets = (0,) if prescription == "no_management" else variant.offsets
        for off in offsets:
            shifted = menus.shift_entries(entries, off)
            if not shifted and prescription != "no_management":
                continue          # collapsed to no entry: `no_management` already covers it
            text = menus.render(plt, template, shifted)
            h = hashlib.sha256(text.encode()).hexdigest()[:20]
            keyfiles.setdefault(h, text)
            out.append((menus.variant_label(prescription, off), h))
        runs[key] = out
        return out

    rows = []
    for s in stands.itertuples(index=False):
        menu = (["no_management"] if s.unit_class == "riparian"
                else menus.menu_for(variant, config, s.owner_class, s.forest_branch))
        for prescription in menu:
            for label, h in runs_for(s.PLT_CN, prescription):
                rows.append({"unit_id": s.unit_id, "PLT_CN": s.PLT_CN, "prescription": label,
                             "base_prescription": prescription, "run_hash": h})
    lib = pd.DataFrame(rows)
    if lib.duplicated(["unit_id", "prescription"]).any():
        raise AssertionError(f"{variant.name}: a stand carries the same option twice")
    log.info("%-28s %6d options over %d stands (%.1f per stand), %d distinct runs",
             variant.name, len(lib), lib["unit_id"].nunique(),
             len(lib) / lib["unit_id"].nunique(), lib["run_hash"].nunique())
    return lib


def run_missing(keyfiles: dict[str, str], plt_of: dict[str, str], workers: int) -> None:
    have = set(pd.read_csv(CYCLES, usecols=["run_hash"])["run_hash"]) if CYCLES.exists() else set()
    todo = sorted(set(keyfiles) - have)
    log.info("FVS: %d distinct runs, %d cached, %d to run on %d workers",
             len(keyfiles), len(set(keyfiles) & have), len(todo), workers)
    if not todo:
        return
    KEYFILES.mkdir(parents=True, exist_ok=True)
    tasks = []
    for h in todo:
        path = KEYFILES / f"{h}.key"
        path.write_text(keyfiles[h])
        tasks.append((h, str(path), str(FVS_DATA_DB), str(FVS_BIN)))

    raw, failures = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fvs_runner.run_one, t) for t in tasks]
        for n, fut in enumerate(as_completed(futures), start=1):
            h, err, rows = fut.result()
            if err:
                failures.append({"run_hash": h, "error": err})
            for r in rows:
                r["run_hash"] = h
            raw.extend(rows)
            if n % 2000 == 0:
                log.info("  %d/%d runs (%d failed)", n, len(tasks), len(failures))

    df = pd.DataFrame(raw)
    df["PLT_CN"] = df["run_hash"].map(plt_of)
    df["prescription"] = df["run_hash"]       # 2026-09-14's merge keys on this column
    df = TL.merge_harvest_year_rows(df)
    df = df[df["Year"] >= menus.INV_YEAR].copy()
    df["cycle"] = ((df["Year"] - menus.INV_YEAR) // menus.CYCLE_YEARS).astype(int)
    complete = df.groupby("run_hash")["cycle"].apply(lambda c: set(c) == set(range(12)))
    bad = complete.index[~complete]
    failures += [{"run_hash": h, "error": "incomplete cycle grid"} for h in bad]
    df = df[~df["run_hash"].isin(set(bad))]
    out = df[["run_hash", "PLT_CN", "cycle", "Year", "Age", "BA", "MCuFt", "RMCuFt"]].rename(
        columns={"Year": "calendar_year", "RMCuFt": "removed_merch_cuft_per_ac"})
    if CYCLES.exists():
        out = pd.concat([pd.read_csv(CYCLES, dtype={"PLT_CN": str}), out], ignore_index=True)
    out.to_csv(CYCLES, index=False)
    prev_fail = pd.read_csv(FAILURES) if FAILURES.exists() else pd.DataFrame()
    pd.concat([prev_fail, pd.DataFrame(failures, columns=["run_hash", "error"])]).to_csv(
        FAILURES, index=False)
    log.info("FVS done: %d runs added, %d failed", out["run_hash"].nunique() - len(have),
             len(failures))


def check_baseline_against_20260914(lib: pd.DataFrame) -> dict:
    """Do the baseline's runs reproduce 2026-09-14's harvest, run for run?

    Not required to: the menus are resolved by today's `assign_prescription`, which has
    since gained the minimum-harvest-age rule. Reported so the baseline's distance from the
    published plan is a measured number rather than an assumption.
    """
    cyc = pd.read_csv(CYCLES, dtype={"PLT_CN": str})
    ours = (lib.drop_duplicates(["PLT_CN", "prescription"])
            .merge(cyc[cyc["cycle"].between(1, 10)], on=["run_hash", "PLT_CN"])
            .groupby(["PLT_CN", "prescription"])["removed_merch_cuft_per_ac"].sum())
    prev = pd.read_csv(PREV_CYCLES, dtype={"PLT_CN": str})
    prev = (prev[prev["cycle"].between(1, 10)]
            .groupby(["PLT_CN", "prescription"])["removed_merch_cuft_per_ac"].sum())
    common = ours.index.intersection(prev.index)
    same = ((ours[common] - prev[common]).abs() < 1e-3)
    out = {"baseline_runs": int(len(ours)), "published_runs": int(len(prev)),
           "in_both": int(len(common)), "identical_harvest": int(same.sum()),
           "only_baseline": int(len(ours.index.difference(prev.index))),
           "only_published": int(len(prev.index.difference(ours.index)))}
    log.info("Baseline vs 2026-09-14 library: %s", out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--dry-run", action="store_true", help="enumerate and count only")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("pipeline.s4_fvs.regime_templates").setLevel(logging.ERROR)
    WORK.mkdir(exist_ok=True)
    OUTPUTS.mkdir(exist_ok=True)

    stands = load_stands()
    sdi = plot_sdi()
    keyfiles: dict[str, str] = {}
    entry_cache: dict = {}
    libs = {v.name: enumerate_variant(v, stands, sdi, keyfiles, entry_cache)
            for v in menus.VARIANTS}

    size = pd.DataFrame([{
        "variant": name, "options": len(lib), "options_per_stand": len(lib) / lib.unit_id.nunique(),
        "max_options_per_stand": int(lib.groupby("unit_id").size().max()),
        "distinct_runs": lib["run_hash"].nunique(),
        "description": next(v.description for v in menus.VARIANTS if v.name == name),
    } for name, lib in libs.items()])
    size.to_csv(OUTPUTS / "library_size.csv", index=False)
    if args.dry_run:
        print(size.to_string(index=False), f"\nunion of distinct runs: {len(keyfiles)}")
        return

    plt_of = {}
    for lib in libs.values():
        plt_of.update(zip(lib["run_hash"], lib["PLT_CN"]))
    run_missing(keyfiles, plt_of, args.workers)

    failed = set(pd.read_csv(FAILURES)["run_hash"]) if FAILURES.exists() else set()
    for name, lib in libs.items():
        if lib["run_hash"].isin(failed).any():
            log.warning("%s: %d options have no FVS trajectory and will be dropped",
                        name, int(lib["run_hash"].isin(failed).sum()))
        lib.to_csv(WORK / f"library_{name}.csv", index=False)
    check = check_baseline_against_20260914(libs["baseline"])
    pd.Series(check).to_csv(OUTPUTS / "baseline_vs_20260914_library.csv", header=["value"])


if __name__ == "__main__":
    main()
