"""Stage 1 — give the trajectory library a *when*, not just a *what*.

`weekly-artifact/2026-08-31` ran the first annealed plan and, in doing so, measured
exactly why the plan could not track its targets. 47 of 80 (dimension × cycle) targets
were **proven unreachable at any selection**, cycle 10 (2072) had a ceiling of literally
zero, and the diagnosis was not the search:

> ARTEMIS's library currently offers **what** and almost no **when** — entry years are
> resolved deterministically from stand age, with no offset variants, so harvests pile
> into the cycles the age distribution happens to select and leave the others empty.

That is `notes/trajectory-library-and-annealing.md` §4's own gap. §1.2 names the fix and
its provenance in one line — Diaz et al.'s

> "Offsets" delaying first activity 5/10/15 yr, explicitly to give the optimizer choices

— and §4 restates it as "timing is deliberate, not padding". This script builds that grid.

**What changes.** Every *cutting* `(plot, prescription)` run is expanded into four
timing variants: the schedule as resolved today, and the same schedule delayed by 5, 10
and 15 years. Every year-valued parameter of the prescription shifts together, so a
delayed variant is the same silviculture started later, not a different silviculture:

    family_uneven_aged_selection      2032 · 2042 · 2052 · 2062
    family_uneven_aged_selection@+5y  2037 · 2047 · 2057 · 2067
    family_uneven_aged_selection@+10y 2042 · 2052 · 2062 · (2072, never executed)
    family_uneven_aged_selection@+15y 2047 · 2057 · 2067 · (2077, past the horizon)

Two rules keep the grid honest:

  * **A delayed entry must land where FVS will actually execute it — 2067 at the latest.**
    The projection is ten five-year cycles from 2022, so 2072 is the terminal *report*
    year and no cycle begins there: FVS accepts an activity scheduled in 2072 and never
    runs it. That is measured, not assumed (the same plot clearcut at 2062 and 2067
    removes volume; at 2072 it removes nothing and the run still ends normally), and it
    is why the cutoff is 2067 rather than the obvious 2072. Regeneration is dropped with
    the stand-replacing harvest that would have triggered it.
  * **A variant with no executable entry left is not published.** It *is*
    `no_management`, which is already in every non-riparian menu, and adding it again
    would inflate the library with a duplicate option and quietly bias the mix. Using the
    2072 cutoff published 55 delayed clearcuts that claimed an entry year and removed
    nothing; they are gone.

**This also corrects last week's diagnosis of the empty final cycle.** 2026-08-31 read
cycle 10's zero ceiling as "no prescription in the enumerated library schedules an entry
in 2072 at all". Twelve clearcuts do schedule one — it simply never fires. Cycle 10 is
empty because of the projection's shape, not the library's, and no timing grid can fill
it; extending the horizon to eleven cycles would, and that is a `config/projection.yaml`
change rather than a library one. The twelve are offset-0 rows, reproduced verbatim
because offset 0 is the control, and reported rather than silently repaired.

**Offset 0 keeps its original name** (`family_light_thin`, not `family_light_thin@+0y`).
That is deliberate: the greedy baseline resolves its defaults through
`regime_assignment.assign_prescription`, which returns base prescription ids, and it
keeps last week's run an exact control. The driver *asserts* the reproduction rather
than asserting it in prose: every offset-0 trajectory must come back identical to the
committed `weekly-artifact/2026-08-31/trajectory_index.csv`, or the run stops.

Riparian stands are untouched — their library is `{no_management}` by construction (§3
rule 2), there is nothing to delay, and the structural no-entry claim is unchanged.

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
  C. **Expand the offset grid and render every variant's keyfile** with
     `pipeline.s4_fvs.regime_templates` — the same committed renderer, driven through its
     explicit `thins=` / `regen=` entry point so out-of-horizon operations can be dropped
     before rendering. That path is verified byte-identical to the params path at offset
     0 by `tests/test_weekly_artifact_20260907_offsets.py`.
  D. **Run FVSsn** over the batch, in parallel isolated working directories, and collect
     `FVS_Summary2` into the two tables §5 specifies: `trajectory_cycles` (the full
     per-cycle state) and `trajectory_index` (the annealer's narrow working set).

Outputs land in `data/interim/` (gitignored) except the two library tables, which are the
artifact.

Usage:
    uv run python weekly-artifact/2026-09-07/make_offset_library.py [--workers N] [--limit N]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.ids import as_id_series  # noqa: E402
from pipeline.s4_fvs.regime_templates import (  # noqa: E402
    DEFAULT_INV_YEAR,
    build_regeneration,
    build_thins,
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

log = logging.getLogger("offset_library")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"
STAGE = DATA / "interim/stage"
WORK = DATA / "interim/fvs_batch"
KEYFILE_DIR = WORK / "keyfiles"

LIB_2026_08_17 = REPO / "weekly-artifact/2026-08-17/trajectory_library.csv"
SMZ_BY_UNIT = REPO / "weekly-artifact/2026-08-24/smz_by_unit.csv"
RIPARIAN_DECISION = REPO / "weekly-artifact/2026-08-24/riparian_decision_space.csv"
DELTA = REPO / "weekly-artifact/2026-08-24/library_riparian_delta.csv"
# Last week's library, at offset 0. Every offset-0 trajectory produced here must come
# back identical to it — the control the whole comparison rests on.
INDEX_2026_08_31 = REPO / "weekly-artifact/2026-08-31/trajectory_index.csv"

FIA_DB = STAGE / "FIA_5county_consolidated.db"
FVS_DATA_DB = WORK / "FVS_Data.db"
MANIFEST = WORK / "batch_manifest.json"
FVS_BIN = Path(os.environ.get("FVSSN_BIN", REPO / "fvs/bin/FVSsn"))

PROJECTION = REPO / "config/projection.yaml"


def projection_grid() -> tuple[int, int, int]:
    """`(inv_year, cycle_years, num_cycle)`, read from config rather than hard-coded.

    These were literals until review pointed out what that costs. `make_annealed_plan.py`
    takes its horizon from `projection.n_cycles`; this driver simulated a fixed ten
    cycles. Raise `n_cycles` to 11 — the first item this artifact's own README recommends
    — and the two would disagree: the batch would still stop at 2072, `Landscape` would
    zero-fill the eleventh cycle for every stand, and the envelope would report a target
    nothing can reach. That is a fabricated result rather than a crash, which is the worst
    shape for one to have.

    So both drivers read the same three numbers, all three go into the batch manifest, and
    `check_batch_matches` refuses a library simulated on a different grid than the one
    being planned over.
    """
    cfg = yaml.safe_load(PROJECTION.read_text())["projection"]
    inv_year = int(cfg["base_year"])
    if inv_year != DEFAULT_INV_YEAR:
        # The StandInit rows this driver writes are stamped with `inv_year`, and
        # `regime_templates` renders keyfiles against its own default. A drift between
        # them puts every trajectory on a different cycle grid than the keywords assume.
        raise AssertionError(
            f"config/projection.yaml base_year is {inv_year} but "
            f"regime_templates.DEFAULT_INV_YEAR is {DEFAULT_INV_YEAR}; the inventory "
            f"anchor and the keyfile renderer have drifted apart"
        )
    return inv_year, int(cfg["cycle_years"]), int(cfg["n_cycles"])


INV_YEAR, CYCLE_YEARS, NUM_CYCLE = projection_grid()   # 2022, 5, 10
LAST_CYCLE_YEAR = INV_YEAR + CYCLE_YEARS * NUM_CYCLE   # 2072; nothing later is simulated

# An entry scheduled in 2072 is accepted by FVS and never executed. The projection's ten
# cycles run 2022→2027 … 2067→2072, so 2072 is the terminal *report* year and no cycle
# begins there. Measured directly rather than assumed: the same plot clearcut at 2062 and
# 2067 removes volume in those years, and clearcut at 2072 removes nothing while the run
# still ends normally at 2072. Across the whole batch, no trajectory at any offset removes
# volume in cycle 10.
#
# So the last year an operation can actually happen is 2067, and a delayed variant whose
# only surviving entries are in 2072 is `no_management` wearing a clearcut's name. Those
# are dropped rather than published — see `resolve_variant`.
LAST_SIMULATED_ENTRY_YEAR = LAST_CYCLE_YEAR - CYCLE_YEARS   # 2067

# The timing grid, from Diaz et al. via §1.2 of the design note: "'Offsets' delaying first
# activity 5/10/15 yr, explicitly to give the optimizer choices". 0 is the schedule as
# resolved today and keeps the base prescription's own name, so last week's plan is an
# exact control and `assign_prescription`'s defaults still resolve.
OFFSET_YEARS = (0, 5, 10, 15)
# FVS ends normally through a Fortran STOP; both codes appear across this batch. Anything
# else -- and any negative code, i.e. death by signal -- is abnormal termination.
FVS_OK_RETURNCODES = frozenset({0, 10})
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
        ["unit_id", "tm_id", "PLT_CN", "county", "owner_class", "forest_branch", "acres",
         "stand_age", "OWN_CODE", "FORTYPCD"]
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
        ["unit_id", "tm_id", "PLT_CN", "county", "owner_class", "forest_branch", "acres",
         "stand_age"]
    ].copy()
    rip_stands["unit_class"] = "riparian"
    # The 2026-08-24 riparian frame carries no OWN_CODE/FORTYPCD, and does not need them:
    # `assign_prescription` applies the riparian override ahead of ownership and forest
    # type, so these stands resolve to `no_management` on SMZ_Pct alone.
    rip_stands["OWN_CODE"] = pd.NA
    rip_stands["FORTYPCD"] = pd.NA

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

    # AGENTS.md: never `.astype(str)` on an ID column whose dtype is not already
    # guaranteed exact. SQLite may hand back a numeric STAND_CN, and a PLT_CN that has
    # been through a float silently loses digits and then fails to join.
    stand["STAND_CN"] = as_id_series(stand["STAND_CN"], column="STAND_CN")
    tree["STAND_CN"] = as_id_series(tree["STAND_CN"], column="STAND_CN")
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
# Stage C1 — the timing-offset grid
# --------------------------------------------------------------------------------------
#
# The whole of this week's change lives in these four functions. Everything above and
# below is 2026-08-31's driver.

# The parameter names that denote a calendar year, per template, in
# `pipeline/s4_fvs/regime_templates.py`. A delay shifts *all* of them together — a
# `selection_harvest` whose `start_year` moved but whose `end_year` did not would be a
# shorter regime, not a later one, and the trajectory would no longer be the same
# silviculture. Anything not listed here (proportions, DBH limits, intervals) is a
# property of the treatment and must not move.
YEAR_PARAMS: dict[str, tuple[str, ...]] = {
    "clearcut": ("year",),
    "thin_from_below": ("year",),
    "thin_from_below_repeated": ("start_year", "end_year"),
    "selection_harvest": ("start_year", "end_year"),
    "plantation_rotation": ("thin_year", "clearcut_year"),
    "no_management": (),
}

# The single stand-replacing harvest each template regenerates after, if any. When a
# delay pushes that harvest past the horizon the harvest is not simulated, so neither is
# its regeneration — `_regen_after` would otherwise emit an Estab packet for a cut that
# never happens.
REGEN_TRIGGER: dict[str, str | None] = {
    "clearcut": "year",
    "plantation_rotation": "clearcut_year",
}


def variant_name(prescription: str, offset: int) -> str:
    """`family_light_thin` at 0; `family_light_thin@+5y` at 5.

    Offset 0 deliberately keeps the base name. `regime_assignment.assign_prescription`
    returns base prescription ids, so the greedy baseline resolves without a translation
    table, and every offset-0 trajectory stays directly comparable to 2026-08-31's.
    """
    return prescription if offset == 0 else f"{prescription}@+{offset}y"


def parse_params(pstr: object) -> dict:
    """`"max_dbh=8.0;proportion=0.35;year=2032"` -> a params dict, as 2026-08-31 read it."""
    kwargs: dict = {}
    if isinstance(pstr, str) and "=" in pstr:
        for item in filter(None, pstr.split(";")):
            k, v = item.split("=", 1)
            kwargs[k] = float(v) if "." in v else int(v)
    return kwargs


def format_params(params: dict) -> str:
    """The inverse of `parse_params`, in the sorted form the committed library uses."""
    return ";".join(f"{k}={params[k]}" for k in sorted(params))


def shift_params(template: str, params: dict, offset: int) -> dict:
    """Delay every year-valued parameter of a prescription by `offset` years."""
    if template not in YEAR_PARAMS:
        raise ValueError(f"no year-parameter mapping for template {template!r}")
    out = dict(params)
    for key in YEAR_PARAMS[template]:
        if key in out:
            out[key] = int(out[key]) + offset
    return out


def resolve_variant(template: str, params: dict, offset: int) -> tuple[list, list] | None:
    """The operations a delayed variant actually runs, or `None` if it runs nothing.

    **Offset 0 is taken verbatim.** It is the enumerated schedule 2026-08-31 ran, and it
    is this week's control; filtering it — even to remove an operation that provably never
    fires — would move the control and make the week-on-week difference partly a change of
    library rather than a change of decision space. The 12 offset-0 clearcuts scheduled in
    2072 are therefore reproduced exactly as they were, and reported as a defect for a
    later run to fix rather than silently repaired inside a comparison.

    **A delayed variant must land where FVS will execute it.** Two rules:

    * an entry after `LAST_SIMULATED_ENTRY_YEAR` (2067) is dropped. The obvious cutoff is
      2072, and it is wrong: FVS accepts an activity scheduled in the projection's
      terminal year and never runs it, because no cycle begins there. Using 2072 published
      55 delayed clearcuts that removed nothing while claiming an entry year — the exact
      shape of silent data error the fail-closed batch exists to prevent.
    * regeneration goes with the stand-replacing harvest that triggers it, so a clearcut
      pushed out of the horizon does not leave an Estab packet behind for a cut that
      never happens.

    Returns `None` when nothing is left. Such a variant *is* `no_management` — which every
    non-riparian menu already carries — so publishing it would add a duplicate option,
    inflate the reported library size, and bias the prescription mix toward doing nothing.
    """
    return resolve_schedule(template, shift_params(template, params, offset),
                            filter_horizon=bool(offset))


def resolve_schedule(template: str, params: dict, *,
                     filter_horizon: bool) -> tuple[list, list] | None:
    """The operations of an already-resolved parameter set, horizon rules applied or not.

    Split out from `resolve_variant` because the keyfile renderer works from parameters
    that have *already* been shifted — it must apply the same horizon rule as the
    expansion did, and it can no longer infer that from an offset argument of zero.
    """
    thins = build_thins(template, params)
    regen_ok = True
    if filter_horizon:
        thins = [t for t in thins if t.year <= LAST_SIMULATED_ENTRY_YEAR]
        if not thins:
            return None
        trigger = REGEN_TRIGGER.get(template)
        regen_ok = trigger is None or int(params[trigger]) <= LAST_SIMULATED_ENTRY_YEAR
    return thins, (build_regeneration(template, params) if regen_ok else [])


def expand_offset_grid(carved_lib: pd.DataFrame) -> pd.DataFrame:
    """The carved library, one row per (unit × prescription × timing offset).

    `no_management` has nothing to delay and is emitted once. Riparian stands carry only
    `no_management`, so §3 rule 2 — a riparian library of exactly `{no_management}`,
    enforced by the absence of an alternative — survives this expansion untouched, and
    the annealer re-checks it.
    """
    rows = []
    for row in carved_lib.itertuples(index=False):
        base = row.prescription
        template = row.template
        if not isinstance(base, str):
            continue
        params = parse_params(getattr(row, "params", None))
        record = row._asdict()
        for offset in OFFSET_YEARS:
            if template == "no_management":
                if offset != 0:
                    continue
                entries: list = []
            else:
                resolved = resolve_variant(template, params, offset)
                if resolved is None:
                    continue
                entries = [t.year for t in resolved[0]]
            # `params` carries the whole shifted parameter set, including a year the
            # horizon rule then drops — `entry_years` is the filtered truth. Keeping the
            # parameters unfiltered is what lets `render_batch` rebuild the identical
            # schedule from this row alone; trimming `end_year` instead would describe a
            # shorter regime than the one that ran.
            rows.append({**record,
                         "prescription": variant_name(base, offset),
                         "base_prescription": base,
                         "offset_years": offset,
                         "params": format_params(shift_params(template, params, offset)),
                         "entry_years": ";".join(str(y) for y in sorted(set(entries))),
                         "n_entries": len(set(entries))})
    out = pd.DataFrame(rows)
    per_stand = out.groupby("unit_id").size()
    log.info("Offset grid: %d library rows over %d stands (was %d rows); "
             "options per stand min %d, median %d, max %d",
             len(out), out["unit_id"].nunique(), len(carved_lib),
             per_stand.min(), int(per_stand.median()), per_stand.max())
    dropped = {o: int((out["offset_years"] == o).sum()) for o in OFFSET_YEARS}
    log.info("Rows by offset: %s", dropped)
    return out


# --------------------------------------------------------------------------------------
# Stage C2 — render the keyfiles
# --------------------------------------------------------------------------------------

def render_batch(carved_lib: pd.DataFrame, sdi: dict[str, dict[str, float]]) -> pd.DataFrame:
    """One keyfile per `(plot, prescription variant)` run in the expanded library.

    Operations are built here rather than inside `render_keyfile` so the horizon rules in
    `resolve_variant` can apply before rendering. `render_keyfile`'s explicit `thins=` /
    `regen=` path is byte-identical to its `params=` path for every template in this
    library — asserted in `tests/test_weekly_artifact_20260907_offsets.py`, and again end
    to end by the offset-0 reproduction check in `main`.
    """
    KEYFILE_DIR.mkdir(parents=True, exist_ok=True)
    params = (carved_lib.dropna(subset=["prescription"])
              .groupby(["PLT_CN", "prescription"], as_index=False)
              .agg(template=("template", "first"),
                   base_prescription=("base_prescription", "first"),
                   offset_years=("offset_years", "first"),
                   params=("params", "first")))

    records, no_sdi, reproduced = [], 0, 0
    for run in params.itertuples(index=False):
        kwargs = parse_params(run.params)
        plot_sdi = sdi.get(str(run.PLT_CN))
        if plot_sdi:
            kwargs["stand_sdi"] = plot_sdi
        else:
            no_sdi += 1
        # `kwargs` already carries the *shifted* years — the expansion applied the offset —
        # so only the horizon rule is applied here, and it must be applied on the same
        # terms the expansion used or the rendered keyfile would not match the library row.
        resolved = resolve_schedule(run.template, kwargs,
                                    filter_horizon=bool(run.offset_years))
        thins, regen = ([], []) if resolved is None else resolved
        stand_id = f"S{run.PLT_CN}"
        key = render_keyfile(stand_id=stand_id, stand_cn=str(run.PLT_CN),
                             regime=run.template, params=kwargs,
                             thins=thins, regen=regen, inv_year=INV_YEAR,
                             cycle_years=CYCLE_YEARS, num_cycle=NUM_CYCLE)
        if int(run.offset_years) == 0:
            # Gate 1 of 2. At offset 0 nothing is shifted and nothing falls outside the
            # horizon, so the explicit `thins=`/`regen=` path must render exactly what
            # 2026-08-31's `params=` path did. Checked here for every unshifted run
            # rather than argued in prose, because it is the hinge of the whole
            # comparison: if this path perturbs the schedules, "the offsets helped" is
            # measuring a rendering change.
            baseline = render_keyfile(stand_id=stand_id, stand_cn=str(run.PLT_CN),
                                      regime=run.template, params=kwargs,
                                      inv_year=INV_YEAR, cycle_years=CYCLE_YEARS,
                                      num_cycle=NUM_CYCLE)
            if key != baseline:
                raise AssertionError(
                    f"offset-0 keyfile for {run.PLT_CN} / {run.prescription} differs from "
                    f"the params-path render 2026-08-31 used; offset 0 is not a control"
                )
            reproduced += 1
        path = KEYFILE_DIR / f"{stand_id}__{run.prescription}.key"
        path.write_text(key)
        records.append({"PLT_CN": run.PLT_CN, "prescription": run.prescription,
                        "base_prescription": run.base_prescription,
                        "offset_years": int(run.offset_years),
                        "template": run.template, "keyfile": str(path),
                        "keyfile_sha256_16": hashlib.sha256(key.encode()).hexdigest()[:16]})
    runs = pd.DataFrame(records)
    log.info("Rendered %d keyfiles (%d distinct by content); %d runs had no live-tree SDI "
             "table and fall back to a single planted record",
             len(runs), runs["keyfile_sha256_16"].nunique(), no_sdi)
    log.info("Offset-0 keyfiles reproduce 2026-08-31's params-path render exactly "
             "(%d runs)", reproduced)
    # The enumeration's own committed hashes are *not* the baseline here, and it is worth
    # saying why. `weekly-artifact/2026-08-17/fvs_run_manifest.csv` was rendered before
    # 2026-08-31 corrected natural regeneration to follow the stand's own species SDI
    # shares, so 643 of its clearcut and plantation keyfiles legitimately no longer match.
    # The live comparison above is against the render 2026-08-31 actually ran, which is
    # the control this week's numbers are measured against.
    return runs


# --------------------------------------------------------------------------------------
# Stage D — run the batch
# --------------------------------------------------------------------------------------

def _run_one(args: tuple[str, str, str]) -> tuple[str, str, str | None, list[dict]]:
    """Run one keyfile in an isolated temp dir; return its FVS_Summary2 rows.

    **Fails closed on abnormal termination.** FVS writes each cycle's summary as it goes,
    so a run killed mid-horizon leaves a *partial* trajectory in `FVS_Out.db`; accepting it
    would silently hand the scheduler a stand that stops being harvested at the crash
    cycle. A run terminated by a signal (`returncode < 0` — e.g. -8, SIGFPE) is therefore
    rejected outright however many rows it managed to write.

    Note that a nonzero exit is *not* by itself a failure: FVS ends normally via a Fortran
    `STOP`, and both `STOP 0` and `STOP 10` are ordinary end-of-run codes seen across this
    batch. Only signals and unknown stop codes are treated as abnormal; completeness of the
    cycle grid is the real invariant and is checked separately in `validate_runs`.
    """
    plt_cn, prescription, keyfile = args
    tmp = Path(tempfile.mkdtemp(prefix="fvs_"))
    try:
        shutil.copy(keyfile, tmp / "run.key")
        os.symlink(FVS_DATA_DB, tmp / "FVS_Data.db")
        proc = subprocess.run([str(FVS_BIN), "--keywordfile=run.key"], cwd=tmp,
                              capture_output=True, text=True, timeout=900)
        out_db = tmp / "FVS_Out.db"
        # `FVS_OK_RETURNCODES` holds only non-negative codes, so death by signal — which
        # `subprocess` reports as a negative returncode — is already rejected by this one
        # membership test. The sign is consulted below only to phrase the error.
        if proc.returncode not in FVS_OK_RETURNCODES:
            tail = (proc.stdout or proc.stderr or "").strip().splitlines()
            detail = next((ln for ln in tail if "signal" in ln.lower() or "error" in ln.lower()),
                          tail[0] if tail else "")
            kind = f"killed by signal {-proc.returncode}" if proc.returncode < 0 \
                else f"unexpected stop code {proc.returncode}"
            return plt_cn, prescription, f"FVS {kind}: {detail[:160]}", []
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
            task = futures[fut]
            try:
                plt_cn, prescription, err, rows = fut.result()
            except Exception as exc:  # noqa: BLE001
                # A worker that dies outright (BrokenProcessPool, OOM kill) raises here
                # rather than inside `_run_one`. Record it as a failure like any other:
                # nothing is written until main() finishes, so letting this propagate
                # would discard every result already collected in a ~6-minute batch.
                plt_cn, prescription = task[0], task[1]
                err, rows = f"{type(exc).__name__}: {exc}", []
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


def validate_runs(cyc: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Drop any trajectory that does not carry the whole 2022→2072 cycle grid.

    The scheduler reads a trajectory as a dense vector over cycles 1..`n_cycles`, filling
    absent cycles with zero. That is only sound when every accepted run is *complete*: a
    trajectory truncated by a crash would otherwise enter the objective as a stand that
    grows on untouched after the crash cycle, which is a silent data error rather than a
    missing option. Incomplete runs are removed and reported, never zero-filled.
    """
    expected = set(range(0, NUM_CYCLE + 1))          # 2022 plus NUM_CYCLE five-year steps
    have = cyc.groupby(["PLT_CN", "prescription"])["cycle"].apply(lambda s: set(s.astype(int)))
    incomplete = have[have.apply(lambda s: s != expected)]
    if incomplete.empty:
        return cyc, pd.DataFrame(columns=["PLT_CN", "prescription", "error"])
    bad = pd.DataFrame({
        "PLT_CN": [k[0] for k in incomplete.index],
        "prescription": [k[1] for k in incomplete.index],
        "error": [f"incomplete cycle grid: {len(s)}/{len(expected)} cycles "
                  f"(missing {sorted(expected - s)})" for s in incomplete],
    })
    keys = set(incomplete.index)
    keep = ~cyc.set_index(["PLT_CN", "prescription"]).index.isin(keys)
    return cyc[keep].copy(), bad


REMOVAL_COLS = ("RTpa", "RTCuFt", "RMCuFt", "RBdFt")
STATE_COLS = ("Age", "BA", "Tpa", "QMD", "SDI", "TCuFt", "MCuFt", "BdFt")


def merge_harvest_year_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse each `(run, year)` to one row: removals from the cut, state from after it.

    A non-harvest year has a single `RmvCode = 0` row and passes through unchanged. A
    harvest year has `RmvCode = 1` (removals + pre-cut state) and `RmvCode = 2` (post-cut
    state, removals zeroed); the merged row takes `REMOVAL_COLS` from the first and
    `STATE_COLS` from the second, which is the only combination that describes the cycle.
    """
    key = ["PLT_CN", "prescription", "Year"]
    df = df.sort_values([*key, "RmvCode"], kind="stable")
    # Post-cut state where it exists, else the row's own state.
    post = df.drop_duplicates(key, keep="last")
    # The row carrying the removals: the largest RMCuFt in the year.
    removals = (df.sort_values([*key, "RMCuFt"], ascending=[True, True, True, False],
                               kind="stable")
                  .drop_duplicates(key, keep="first"))

    merged = post.set_index(key).copy()
    rem = removals.set_index(key)
    for col in REMOVAL_COLS:
        if col in merged.columns and col in rem.columns:
            merged[col] = rem[col]
    return merged.reset_index()


def build_library_tables(cycles: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`trajectory_cycles` and the narrow `trajectory_index`, per §5 of the design note.

    FVS_Summary2 emits **two rows** for a cycle in which a harvest occurs, and they carry
    different things:

      * `RmvCode = 1` — the removal quantities (`RMCuFt`, `RTpa`, …) alongside the state
        *before* the cut. Observed: BA 50.2, Tpa 4741, having removed 1894 trees.
      * `RmvCode = 2` — the state *after* the cut, with the removal columns back at zero.
        Same year: BA 31.2, Tpa 2847.

    So neither row alone describes the cycle. Taking whichever has the larger `RMCuFt`
    keeps the right removal and the wrong state — the trajectory would report a harvest
    cycle's standing volume as if the trees were still there, which matters for the
    `standing_volume` objective and for anyone reading the state table. The two are merged:
    removal columns from the `RmvCode = 1` row, endpoint state from the `RmvCode = 2` row.

    Volumes are per-acre; the scheduler multiplies by stand acres.
    """
    df = cycles.copy()
    # AGENTS.md again: exact-string IDs, never `.astype(str)`. These come back through
    # FVS_Out.db and a worker boundary, so the dtype is not guaranteed here either.
    df["PLT_CN"] = as_id_series(df["PLT_CN"], column="PLT_CN")
    df = merge_harvest_year_rows(df)
    df = df[df["Year"] >= INV_YEAR].copy()
    df["cycle"] = ((df["Year"] - INV_YEAR) // CYCLE_YEARS).astype(int)

    cyc = df[["PLT_CN", "prescription", "cycle", "Year", "Age", "BA", "Tpa", "QMD", "SDI",
              "TCuFt", "MCuFt", "BdFt", "RTpa", "RTCuFt", "RMCuFt", "RBdFt", "RmvCode"]].copy()
    cyc = cyc.rename(columns={"Year": "calendar_year", "RMCuFt": "removed_merch_cuft_per_ac"})

    # `cycle` 0 is the 2022 inventory state, not a simulated cycle: including it would
    # make `cycles` read 11 where the horizon is ten five-year steps.
    simulated = cyc[cyc["cycle"] >= 1]
    idx = (simulated.groupby(["PLT_CN", "prescription"], as_index=False)
              .agg(cycles=("cycle", "nunique"),
                   first_year=("calendar_year", "min"),
                   last_year=("calendar_year", "max"),
                   total_removed_merch_cuft_per_ac=("removed_merch_cuft_per_ac", "sum"),
                   harvest_cycles=("removed_merch_cuft_per_ac", lambda s: int((s > 0).sum())),
                   ending_ba=("BA", "last"),
                   ending_merch_cuft_per_ac=("MCuFt", "last")))
    return cyc, idx


def check_offset_zero_trajectories(idx: pd.DataFrame) -> None:
    """Offset-0 trajectories must reproduce 2026-08-31's published library exactly.

    The second reproduction gate. `check_offset_zero_keyfiles` proved the *inputs* were
    unchanged; this proves the *outputs* are, which is what makes last week's plan a
    control rather than a remembered number. Any drift here — a different FVS build, a
    different compiler flag, a different input database — would otherwise be absorbed
    silently into "the offsets helped".
    """
    want = pd.read_csv(INDEX_2026_08_31, dtype={"PLT_CN": str})
    got = idx[~idx["prescription"].str.contains("@+", regex=False)].copy()

    want_keys = set(zip(want["PLT_CN"], want["prescription"]))
    got_keys = set(zip(got["PLT_CN"], got["prescription"]))
    if want_keys != got_keys:
        raise AssertionError(
            f"offset-0 trajectory set differs from 2026-08-31: "
            f"{len(got_keys - want_keys)} new, {len(want_keys - got_keys)} missing "
            f"(e.g. {sorted(got_keys ^ want_keys)[:3]})"
        )

    cols = ["cycles", "first_year", "last_year", "total_removed_merch_cuft_per_ac",
            "harvest_cycles", "ending_ba", "ending_merch_cuft_per_ac"]
    merged = got.merge(want, on=["PLT_CN", "prescription"], suffixes=("_got", "_want"))
    worst = 0.0
    for col in cols:
        delta = (merged[f"{col}_got"] - merged[f"{col}_want"]).abs()
        scale = merged[f"{col}_want"].abs().clip(lower=1.0)
        worst = max(worst, float((delta / scale).max()))
    if worst > 1e-9:
        raise AssertionError(
            f"offset-0 trajectories differ from 2026-08-31 by up to {worst:.3g} relative; "
            f"the control has moved and this week's comparison would be meaningless"
        )
    log.info("Offset-0 trajectories reproduce 2026-08-31 exactly (%d runs, worst relative "
             "difference %.1e)", len(merged), worst)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--limit", type=int, default=None, help="smoke-test a subset of runs")
    ap.add_argument("--allow-excluded-runs", type=int, default=0,
                    help="publish even though this many (plot, prescription) trajectories "
                         "could not be simulated. The batch fails closed by default; the "
                         "number must be stated deliberately, and every exclusion is "
                         "written to fvs_failures.csv and carried into the plan.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not FVS_BIN.exists():
        raise SystemExit(f"FVSsn not found at {FVS_BIN}; set FVSSN_BIN or build it (see README)")

    # Invalidate the previous run's marker first. Everything downstream reads the library
    # tables under WORK, and a rebuild that fails partway must not leave a manifest
    # vouching for a *predecessor's* files — the annealer would then happily plan over a
    # library nobody meant to publish.
    WORK.mkdir(parents=True, exist_ok=True)
    MANIFEST.unlink(missing_ok=True)

    stands, base_lib = carved_landscape()
    carved_lib = expand_offset_grid(base_lib)

    plots = set(carved_lib["PLT_CN"].dropna().astype(str))
    sdi = build_input_db(plots)
    log.info("Species SDI tables built for %d/%d donor plots", len(sdi), len(plots))

    runs = render_batch(carved_lib, sdi)
    # A `--limit` run produces a partial library. It is useful for exercising the
    # plumbing and it must never look like a finished batch: the gates below and the
    # success marker are all suppressed under it.
    smoke = bool(args.limit)
    if smoke:
        runs = runs.head(args.limit)
        log.warning("SMOKE MODE: only %d runs", len(runs))

    log.info("Running %d FVS trajectories on %d workers", len(runs), args.workers)
    cycles, failures = run_batch(runs, args.workers)
    log.info("Collected %d FVS_Summary2 rows; %d runs failed outright",
             len(cycles), len(failures))

    cyc, idx = build_library_tables(cycles)
    cyc, incomplete = validate_runs(cyc)
    if len(incomplete):
        idx = idx.merge(incomplete[["PLT_CN", "prescription"]], on=["PLT_CN", "prescription"],
                        how="left", indicator=True)
        idx = idx[idx["_merge"] == "left_only"].drop(columns="_merge")
    excluded = pd.concat([failures, incomplete], ignore_index=True) if len(incomplete) \
        else failures

    # Fail closed. A partial library is not a smaller library: the scheduler would read a
    # missing trajectory as an option the stand does not have, and the plan would be
    # quietly built over a decision space nobody chose.
    # `fvs_failures.csv` is a published artifact file like any other, so a smoke run must
    # neither write nor delete it. A 20-run prefix that happens to exclude nothing is not
    # evidence that the batch excludes nothing — and the unlink below would silently
    # remove the real batch's committed record of what FVS could not simulate.
    if len(excluded):
        if not smoke:
            excluded.to_csv(OUT_DIR / "fvs_failures.csv", index=False)
        log.warning("%d trajectories could not be simulated; see fvs_failures.csv",
                    len(excluded))
        for row in excluded.itertuples(index=False):
            log.warning("  excluded %s / %s — %s", row.PLT_CN, row.prescription, row.error)
    # The flag is an *exact* acknowledgement, not a ceiling: it records that an operator
    # looked at this many exclusions and accepted them. If the count moves in either
    # direction the FVS outcomes have changed and that acknowledgement no longer describes
    # the run, so publication stops until a human looks again — including the case where
    # fewer runs failed than declared.
    if len(excluded) != args.allow_excluded_runs:
        raise SystemExit(
            f"{len(excluded)} trajectories failed or came back incomplete, but "
            f"--allow-excluded-runs declares {args.allow_excluded_runs}. Refusing to "
            f"publish: the exclusion set has changed since it was last acknowledged. "
            f"Inspect fvs_failures.csv, then re-run with --allow-excluded-runs "
            f"{len(excluded)} if these exclusions are acceptable."
        )
    if not len(excluded) and not smoke:
        (OUT_DIR / "fvs_failures.csv").unlink(missing_ok=True)

    if smoke:
        log.warning("SMOKE MODE: skipping the offset-0 reproduction gate")
    else:
        check_offset_zero_trajectories(idx)

    WORK.mkdir(parents=True, exist_ok=True)
    excluded.to_csv(WORK / "excluded_runs.csv", index=False)
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
    # Both published tables carry `fvs_run_id` explicitly, so a consumer can join the
    # plan to the library on the documented key without reconstructing it by hand. The
    # join is many-to-one: many stands share one donor plot's run, which is the dedup
    # cache §4 describes, not a duplicate.
    # Both published tables now split the run key into its two halves — the silviculture
    # and the delay — so a reader can ask "what does five years later cost this stand?"
    # without parsing the variant string.
    split = runs[["PLT_CN", "prescription", "base_prescription", "offset_years"]]
    idx_out = idx.merge(split, on=["PLT_CN", "prescription"], how="left")
    idx_out = idx_out.assign(fvs_run_id=idx_out["PLT_CN"] + "::" + idx_out["prescription"])
    lead = ["fvs_run_id", "PLT_CN", "prescription", "base_prescription", "offset_years"]
    idx_out = idx_out[lead + [c for c in idx_out.columns if c not in lead]]
    harvest = cyc[["PLT_CN", "prescription", "cycle", "calendar_year",
                   "removed_merch_cuft_per_ac"]].merge(split, on=["PLT_CN", "prescription"],
                                                       how="left")
    harvest.insert(0, "fvs_run_id", harvest["PLT_CN"] + "::" + harvest["prescription"])
    if smoke:
        log.warning("SMOKE MODE: not overwriting the published artifact tables in %s",
                    OUT_DIR)
    else:
        idx_out.to_csv(OUT_DIR / "trajectory_index.csv", index=False)
        harvest.to_csv(OUT_DIR / "trajectory_harvest_by_cycle.csv", index=False)

    # The grid itself, summarised: what each delay cost in runs and where it put the wood.
    # `last_harvest_year` is the column that shows the delay working — each step of the
    # grid should push it one five-year cycle later. Note `trajectory_index.last_year` is
    # *not* that number: it is the last reported year, 2072 for every run.
    cut = harvest[harvest["removed_merch_cuft_per_ac"] > 0]
    last_cut = cut.groupby("fvs_run_id")["calendar_year"].max()
    first_cut = cut.groupby("fvs_run_id")["calendar_year"].min()
    grid = (idx_out.assign(last_harvest_year=idx_out["fvs_run_id"].map(last_cut),
                           first_harvest_year=idx_out["fvs_run_id"].map(first_cut))
            .groupby("offset_years", as_index=False)
            .agg(runs=("fvs_run_id", "count"),
                 runs_that_cut=("harvest_cycles", lambda s: int((s > 0).sum())),
                 harvest_cycles_total=("harvest_cycles", "sum"),
                 mean_removed_cuft_per_ac=("total_removed_merch_cuft_per_ac", "mean"),
                 median_first_harvest_year=("first_harvest_year", "median"),
                 median_last_harvest_year=("last_harvest_year", "median")))
    final_cycle = (harvest[(harvest["cycle"] == NUM_CYCLE)
                           & (harvest["removed_merch_cuft_per_ac"] > 0)]
                   .groupby("offset_years")["fvs_run_id"].nunique())
    grid["runs_cutting_in_final_cycle"] = grid["offset_years"].map(final_cycle).fillna(0).astype(int)
    if not smoke:
        grid.to_csv(OUT_DIR / "offset_grid.csv", index=False)

    log.info("Artifact tables: trajectory_index.csv (%d), "
             "trajectory_harvest_by_cycle.csv (%d), offset_grid.csv (%d)",
             len(idx), len(harvest), len(grid))
    log.info("Median first/last harvest year by offset: %s",
             {int(o): (f, ln) for o, f, ln in zip(grid["offset_years"],
                                                  grid["median_first_harvest_year"],
                                                  grid["median_last_harvest_year"])})
    # Expected to be zero at every offset — see LAST_SIMULATED_ENTRY_YEAR. Logged rather
    # than asserted: it is a measurement of FVS's behaviour, and if it ever becomes
    # nonzero that is a finding, not a failure.
    log.info("Runs cutting in the final cycle (2072), by offset: %s",
             dict(zip(grid["offset_years"], grid["runs_cutting_in_final_cycle"])))

    # A smoke run must not vouch for anything. `--limit` runs a prefix of the batch and
    # everything downstream of it is a *partial* library — but the tables it writes have
    # the same names and the same shape as a real one, so a manifest written here would
    # be a valid-looking marker over a library nobody meant to publish, and
    # `check_batch_matches` would happily confirm the truncated row counts it recorded.
    # The marker was already unlinked at the top of `main`, so refusing to write it also
    # leaves any predecessor's marker invalidated: the next plan run stops with "the FVS
    # batch has not completed successfully", which is exactly right.
    if smoke:
        log.warning("SMOKE MODE: refusing to write %s. The library under %s is a %d-run "
                    "prefix, not a publishable batch; re-run without --limit before "
                    "planning over it.", MANIFEST.name, WORK, len(runs))
        return

    # The success marker, written last: validation has passed and every output is on disk.
    # `make_annealed_plan.py` refuses to run without it and checks the row counts match,
    # so a failed or half-finished rebuild cannot be planned over.
    MANIFEST.write_text(json.dumps({
        "completed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trajectory_cycles_rows": int(len(cyc)),
        "trajectory_index_rows": int(len(idx)),
        "carved_stands_rows": int(len(stands)),
        "carved_library_rows": int(len(carved_lib)),
        "excluded_runs": int(len(excluded)),
        # The projection grid this library was simulated on. `check_batch_matches`
        # compares all three against the active config and refuses a mismatch, so a
        # horizon change cannot be planned over a library built for the old one.
        "num_cycle": NUM_CYCLE,
        "cycle_years": CYCLE_YEARS,
        "inv_year": INV_YEAR,
    }, indent=2))
    log.info("Wrote %s — the batch is complete and safe to plan over", MANIFEST.name)


if __name__ == "__main__":
    main()
