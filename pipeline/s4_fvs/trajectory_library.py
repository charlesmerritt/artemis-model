"""
The trajectory library: every stand's eligible options, rendered to FVS keyfiles and run.

A stand's options are its owner class's eligible menu (`config/management_regimes.yaml`),
filtered by forest type, each at every timing offset (Diaz et al.'s delayed starts). Riparian
stands get only ``no_management``. Each option renders to one single-stand keyfile; options
whose keyfile text is identical share one FVS run (``run_hash``), so stands on the same donor
plot share runs. :func:`run_fvs` runs whatever is not yet cached, fail-closed, and appends
each run's per-cycle state and removed volume to ``fvs_cycles.csv.gz``. That table and the
library frame are what `pipeline.s3_management.annealer.Landscape` reads.

Promoted from `experiments/2026-10-08_permissive-menus` (`menus.py`, `01_fvs_library.py`,
`fvs_runner.py`) and two helpers of `weekly-artifact/2026-09-14/make_timing_library.py`
(`stand_sdi_tables`, `merge_harvest_year_rows`). :data:`OPEN_MENU` is that sweep's
`repeat_offsets_25` variant, the one the FL5 open-menu deck and the 2026-10-19 LETO-stand
schedule ran. The sweep's pre-2026-10-08 baseline menu stays with the experiment.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from enum import Enum
from pathlib import Path

import pandas as pd

from pipeline.ids import as_id_series
from pipeline.s3_management.regime_assignment import (
    assign_prescription,
    eligible_prescriptions,
    load_regimes_config,
)
from pipeline.s4_fvs.regime_templates import (
    _KEYFILE,
    DEFAULT_INV_YEAR,
    Regeneration,
    ThinDBH,
    build_regeneration,
    build_thins,
    render_estab_block,
    render_schedule_block,
)
from pipeline.s4_fvs.sn_species import FIA_TO_SN

log = logging.getLogger("trajectory_library")

REPO = Path(__file__).resolve().parents[2]

INV_YEAR = DEFAULT_INV_YEAR            # 2022
CYCLE_YEARS = 5
LAST_ENTRY_YEAR = INV_YEAR + 50        # 2072: the last scored cycle
NUM_CYCLE = 11                         # one carrier cycle so a 2072 entry executes
OFFSET_SEP = "@+"                      # `{prescription}@+{offset}`
FVS_OK_RETURNCODES = frozenset({0, 10})
REMOVAL_COLS = ("RTpa", "RTCuFt", "RMCuFt", "RBdFt")

OFFSETS_0_15 = (0, 5, 10, 15)          # 2026-09-14's grid
OFFSETS_0_25 = (0, 5, 10, 15, 20, 25)  # AGENTS.md's 5-25 year offsets


class RotationMode(Enum):
    SINGLE = "single"      # one rotation per keyfile
    REPEAT = "repeat"      # honour `repeats: true` inside the horizon


class ForestTypeRule(Enum):
    FILTER = "filter"      # a prescription's `forest_types` restricts who may take it
    IGNORE = "ignore"      # any stand may take any prescription (e.g. pine on hardwood)


@dataclasses.dataclass(frozen=True)
class MenuPolicy:
    rotations: RotationMode
    forest_types: ForestTypeRule
    offsets: tuple[int, ...]
    exclude: tuple[str, ...] = ()


OPEN_MENU = MenuPolicy(RotationMode.REPEAT, ForestTypeRule.FILTER, OFFSETS_0_25)


@dataclasses.dataclass(frozen=True)
class FvsTables:
    """The input-database tables a keyfile reads its stand and trees from."""
    stand: str
    tree: str


PLOT_TABLES = FvsTables("FVS_StandInit_Plot", "FVS_TreeInit_Plot")   # FIA per-plot inputs
LETO_TABLES = FvsTables("FVS_StandInit", "FVS_TreeInit")             # `leto_database`


def menu_for(policy: MenuPolicy, config: dict, owner_class: str, branch: str) -> list[str]:
    """The prescriptions a managed stand of this owner and forest branch may take."""
    branch_arg = branch if policy.forest_types is ForestTypeRule.FILTER else None
    menu = eligible_prescriptions(owner_class, branch_arg, config=config)
    return [p for p in menu if p not in policy.exclude]


# --------------------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Entry:
    """One harvest operation and the regeneration it causes (empty unless stand-replacing)."""
    thin: ThinDBH
    regen: tuple[Regeneration, ...] = ()


def resolve_first_rotation(config: dict, prescription: str, stand_age: float) -> tuple[str, dict]:
    """`(template, params)` through the repository's resolver, for any owner and branch.

    `assign_prescription` resolves an owner's *default*. Writing the prescription into every
    default slot of a config copy resolves it through that same path — minimum harvest age,
    horizon clipping and template downgrade included — whoever the stand's owner is.
    """
    cfg = copy.deepcopy(config)
    for spec in cfg["owner_classes"].values():
        spec["default"] = {b: prescription for b in spec["default"]}
    p = assign_prescription({"OWN_CODE": 3, "FORTYPCD": 161, "stand_age": stand_age,
                             "SMZ_Pct": 0.0}, config=cfg)
    return p.template, p.params


def _snap(years: float) -> int:
    return int(-(-years // CYCLE_YEARS) * CYCLE_YEARS)


def _regen_for(template: str, year: int, stand_sdi: dict | None) -> tuple[Regeneration, ...]:
    params: dict = {"clearcut_year" if template == "plantation_rotation" else "year": year}
    if stand_sdi:
        params["stand_sdi"] = stand_sdi
    return tuple(build_regeneration(template, params))


def build_entries(config: dict, prescription: str, stand_age: float, rotations: RotationMode,
                  stand_sdi: dict | None) -> list[Entry]:
    """Every operation of one prescription on one stand, unshifted, inside the horizon."""
    template, params = resolve_first_rotation(config, prescription, stand_age)
    if template == "no_management":
        return []
    regen_params = {**params, "stand_sdi": stand_sdi} if stand_sdi else params
    regen = tuple(build_regeneration(template, regen_params))
    thins = build_thins(template, params)
    last_cut = max((t.year for t in thins if t.proportion >= 1.0), default=None)
    entries = [Entry(t, regen if t.year == last_cut else ()) for t in thins]

    spec = config["prescriptions"][prescription]
    if rotations is RotationMode.REPEAT and spec.get("repeats") and last_cut is not None:
        entries += _later_rotations(spec, last_cut, stand_sdi)
    return entries


def _later_rotations(spec: dict, cut: int, stand_sdi: dict | None) -> list[Entry]:
    """The next rotations after a clearcut in `cut`, until the horizon ends.

    A replanted stand is age 0 at the cut. Its thin falls at `first_thin_age` and its
    clearcut at `rotation_years`, both snapped up to a cycle; both are at least 15 years
    after the cut, so the minimum harvest age holds. A thin whose clearcut would fall past
    the horizon is kept: it happens inside the horizon whether or not the rotation finishes.
    """
    schedule, template = spec["schedule"], spec["template"]
    rotation = _snap(schedule["rotation_years"])
    out: list[Entry] = []
    while True:
        if "first_thin_age" in schedule:
            year = cut + _snap(schedule["first_thin_age"])
            if year > LAST_ENTRY_YEAR:
                break
            thin = build_thins("plantation_rotation", {"thin_year": year, "clearcut_year": year,
                                                       **spec.get("params", {})})[0]
            out.append(Entry(thin))
        cut += rotation
        if cut > LAST_ENTRY_YEAR:
            break
        out.append(Entry(ThinDBH(cut, 1.0), _regen_for(template, cut, stand_sdi)))
    return out


def shift_entries(entries: list[Entry], offset: int) -> list[Entry]:
    """Start the whole sequence `offset` years later; drop what leaves the horizon.

    A regeneration record moves with the harvest that caused it, and goes when it goes.
    """
    out = []
    for e in entries:
        year = e.thin.year + offset
        if year > LAST_ENTRY_YEAR:
            continue
        regen = tuple(dataclasses.replace(r, year=r.year + offset) for r in e.regen)
        out.append(Entry(dataclasses.replace(e.thin, year=year), regen))
    return out


def variant_label(prescription: str, offset: int) -> str:
    return prescription if prescription == "no_management" else f"{prescription}{OFFSET_SEP}{offset}"


def split_label(label: str) -> tuple[str, int]:
    """`"public_thin_restore@+10"` -> `("public_thin_restore", 10)`. Unoffset labels give 0."""
    if OFFSET_SEP not in label:
        return label, 0
    base, off = label.rsplit(OFFSET_SEP, 1)
    return base, int(off)


def render(stand_cn: str, template: str, entries: list[Entry], *,
           tables: FvsTables = PLOT_TABLES) -> str:
    """A single-stand keyfile: the operations, then one `Estab` packet per clearcut.

    With one packet this is byte-identical to `regime_templates.render_keyfile`.
    """
    blocks = [e.thin.render() for e in entries]
    blocks += [render_estab_block(list(e.regen)) for e in entries if e.regen]
    return _KEYFILE.format(
        stand_id=f"S{stand_cn}", stand_cn=stand_cn, regime=template, mgmt_id="A001",
        schedule_block=render_schedule_block(INV_YEAR, CYCLE_YEARS, NUM_CYCLE),
        activity_block="\n".join(blocks), out_db="FVS_Out.db", in_db="FVS_Data.db",
        stand_table=tables.stand, tree_table=tables.tree, sp_label="ARTEMIS",
    )


# --------------------------------------------------------------------------------------
# The library
# --------------------------------------------------------------------------------------

def stand_sdi_tables(trees: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Per-stand species SDI shares (SN alpha codes) for natural regeneration.

    Natural regeneration is apportioned across the stand's own species by SDI share (Diaz et
    al. 2015), rather than one loblolly record that would regenerate every bottomland hardwood
    clearcut as pine plantation. Keyed by the exact ``STAND_CN`` (`as_id_series`).
    """
    t = trees.copy()
    t["STAND_CN"] = as_id_series(t["STAND_CN"], column="STAND_CN")
    t["SN_SP"] = t["SPECIES"].astype(str).str.split(".").str[0].str.zfill(3).map(FIA_TO_SN)
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
            out[stand_cn] = table
    return out


def enumerate_library(policy: MenuPolicy, stands: pd.DataFrame, sdi: dict,
                      keyfiles: dict[str, str], *, config: dict | None = None,
                      tables: FvsTables = PLOT_TABLES, entry_cache: dict | None = None
                      ) -> pd.DataFrame:
    """One row per (stand, option): the label the scheduler sees and the run it reads.

    ``stands`` carries ``unit_id``, ``PLT_CN`` (the FVS stand the keyfile reads),
    ``stand_age``, ``unit_class``, ``owner_class`` and ``forest_branch``. Rendered keyfiles are
    added to ``keyfiles`` by hash.
    """
    config = load_regimes_config() if config is None else config
    entry_cache = {} if entry_cache is None else entry_cache
    plots = stands.drop_duplicates("PLT_CN").set_index("PLT_CN")

    # Per plot, the union of what its stands may take; per (plot, prescription), its runs.
    runs: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def runs_for(plt: str, prescription: str) -> list[tuple[str, str]]:
        key = (plt, prescription)
        if key in runs:
            return runs[key]
        age = float(plots.at[plt, "stand_age"])
        ekey = (policy.rotations, prescription, plt)
        if ekey not in entry_cache:
            template, _ = resolve_first_rotation(config, prescription, age)
            entry_cache[ekey] = (template, build_entries(
                config, prescription, age, policy.rotations, sdi.get(plt)))
        template, entries = entry_cache[ekey]
        out = []
        offsets = (0,) if prescription == "no_management" else policy.offsets
        for off in offsets:
            shifted = shift_entries(entries, off)
            if not shifted and prescription != "no_management":
                continue          # collapsed to no entry: `no_management` already covers it
            text = render(plt, template, shifted, tables=tables)
            h = hashlib.sha256(text.encode()).hexdigest()[:20]
            keyfiles.setdefault(h, text)
            out.append((variant_label(prescription, off), h))
        runs[key] = out
        return out

    rows = []
    for s in stands.itertuples(index=False):
        menu = (["no_management"] if s.unit_class == "riparian"
                else menu_for(policy, config, s.owner_class, s.forest_branch))
        for prescription in menu:
            for label, h in runs_for(s.PLT_CN, prescription):
                rows.append({"unit_id": s.unit_id, "PLT_CN": s.PLT_CN, "prescription": label,
                             "base_prescription": prescription, "run_hash": h})
    lib = pd.DataFrame(rows)
    if lib.duplicated(["unit_id", "prescription"]).any():
        raise AssertionError("a stand carries the same option twice")
    log.info("%d options over %d stands (%.1f per stand), %d distinct runs", len(lib),
             lib["unit_id"].nunique(), len(lib) / lib["unit_id"].nunique(),
             lib["run_hash"].nunique())
    return lib


# --------------------------------------------------------------------------------------
# Running FVS
# --------------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class RunPaths:
    """Where one library's runs read from and write to."""
    work: Path
    data_db: Path
    fvs_bin: Path = Path(os.environ.get("FVSSN_BIN", REPO / "fvs/bin/FVSsn"))

    @property
    def keyfiles(self) -> Path:
        return self.work / "keyfiles"

    @property
    def cycles(self) -> Path:
        return self.work / "fvs_cycles.csv.gz"

    @property
    def failures(self) -> Path:
        return self.work / "fvs_failures.csv"


def run_one(args: tuple[str, str, str, str]) -> tuple[str, str | None, list[dict]]:
    """One FVS run in an isolated directory, fail-closed.

    A run killed by a signal or ending on an unexpected stop code is a failure however many
    summary rows it wrote.
    """
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


def merge_harvest_year_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse each `(run, year)` to one row: removals from the cut, state from after it.

    A non-harvest year has a single `RmvCode = 0` row and passes through unchanged. A
    harvest year has `RmvCode = 1` (removals + pre-cut state) and `RmvCode = 2` (post-cut
    state, removals zeroed); the merged row takes the removal columns from the first and the
    state from the second, which is the only combination that describes the cycle.
    """
    key = ["PLT_CN", "prescription", "Year"]
    df = df.sort_values([*key, "RmvCode"], kind="stable")
    post = df.drop_duplicates(key, keep="last")
    removals = (df.sort_values([*key, "RMCuFt"], ascending=[True, True, True, False],
                               kind="stable")
                  .drop_duplicates(key, keep="first"))
    merged = post.set_index(key).copy()
    rem = removals.set_index(key)
    for col in REMOVAL_COLS:
        if col in merged.columns and col in rem.columns:
            merged[col] = rem[col]
    return merged.reset_index()


def run_fvs(keyfiles: dict[str, str], plt_of: dict[str, str], paths: RunPaths,
            workers: int) -> None:
    """Run every keyfile not yet in ``paths.cycles``; append its cycles and any failures."""
    have = (set(pd.read_csv(paths.cycles, usecols=["run_hash"])["run_hash"])
            if paths.cycles.exists() else set())
    todo = sorted(set(keyfiles) - have)
    log.info("FVS: %d distinct runs, %d cached, %d to run on %d workers",
             len(keyfiles), len(set(keyfiles) & have), len(todo), workers)
    if not todo:
        return
    paths.keyfiles.mkdir(parents=True, exist_ok=True)
    tasks = []
    for h in todo:
        path = paths.keyfiles / f"{h}.key"
        path.write_text(keyfiles[h])
        tasks.append((h, str(path), str(paths.data_db), str(paths.fvs_bin)))

    raw, failures = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one, t) for t in tasks]
        for n, fut in enumerate(as_completed(futures), start=1):
            h, err, rows = fut.result()
            if err:
                failures.append({"run_hash": h, "error": err})
            for r in rows:
                r["run_hash"] = h
            raw.extend(rows)
            if n % 2000 == 0:
                log.info("  %d/%d runs (%d failed)", n, len(tasks), len(failures))

    if raw:
        df = pd.DataFrame(raw)
        df["PLT_CN"] = df["run_hash"].map(plt_of)
        df["prescription"] = df["run_hash"]       # merge_harvest_year_rows keys on this column
        df = merge_harvest_year_rows(df)
        df = df[df["Year"] >= INV_YEAR].copy()
        df["cycle"] = ((df["Year"] - INV_YEAR) // CYCLE_YEARS).astype(int)
        complete = df.groupby("run_hash")["cycle"].apply(lambda c: set(c) == set(range(NUM_CYCLE + 1)))
        bad = complete.index[~complete]
        failures += [{"run_hash": h, "error": "incomplete cycle grid"} for h in bad]
        df = df[~df["run_hash"].isin(set(bad))]
        out = df[["run_hash", "PLT_CN", "cycle", "Year", "Age", "BA", "MCuFt", "RMCuFt"]].rename(
            columns={"Year": "calendar_year", "RMCuFt": "removed_merch_cuft_per_ac"})
        if paths.cycles.exists():
            out = pd.concat([pd.read_csv(paths.cycles, dtype={"PLT_CN": str}), out],
                            ignore_index=True)
        out.to_csv(paths.cycles, index=False)
    prev_fail = pd.read_csv(paths.failures) if paths.failures.exists() else pd.DataFrame()
    pd.concat([prev_fail, pd.DataFrame(failures, columns=["run_hash", "error"])]).to_csv(
        paths.failures, index=False)
    log.info("FVS done: %d runs added, %d failed", len(todo) - len(failures), len(failures))
