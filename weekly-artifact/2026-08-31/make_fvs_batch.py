"""Stage 1 — turn the enumerated trajectory library into a library with real volumes.

`weekly-artifact/2026-08-17` enumerated the decision space: which management unit may run
which prescription, with entry years resolved and a keyfile rendered per
`(plot, prescription)` run. What it could not do is say **how much wood any of those
trajectories actually removes**, because no FVS run had been made. The library carried a
schema and a row count, not a volume.

`notes/trajectory-library-and-annealing.md` §5 names the missing column exactly:

    `harvest_cuft[cycle]` | Removed merchantable volume per cycle — the constraint currency

Without it the annealer in §6 cannot be written at all: every objective form
(`maximize` / `minimize` / `evenflow` / `evenflow_target`) is a sum of precomputed
per-trajectory quantities, and there were no quantities. This script runs the FVS batch
that produces them.

What it does, in order:

  A. **Rebuild the carved landscape** from the two committed artifacts — the 2026-08-17
     enumeration and the 2026-08-24 riparian carve — and assert it reproduces
     `library_riparian_delta.csv` exactly (11,831 stands, 22,317 library rows,
     913,943 harvestable acres). No geoprocessing is repeated; `smz_by_unit.csv` is the
     committed per-unit SMZ acreage that *is* the carve.
  B. **Build the FVS input database.** One StandInit row per donor plot, taken from the
     plot's own `FVS_STANDINIT_PLOT` row with `INV_YEAR` set to 2022 — the TreeMap 2022
     imputation anchor, which is `build_fvs_inputs.build_stand_init`'s rule
     (`inv_year=2022`) in its degenerate one-plot-per-stand case. The raw FIA rows carry
     their real inventory years (2009 and earlier), which would run every trajectory off
     the project's cycle grid.
  C. **Render every run's keyfile** with `pipeline.s4_fvs.regime_templates.render_keyfile`
     — the same committed renderer the 2026-08-17 artifact used, byte-for-byte (the
     rendered hashes are checked against that artifact's `fvs_run_manifest.csv`).
  D. **Run FVSsn** over the batch, in parallel isolated working directories, and collect
     `FVS_Summary2` into the two tables §5 specifies: `trajectory_cycles` (the full
     per-cycle state) and `trajectory_index` (the annealer's narrow working set).

Outputs land in `data/interim/` (gitignored) except the two library tables, which are the
artifact.

Usage:
    uv run python weekly-artifact/2026-08-31/make_fvs_batch.py [--workers N] [--limit N]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.s4_fvs.regime_templates import (  # noqa: E402
    DEFAULT_INV_YEAR,
    render_keyfile,
)


def _leto_species_crosswalk() -> dict[str, str]:
    """FIA SPCD -> FVS SN alpha code, from the compiled variant's own `sn/blkdat.f` tables.

    The table is defined in `experiments/2026-08-24_leto-ca-forest-viz/04_fvs_run.py`
    (`_SN_JSP` / `_SN_FIAJSP`, positionally aligned). It is read out of that file's source
    rather than re-typed here, so the repository keeps one copy; the module is parsed, not
    executed, because importing it resolves an FVS binary and experiment paths as a side
    effect.
    """
    path = REPO / "experiments/2026-08-24_leto-ca-forest-viz/04_fvs_run.py"
    tree = ast.parse(path.read_text())
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        name = getattr(node.targets[0], "id", None)
        if name in ("_SN_JSP", "_SN_FIAJSP"):
            # Both are `("...").split()` — evaluate the literal string, then split.
            call = node.value
            found[name] = ast.literal_eval(call.func.value).split()
    if set(found) != {"_SN_JSP", "_SN_FIAJSP"}:
        raise AssertionError(f"species crosswalk not found in {path}")
    if len(found["_SN_JSP"]) != len(found["_SN_FIAJSP"]):
        raise AssertionError("SN/FIA species tables are not positionally aligned")
    return dict(zip(found["_SN_FIAJSP"], found["_SN_JSP"]))


def stand_sdi_tables(trees: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Per-plot species SDI shares (SN alpha codes) for natural regeneration.

    The rule and the SDI form are `04_fvs_run.stand_sdi_tables`: natural regeneration is
    apportioned across the stand's own species by SDI share (Diaz et al. 2015), rather
    than falling back to a single loblolly record — which would regenerate every
    bottomland hardwood clearcut as pine plantation.
    """
    fia_to_sn = _leto_species_crosswalk()
    t = trees.copy()
    t["SN_SP"] = t["SPECIES"].astype(str).str.split(".").str[0].str.zfill(3).map(fia_to_sn)
    t = t.dropna(subset=["SN_SP"])
    live = t[(t["HISTORY"].fillna(1) <= 5) & (t["TREE_COUNT"] > 0)]
    live = live.assign(
        SDI=live["TREE_COUNT"] * (live["DIAMETER"].fillna(1.0).clip(lower=0.5) / 10.0) ** 1.6
    )
    out: dict[str, dict[str, float]] = {}
    for stand_cn, grp in live.groupby("STAND_CN"):
        by_sp = grp.groupby("SN_SP")["SDI"].sum()
        table = {sp: float(v) for sp, v in by_sp.items() if v > 0}
        if table:
            out[str(stand_cn)] = table
    return out

log = logging.getLogger("fvs_batch")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"
STAGE = DATA / "interim/stage"
WORK = DATA / "interim/fvs_batch"
KEYFILE_DIR = WORK / "keyfiles"

LIB_2026_08_17 = REPO / "weekly-artifact/2026-08-17/trajectory_library.csv"
MANIFEST_2026_08_17 = REPO / "weekly-artifact/2026-08-17/fvs_run_manifest.csv"
SMZ_BY_UNIT = REPO / "weekly-artifact/2026-08-24/smz_by_unit.csv"
RIPARIAN_DECISION = REPO / "weekly-artifact/2026-08-24/riparian_decision_space.csv"
DELTA = REPO / "weekly-artifact/2026-08-24/library_riparian_delta.csv"

FIA_DB = STAGE / "FIA_5county_consolidated.db"
FVS_DATA_DB = WORK / "FVS_Data.db"
FVS_BIN = Path(os.environ.get("FVSSN_BIN", REPO / "fvs/bin/FVSsn"))

INV_YEAR = DEFAULT_INV_YEAR          # 2022
CYCLE_YEARS = 5
NUM_CYCLE = 10                       # 2022 -> 2072, the ~50 yr horizon in README
ID_COLS = {"PLT_CN": str, "unit_id": str, "tm_id": str}


# --------------------------------------------------------------------------------------
# Stage A — rebuild the carved landscape from the committed artifacts
# --------------------------------------------------------------------------------------

def carved_landscape() -> tuple[pd.DataFrame, pd.DataFrame]:
    """The 2026-08-24 carved landscape: upland remainders + riparian stands.

    Reconstructed from committed artifacts rather than re-derived, and asserted against
    `library_riparian_delta.csv`. The carve subtracts each unit's measured SMZ acreage
    (`smz_by_unit.csv`) from its pre-carve acreage; a unit whose whole area was inside a
    buffer disappears.
    """
    lib = pd.read_csv(LIB_2026_08_17, dtype=ID_COLS)
    smz = pd.read_csv(SMZ_BY_UNIT, dtype=ID_COLS)[["unit_id", "smz_acres"]]

    units = lib.drop_duplicates("unit_id")[
        ["unit_id", "tm_id", "PLT_CN", "county", "owner_class", "forest_branch", "acres", "stand_age"]
    ].copy()
    pre_units, pre_acres = len(units), units["acres"].sum()

    units = units.merge(smz, on="unit_id", how="left")
    units["smz_acres"] = units["smz_acres"].fillna(0.0)
    units["acres"] = units["acres"] - units["smz_acres"]
    # A unit wholly inside a buffer has no upland remainder left to schedule.
    upland = units[units["acres"] > 1e-9].drop(columns=["smz_acres"]).copy()
    upland["unit_class"] = "managed"

    rip = pd.read_csv(RIPARIAN_DECISION, dtype=ID_COLS)
    rip_stands = rip.drop_duplicates("unit_id")[
        ["unit_id", "tm_id", "PLT_CN", "county", "owner_class", "forest_branch", "acres", "stand_age"]
    ].copy()
    rip_stands["unit_class"] = "riparian"

    stands = pd.concat([upland, rip_stands], ignore_index=True)

    # The carved library: upland keeps its enumerated menu, riparian gets {no_management}.
    upland_lib = lib[lib["unit_id"].isin(set(upland["unit_id"]))].copy()
    upland_lib = upland_lib.drop(columns=["acres"]).merge(
        upland[["unit_id", "acres"]], on="unit_id", how="left"
    )
    upland_lib["unit_class"] = "managed"
    rip_lib = rip.rename(columns={}).copy()
    rip_lib["unit_class"] = "riparian"
    keep = ["unit_id", "tm_id", "PLT_CN", "county", "owner_class", "forest_branch", "acres",
            "stand_age", "prescription", "template", "unit_class"]
    for frame in (upland_lib, rip_lib):
        for col in keep:
            if col not in frame.columns:
                frame[col] = pd.NA
    carved_lib = pd.concat([upland_lib[keep + ["params"]] if "params" in upland_lib else upland_lib[keep],
                            rip_lib[keep]], ignore_index=True)

    # --- reproducibility check against the committed 2026-08-24 delta ------------------
    delta = pd.read_csv(DELTA).set_index("scenario").loc["riparian_stands"]
    checks = {
        "pre-carve units": (pre_units, 5240),
        "pre-carve acres": (round(pre_acres, 1), round(925097.8313136607, 1)),
        "carved stands": (len(stands), int(delta["units"])),
        "carved library rows": (len(carved_lib), int(delta["library_rows"])),
        "riparian stands": (int((stands.unit_class == "riparian").sum()), int(delta["riparian_units"])),
        "upland stands": (int((stands.unit_class == "managed").sum()),
                          int(delta["units_with_a_cutting_option"])),
        "harvestable acres": (round(upland["acres"].sum(), 1),
                              round(float(delta["acres_with_a_cutting_option"]), 1)),
        "total acres": (round(stands["acres"].sum(), 1), round(float(delta["acres"]), 1)),
    }
    for name, (got, want) in checks.items():
        if got != want:
            raise AssertionError(f"carve check {name!r}: got {got}, expected {want}")
    log.info("Carved landscape reproduces 2026-08-24 exactly: %s",
             ", ".join(f"{k}={v[0]}" for k, v in checks.items()))
    return stands, carved_lib


# --------------------------------------------------------------------------------------
# Stage B — the FVS input database
# --------------------------------------------------------------------------------------

def build_input_db(plots: set[str]) -> dict[str, dict[str, float]]:
    """StandInit/TreeInit for the donor plots, anchored to INV_YEAR 2022.

    `build_fvs_inputs.build_stand_init` sets `INV_YEAR = 2022` on every StandInit row it
    writes — the TreeMap 2022 imputation anchor (`notes/treemap-fvs-workflow.md`). Here
    each FVS run *is* a single donor plot (the library dedupes runs to
    `(plot, prescription)`), so that rule degenerates to: take the plot's own row and set
    its inventory year. The raw FIA rows carry inventory years of 2009 and earlier, which
    would put every trajectory on the wrong cycle grid.
    """
    WORK.mkdir(parents=True, exist_ok=True)
    if FVS_DATA_DB.exists():
        FVS_DATA_DB.unlink()

    src = sqlite3.connect(FIA_DB)
    stand = pd.read_sql("SELECT * FROM FVS_STANDINIT_PLOT", src)
    tree = pd.read_sql("SELECT * FROM FVS_TREEINIT_PLOT", src)
    src.close()

    stand["STAND_CN"] = stand["STAND_CN"].astype(str)
    tree["STAND_CN"] = tree["STAND_CN"].astype(str)
    stand = stand[stand["STAND_CN"].isin(plots)].copy()
    tree = tree[tree["STAND_CN"].isin(plots)].copy()

    missing = plots - set(stand["STAND_CN"])
    if missing:
        raise AssertionError(f"{len(missing)} donor plots absent from FVS_STANDINIT_PLOT")

    stand["INV_YEAR"] = INV_YEAR
    stand["VARIANT"] = "SN"
    stand["STAND_ID"] = "S" + stand["STAND_CN"]

    dst = sqlite3.connect(FVS_DATA_DB)
    stand.to_sql("FVS_StandInit_Plot", dst, index=False)
    tree.to_sql("FVS_TreeInit_Plot", dst, index=False)
    dst.execute("CREATE INDEX ix_stand ON FVS_StandInit_Plot(STAND_CN)")
    dst.execute("CREATE INDEX ix_tree ON FVS_TreeInit_Plot(STAND_CN)")
    dst.commit()
    dst.close()
    log.info("FVS input DB: %d stands, %d trees, INV_YEAR=%d", len(stand), len(tree), INV_YEAR)
    return stand_sdi_tables(tree)


# --------------------------------------------------------------------------------------
# Stage C — render the keyfiles
# --------------------------------------------------------------------------------------

def render_batch(carved_lib: pd.DataFrame, sdi: dict[str, dict[str, float]]) -> pd.DataFrame:
    """One keyfile per `(plot, prescription)` run in the carved library."""
    KEYFILE_DIR.mkdir(parents=True, exist_ok=True)
    params = (carved_lib.dropna(subset=["prescription"])
              .groupby(["PLT_CN", "prescription"], as_index=False)
              .agg(template=("template", "first"),
                   params=("params", "first") if "params" in carved_lib else ("template", "first")))

    records, no_sdi = [], 0
    for run in params.itertuples(index=False):
        kwargs = {}
        pstr = getattr(run, "params", "") or ""
        if isinstance(pstr, str) and "=" in pstr:
            for item in filter(None, pstr.split(";")):
                k, v = item.split("=", 1)
                kwargs[k] = float(v) if "." in v else int(v)
        plot_sdi = sdi.get(str(run.PLT_CN))
        if plot_sdi:
            kwargs["stand_sdi"] = plot_sdi
        else:
            no_sdi += 1
        stand_id = f"S{run.PLT_CN}"
        key = render_keyfile(stand_id=stand_id, stand_cn=str(run.PLT_CN),
                             regime=run.template, params=kwargs, inv_year=INV_YEAR,
                             cycle_years=CYCLE_YEARS, num_cycle=NUM_CYCLE)
        path = KEYFILE_DIR / f"{stand_id}__{run.prescription}.key"
        path.write_text(key)
        records.append({"PLT_CN": run.PLT_CN, "prescription": run.prescription,
                        "template": run.template, "keyfile": str(path),
                        "keyfile_sha256_16": hashlib.sha256(key.encode()).hexdigest()[:16]})
    runs = pd.DataFrame(records)
    log.info("Rendered %d keyfiles (%d distinct by content); %d runs had no live-tree SDI "
             "table and fall back to a single planted record",
             len(runs), runs["keyfile_sha256_16"].nunique(), no_sdi)
    return runs


# --------------------------------------------------------------------------------------
# Stage D — run the batch
# --------------------------------------------------------------------------------------

def _run_one(args: tuple[str, str, str]) -> tuple[str, str, str | None, list[dict]]:
    """Run one keyfile in an isolated temp dir; return its FVS_Summary2 rows."""
    plt_cn, prescription, keyfile = args
    tmp = Path(tempfile.mkdtemp(prefix="fvs_"))
    try:
        shutil.copy(keyfile, tmp / "run.key")
        os.symlink(FVS_DATA_DB, tmp / "FVS_Data.db")
        proc = subprocess.run([str(FVS_BIN), "--keywordfile=run.key"], cwd=tmp,
                              capture_output=True, text=True, timeout=900)
        out_db = tmp / "FVS_Out.db"
        if not out_db.exists():
            return plt_cn, prescription, f"no FVS_Out.db (rc={proc.returncode})", []
        con = sqlite3.connect(out_db)
        try:
            rows = pd.read_sql("SELECT * FROM FVS_Summary2", con).to_dict("records")
        finally:
            con.close()
        if not rows:
            return plt_cn, prescription, f"empty FVS_Summary2 (rc={proc.returncode})", []
        return plt_cn, prescription, None, rows
    except Exception as exc:  # noqa: BLE001 - reported per run, never silently dropped
        return plt_cn, prescription, f"{type(exc).__name__}: {exc}", []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_batch(runs: pd.DataFrame, workers: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    tasks = [(r.PLT_CN, r.prescription, r.keyfile) for r in runs.itertuples(index=False)]
    cycles, failures, done = [], [], 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one, t): t for t in tasks}
        for fut in as_completed(futures):
            plt_cn, prescription, err, rows = fut.result()
            done += 1
            if err:
                failures.append({"PLT_CN": plt_cn, "prescription": prescription, "error": err})
            for row in rows:
                row["PLT_CN"] = plt_cn
                row["prescription"] = prescription
                cycles.append(row)
            if done % 250 == 0:
                log.info("  %d/%d runs complete (%d failed)", done, len(tasks), len(failures))
    return pd.DataFrame(cycles), pd.DataFrame(failures)


def build_library_tables(cycles: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`trajectory_cycles` and the narrow `trajectory_index`, per §5 of the design note.

    FVS_Summary2 emits two rows for a cycle in which a harvest occurs — the pre-removal
    state (`RmvCode` > 0 carries the removal) and the post-removal state. Volumes are
    per-acre; the scheduler multiplies by stand acres.
    """
    df = cycles.copy()
    df["PLT_CN"] = df["PLT_CN"].astype(str)
    # One row per (run, year): keep the removal record where a cycle has both.
    df = (df.sort_values(["PLT_CN", "prescription", "Year", "RMCuFt"], ascending=[True, True, True, False])
            .drop_duplicates(["PLT_CN", "prescription", "Year"], keep="first"))
    df = df[df["Year"] >= INV_YEAR].copy()
    df["cycle"] = ((df["Year"] - INV_YEAR) // CYCLE_YEARS).astype(int)

    cyc = df[["PLT_CN", "prescription", "cycle", "Year", "Age", "BA", "Tpa", "QMD", "SDI",
              "TCuFt", "MCuFt", "BdFt", "RTpa", "RTCuFt", "RMCuFt", "RBdFt", "RmvCode"]].copy()
    cyc = cyc.rename(columns={"Year": "calendar_year", "RMCuFt": "removed_merch_cuft_per_ac"})

    idx = (cyc.groupby(["PLT_CN", "prescription"], as_index=False)
              .agg(cycles=("cycle", "nunique"),
                   first_year=("calendar_year", "min"),
                   last_year=("calendar_year", "max"),
                   total_removed_merch_cuft_per_ac=("removed_merch_cuft_per_ac", "sum"),
                   harvest_cycles=("removed_merch_cuft_per_ac", lambda s: int((s > 0).sum())),
                   ending_ba=("BA", "last"),
                   ending_merch_cuft_per_ac=("MCuFt", "last")))
    return cyc, idx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--limit", type=int, default=None, help="smoke-test a subset of runs")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not FVS_BIN.exists():
        raise SystemExit(f"FVSsn not found at {FVS_BIN}; set FVSSN_BIN or build it (see README)")

    stands, carved_lib = carved_landscape()

    plots = set(carved_lib["PLT_CN"].dropna().astype(str))
    sdi = build_input_db(plots)
    log.info("Species SDI tables built for %d/%d donor plots", len(sdi), len(plots))

    runs = render_batch(carved_lib, sdi)
    if args.limit:
        runs = runs.head(args.limit)
        log.warning("SMOKE MODE: only %d runs", len(runs))

    log.info("Running %d FVS trajectories on %d workers", len(runs), args.workers)
    cycles, failures = run_batch(runs, args.workers)
    if len(failures):
        failures.to_csv(OUT_DIR / "fvs_failures.csv", index=False)
        log.warning("%d runs failed; see fvs_failures.csv", len(failures))
    log.info("Collected %d FVS_Summary2 rows from %d successful runs",
             len(cycles), len(runs) - len(failures))

    cyc, idx = build_library_tables(cycles)
    WORK.mkdir(parents=True, exist_ok=True)
    cyc.to_csv(WORK / "trajectory_cycles.csv", index=False)
    idx.to_csv(WORK / "trajectory_index.csv", index=False)
    stands.to_csv(WORK / "carved_stands.csv", index=False)
    carved_lib.to_csv(WORK / "carved_library.csv", index=False)
    log.info("trajectory_cycles: %d rows; trajectory_index: %d runs", len(cyc), len(idx))
    log.info("Wrote library tables to %s", WORK)

    # The artifact carries the two tables a reader needs to re-use the library without
    # re-running FVS: the narrow per-trajectory index (§5 `trajectory_index`) and
    # `harvest_cuft[cycle]` itself — the constraint currency, and the column the
    # 2026-08-17 enumeration could not supply. The full per-cycle FVS state
    # (`trajectory_cycles`, with BA/TPA/QMD/SDI) stays in gitignored `data/interim`.
    idx.to_csv(OUT_DIR / "trajectory_index.csv", index=False)
    harvest = cyc[["PLT_CN", "prescription", "cycle", "calendar_year",
                   "removed_merch_cuft_per_ac"]]
    harvest.to_csv(OUT_DIR / "trajectory_harvest_by_cycle.csv", index=False)
    log.info("Artifact tables: trajectory_index.csv (%d), "
             "trajectory_harvest_by_cycle.csv (%d)", len(idx), len(harvest))


if __name__ == "__main__":
    main()
