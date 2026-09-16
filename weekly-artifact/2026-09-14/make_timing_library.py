"""Stage 1 — add the timing-offset grid to the trajectory library, and price it with FVS.

`weekly-artifact/2026-08-31` produced the first annealed plan and, with it, one finding that
dominated everything else: **47 of the 80 (dimension x cycle) volume targets were provably
unreachable from the enumerated library at any selection.** Its diagnosis named the cause,
and `notes/trajectory-library-and-annealing.md` §4 had named it first:

    Timing is deliberate, not padding. Diaz et al. offered delayed activity starts so the
    optimizer could choose both *what* and *when*.

ARTEMIS offered *what* and almost no *when*. Entry years are resolved deterministically
from stand age and from fixed offsets, so every stand on a given prescription cut on the
same comb of cycles: `family_light_thin` in cycle 2 and nowhere else, `public_thin_restore`
in 1/4/7, `public_selection_light` in 2/4/6/8, `family_uneven_aged_selection` in 3/6/9.
Cycle 5 got 42 cutting runs out of 3,781 and cycle 10 got none at all. Even flow is a
timing property, so no amount of searching could deliver it.

This script builds the increment that artifact asked for ("the single highest-value next
increment ... it costs FVS runs, not scheduler work") and prices it:

  A. **Rebuild the carved landscape** from the committed 2026-08-17 enumeration and
     2026-08-24 riparian carve, asserted against `library_riparian_delta.csv` exactly as
     2026-08-31 did. The landscape does not change this week; only the decision space does.
  B. **Expand every cutting prescription into timing variants** at offsets of 0, 5, 10 and
     15 years — `{prescription}@+{offset}`. The offsets are cycle multiples, so every entry
     still lands on an FVS cycle boundary, and each variant is the *same* prescription with
     the *same* parameters, started later. Entries pushed past the last schedulable year are
     dropped, which is `config/management_regimes.yaml`'s own rule; a variant that loses
     every entry collapses to `no_management` and is not published as an option, because it
     would duplicate the `no_management` the menu already carries.
  C. **Run FVS over eleven cycles rather than ten** — see `TERMINAL CYCLE` below. This is
     the other half of the fix, and without it the offsets cannot reach cycle 10 at all.
  D. **Check the expansion against last week's library, run for run.** Every offset-0 run
     must reproduce the total removed volume `2026-08-31/trajectory_index.csv` published for
     that same `(plot, prescription)`. That is the reproducibility claim this artifact rests
     on: the base library is untouched and the new options are additions to it.

TERMINAL CYCLE — why eleven cycles, and why it changes nothing inside ten
------------------------------------------------------------------------
2026-08-31 reported cycle 10 (2072) as empty and read that as a library gap. It is not: it
is a projection-horizon artifact, and `probe_terminal_cycle.py` in this directory is the FVS
run that shows it. A `ThinDBH` scheduled in 2072 under a ten-cycle projection produces **no
removal at all** — 2072 is the final summary year, and the activity never executes. The same
keyfile with an eleventh cycle executes it and reports the removal in the 2072 row, exactly
as a 2067 entry is reported in the 2067 row. Adding the eleventh cycle changes no row inside
the horizon: the probe checks the 2022-2072 rows of a ten-cycle and an eleven-cycle run are
identical, and `--check-base-library` then checks the same thing across the whole batch
against last week's published volumes.

So the 50-year harvest horizon is unchanged and the objective still scores cycles 1..10
(2027..2072). The eleventh cycle exists only so that an activity in the horizon's final year
is carried out inside the projection instead of falling off its end. `trajectory_index.csv`
publishes cycle 11 alongside the rest and marks it `in_horizon = False`.

Usage:
    uv run python weekly-artifact/2026-09-14/make_timing_library.py [--workers N]
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
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

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.ids import as_id_series  # noqa: E402
from pipeline.s4_fvs.regime_templates import (  # noqa: E402
    DEFAULT_INV_YEAR,
    Regeneration,
    ThinDBH,
    build_regeneration,
    build_thins,
    render_keyfile,
)

log = logging.getLogger("timing_library")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"
STAGE = DATA / "interim/stage"
WORK = DATA / "interim/timing_library"
KEYFILE_DIR = WORK / "keyfiles"

LIB_2026_08_17 = REPO / "weekly-artifact/2026-08-17/trajectory_library.csv"
SMZ_BY_UNIT = REPO / "weekly-artifact/2026-08-24/smz_by_unit.csv"
RIPARIAN_DECISION = REPO / "weekly-artifact/2026-08-24/riparian_decision_space.csv"
DELTA = REPO / "weekly-artifact/2026-08-24/library_riparian_delta.csv"
INDEX_2026_08_31 = REPO / "weekly-artifact/2026-08-31/trajectory_index.csv"

FIA_DB = STAGE / "FIA_5county_consolidated.db"
FVS_DATA_DB = WORK / "FVS_Data.db"
MANIFEST = WORK / "batch_manifest.json"
FVS_BIN = Path(os.environ.get("FVSSN_BIN", REPO / "fvs/bin/FVSsn"))

INV_YEAR = DEFAULT_INV_YEAR          # 2022
CYCLE_YEARS = 5
HORIZON_YEARS = 50
# The last year an entry may be scheduled in: config/management_regimes.yaml drops anything
# past `inventory_year + horizon_years`, and the objective scores cycles 1..10.
LAST_ENTRY_YEAR = INV_YEAR + HORIZON_YEARS       # 2072
N_OBJECTIVE_CYCLES = HORIZON_YEARS // CYCLE_YEARS  # 10
# One cycle past the horizon, so that an entry in 2072 is executed inside the projection
# rather than falling off its end. See TERMINAL CYCLE in the module docstring.
NUM_CYCLE = N_OBJECTIVE_CYCLES + 1                 # 11 -> 2022..2077

# The timing grid. Multiples of `CYCLE_YEARS`, so every shifted entry still lands on a cycle
# boundary and no operation has to be snapped a second time. 15 years is three cycles, which
# is the longest interval any prescription in the library uses, so the four offsets together
# cover every phase of every comb: a 15-year comb at cycles {3,6,9} becomes {4,7,10} and
# {5,8} and {6,9}, and a 10-year comb at {2,4,6,8} becomes {3,5,7,9} and {4,6,8,10}.
#
# Library cost, which §4 requires any addition to state: of the 3,782 runs in the 2026-08-24
# carved library, 3,106 carry at least one entry and 676 are `no_management`. The three added
# offsets contribute 3,100 + 3,088 + 3,065 rather than 3 x 3,106, because 65 variants lose
# every entry to the horizon rule below — 13,035 runs in total, about 3.5x last week, and
# ~22 minutes on four cores. Per stand the menu goes from 1-4 trajectories to 1-13, against
# §4's target of 6-12.
OFFSETS = (0, 5, 10, 15)
OFFSET_SEP = "@+"

# FVS ends normally through a Fortran STOP; both codes appear across this batch. Anything
# else -- and any negative code, i.e. death by signal -- is abnormal termination.
FVS_OK_RETURNCODES = frozenset({0, 10})
ID_COLS = {"PLT_CN": str, "unit_id": str, "tm_id": str}

# The schema every failure frame carries, whether or not anything failed.
FAILURE_COLUMNS = ["PLT_CN", "prescription", "error"]


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
            found[name] = ast.literal_eval(node.value.func.value).split()
    if set(found) != {"_SN_JSP", "_SN_FIAJSP"}:
        raise AssertionError(f"species crosswalk not found in {path}")
    if len(found["_SN_JSP"]) != len(found["_SN_FIAJSP"]):
        raise AssertionError("SN/FIA species tables are not positionally aligned")
    return dict(zip(found["_SN_FIAJSP"], found["_SN_JSP"]))


def stand_sdi_tables(trees: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Per-plot species SDI shares (SN alpha codes) for natural regeneration.

    The rule and the SDI form are `04_fvs_run.stand_sdi_tables`: natural regeneration is
    apportioned across the stand's own species by SDI share (Diaz et al. 2015), rather than
    falling back to a single loblolly record — which would regenerate every bottomland
    hardwood clearcut as pine plantation. Unchanged from 2026-08-31, whose volumes this
    artifact reproduces at offset 0.
    """
    fia_to_sn = _leto_species_crosswalk()
    t = trees.copy()
    # AGENTS.md: an ID column is normalised with `as_id_series`, never `str()`/`.astype(str)`.
    # This lookup is keyed by PLT_CN and read by `render_batch` with an exact string, so a
    # STAND_CN that arrived numeric would key the table as "4.4894e+14" and miss every
    # lookup — silently sending every natural regeneration back to the single-species
    # fallback instead of the stand's own composition. The batch's own frame is already
    # normalised upstream; this makes the helper safe for any caller's.
    t["STAND_CN"] = as_id_series(t["STAND_CN"], column="STAND_CN")
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
            out[stand_cn] = table
    return out


# --------------------------------------------------------------------------------------
# Stage A — rebuild the carved landscape from the committed artifacts
# --------------------------------------------------------------------------------------

def carved_landscape() -> tuple[pd.DataFrame, pd.DataFrame]:
    """The 2026-08-24 carved landscape: upland remainders + riparian stands.

    Reconstructed from committed artifacts rather than re-derived, and asserted against
    `library_riparian_delta.csv`. Identical to `weekly-artifact/2026-08-31/make_fvs_batch.py`
    — the landscape is deliberately unchanged this week so that the only difference between
    the two plans is the decision space.
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
    upland = units[units["acres"] > 1e-9].drop(columns=["smz_acres"]).copy()
    upland["unit_class"] = "managed"

    rip = pd.read_csv(RIPARIAN_DECISION, dtype=ID_COLS)
    rip_stands = rip.drop_duplicates("unit_id")[
        ["unit_id", "tm_id", "PLT_CN", "county", "owner_class", "forest_branch", "acres",
         "stand_age"]
    ].copy()
    rip_stands["unit_class"] = "riparian"
    rip_stands["OWN_CODE"] = pd.NA
    rip_stands["FORTYPCD"] = pd.NA

    stands = pd.concat([upland, rip_stands], ignore_index=True)

    upland_lib = lib[lib["unit_id"].isin(set(upland["unit_id"]))].copy()
    upland_lib = upland_lib.drop(columns=["acres"]).merge(
        upland[["unit_id", "acres"]], on="unit_id", how="left"
    )
    upland_lib["unit_class"] = "managed"
    rip_lib = rip.copy()
    rip_lib["unit_class"] = "riparian"
    keep = ["unit_id", "tm_id", "PLT_CN", "county", "owner_class", "forest_branch", "acres",
            "stand_age", "prescription", "template", "unit_class"]
    for frame in (upland_lib, rip_lib):
        for col in keep + ["params"]:
            if col not in frame.columns:
                frame[col] = pd.NA
    carved_lib = pd.concat([upland_lib[keep + ["params"]], rip_lib[keep + ["params"]]],
                           ignore_index=True)

    # --- reproducibility check against the committed 2026-08-24 delta ------------------
    delta = pd.read_csv(DELTA).set_index("scenario").loc["riparian_stands"]
    checks = {
        "pre-carve units": (pre_units, 5240),
        "pre-carve acres": (round(pre_acres, 1), round(925097.8313136607, 1)),
        "carved stands": (len(stands), int(delta["units"])),
        "carved library rows": (len(carved_lib), int(delta["library_rows"])),
        "riparian stands": (int((stands.unit_class == "riparian").sum()),
                            int(delta["riparian_units"])),
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
# Stage B — the timing-offset grid
# --------------------------------------------------------------------------------------

def parse_params(raw: object) -> dict:
    """`"a=1;b=2.5"` -> `{"a": 1, "b": 2.5}`. The form 2026-08-17 wrote its params in."""
    out: dict[str, float | int] = {}
    if not isinstance(raw, str):
        return out
    for item in filter(None, raw.split(";")):
        k, v = item.split("=", 1)
        out[k] = float(v) if "." in v else int(v)
    return out


def variant_id(prescription: str, offset: int) -> str:
    return f"{prescription}{OFFSET_SEP}{offset}"


def base_of(variant: str) -> tuple[str, int]:
    """Inverse of `variant_id`. `no_management` carries no offset and returns 0."""
    if OFFSET_SEP not in variant:
        return variant, 0
    base, off = variant.rsplit(OFFSET_SEP, 1)
    return base, int(off)


@dataclasses.dataclass(frozen=True)
class Variant:
    """One timing variant of one prescription, as FVS operations ready to render.

    A variant with `collapsed = True` has no operations left inside the horizon and is not
    published as an option. It is still returned rather than replaced by `None`, because the
    accounting it carries — how many entries the horizon rule dropped — is exactly what a
    reader of `library_expansion.csv` needs from the collapsed rows, and a null cannot
    carry it.
    """
    prescription: str
    base: str
    template: str
    offset: int
    thins: tuple[ThinDBH, ...]
    regen: tuple[Regeneration, ...]
    entry_years: tuple[int, ...]
    dropped_entries: int
    collapsed: bool


def _shift_operations(
    thins: list[ThinDBH], regen: list[Regeneration], offset: int
) -> tuple[list[ThinDBH], list[Regeneration]]:
    """Move every operation `offset` years and drop what leaves the horizon.

    Split out from `shift_variant` because the case that matters most cannot be reached
    through any prescription in the current library: a template with **two** stand-replacing
    entries whose later one — the parent of a regeneration record — falls outside the
    horizon while an earlier, unrelated entry survives. Taking the parent to be "the nearest
    preceding survivor" would hand that record to the wrong entry and re-initialize the
    stand from a planting list for a harvest that never happened. Resolving the parent on
    the unshifted schedule, where `_regen_after` placed it exactly `delay_years` after its
    own harvest, is correct whether or not a template ever grows a second one.
    """
    survivors = [dataclasses.replace(t, year=t.year + offset) for t in thins
                 if t.year + offset <= LAST_ENTRY_YEAR]
    if not survivors:
        return [], []           # nothing left to regenerate from

    shifted_regen = []
    for r in regen:
        parent = max((t.year for t in thins if t.year <= r.year), default=None)
        if parent is None or parent + offset > LAST_ENTRY_YEAR:
            continue            # the entry that created this record did not survive
        year = r.year + offset
        if year > LAST_ENTRY_YEAR + CYCLE_YEARS:
            # Regeneration for a surviving final-year harvest may legitimately fall in the
            # eleventh cycle; anything beyond that has no projection left to grow in.
            continue
        shifted_regen.append(dataclasses.replace(r, year=year))
    return survivors, shifted_regen


def shift_variant(template: str, params: dict, base: str, offset: int,
                  *, with_regen: bool = True) -> Variant:
    """The prescription started `offset` years later, or `None` if nothing survives.

    The operations come from the repository's own builders — `build_thins` and
    `build_regeneration` on the resolved parameters 2026-08-17 wrote — and only their years
    move. Nothing else about the prescription changes: same proportions, same DBH windows,
    same regeneration rule and delay. At `offset = 0` the operations are therefore exactly
    the ones 2026-08-31 ran, which `check_base_library` verifies against its volumes.

    Two rules decide what survives, and both are `config/management_regimes.yaml`'s:

    * An entry past `LAST_ENTRY_YEAR` (2072) is **dropped** — "the keyfile only runs ten
      5-year cycles, so a later entry is noise in the keyfile and a lie in the schedule".
      A delayed plantation rotation whose final harvest lands in 2082 is a thin inside this
      horizon and nothing more, which is what the trajectory then reports.
    * A variant that loses **every** entry "resolves to `no_management` for that stand".
      It comes back marked `collapsed` and is not published as a separate option: the menu
      already carries `no_management`, and adding a second copy of it would inflate the
      library with an option identical to one already there. It still carries its
      `dropped_entries` count, which is the whole reason it collapsed and which
      `library_expansion.csv` reports.

    Regeneration follows its harvest, and the pairing is resolved on the **unshifted**
    schedule. A `Regeneration` record exists because a particular stand-replacing entry
    created it — `_regen_after` places it `delay_years` after that entry — so its parent is
    identified among the original operations, where the delta is exactly that delay, and the
    record survives only if *that* entry does. Re-deriving the parent after the shift, as
    "the nearest preceding survivor", would agree today (every template carrying
    regeneration has a single stand-replacing entry) but would silently re-attach a record
    to the wrong entry the moment a template had two and the later one was dropped.

    ``with_regen=False`` skips building the regeneration records, for the caller that only
    needs the entry years. It is not an optimisation: natural regeneration follows the
    stand's own species composition, so building it without a `stand_sdi` table in `params`
    warns that the Diaz rule was skipped — a real warning that would be noise here, and
    misleading, since those records would be discarded unused.
    """
    thins = build_thins(template, params)
    regen = build_regeneration(template, params) if with_regen else []
    survivors, shifted_regen = _shift_operations(thins, regen, offset)
    dropped = len(thins) - len(survivors)

    return Variant(prescription=variant_id(base, offset), base=base, template=template,
                   offset=offset, thins=tuple(survivors), regen=tuple(shifted_regen),
                   entry_years=tuple(sorted(t.year for t in survivors)),
                   dropped_entries=dropped, collapsed=not survivors)


def expand_library(carved_lib: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The carved library, with every cutting prescription expanded over `OFFSETS`.

    Returns the expanded per-stand library and the per-`(base, offset)` accounting. A
    prescription with no entries at all (`no_management`) is passed through untouched and
    unoffset: shifting "no entry" produces no new trajectory, and the riparian libraries
    must stay exactly `{no_management}` for §3 rule 2 to hold structurally.
    """
    rows, accounting = [], []
    # One (prescription, template, params) resolves to one set of operations, and the 22,317
    # carved library rows carry only 29 of them — 28 cutting combinations plus
    # `no_management`. Resolve each distinct combination once.
    specs = (carved_lib.assign(params=carved_lib["params"].fillna(""))
             .drop_duplicates(["prescription", "template", "params"]))
    resolved: dict[tuple[str, str, str], list[Variant]] = {}
    for spec in specs.itertuples(index=False):
        params = parse_params(spec.params)
        key = (spec.prescription, spec.template, spec.params)
        if not build_thins(spec.template, params):
            resolved[key] = []          # no_management: no entries to move
            continue
        variants = []
        for offset in OFFSETS:
            # Entry years only here; `render_batch` rebuilds each variant with the plot's
            # SDI table so natural regeneration follows the stand's own composition.
            v = shift_variant(spec.template, params, spec.prescription, offset,
                              with_regen=False)
            if not v.collapsed:
                variants.append(v)
            accounting.append({
                "base_prescription": spec.prescription, "template": spec.template,
                "offset_years": offset, "resolved_params": spec.params,
                "entries_kept": len(v.entry_years),
                "entries_dropped": v.dropped_entries,
                "collapsed_to_no_management": v.collapsed,
                "entry_years": ";".join(str(y) for y in v.entry_years),
            })
        resolved[key] = variants

    for row in carved_lib.itertuples(index=False):
        params_raw = "" if not isinstance(row.params, str) else row.params
        variants = resolved[(row.prescription, row.template, params_raw)]
        if not variants:
            rows.append({**row._asdict(), "base_prescription": row.prescription,
                         "offset_years": 0, "entry_years": "",
                         "params": params_raw or pd.NA})
            continue
        for v in variants:
            rows.append({**row._asdict(), "prescription": v.prescription,
                         "base_prescription": v.base, "offset_years": v.offset,
                         "entry_years": ";".join(str(y) for y in v.entry_years),
                         "params": params_raw or pd.NA})

    expanded = pd.DataFrame(rows)
    acct = pd.DataFrame(accounting)
    # The variant set must be resolvable back to its parts, or the plan cannot be read.
    if not (expanded["prescription"].map(lambda p: base_of(p)[0])
            == expanded["base_prescription"]).all():
        raise AssertionError("a variant id does not decode back to its base prescription")
    log.info("Library expanded: %d rows -> %d rows; options per stand %d-%d (was %d-%d)",
             len(carved_lib), len(expanded),
             expanded.groupby("unit_id").size().min(), expanded.groupby("unit_id").size().max(),
             carved_lib.groupby("unit_id").size().min(),
             carved_lib.groupby("unit_id").size().max())
    collapsed = int(acct["collapsed_to_no_management"].sum()) if len(acct) else 0
    log.info("%d (prescription, offset) combinations collapse to no_management and are not "
             "published as options", collapsed)
    return expanded, acct


# --------------------------------------------------------------------------------------
# Stage C — the FVS input database
# --------------------------------------------------------------------------------------

def build_input_db(plots: set[str]) -> tuple[dict[str, dict[str, float]], str]:
    """StandInit/TreeInit for the donor plots, anchored to INV_YEAR 2022.

    Unchanged from 2026-08-31: each FVS run is a single donor plot, so
    `build_fvs_inputs.build_stand_init`'s `INV_YEAR = 2022` rule (the TreeMap 2022
    imputation anchor) degenerates to taking the plot's own row and setting its inventory
    year. The raw FIA rows carry inventory years of 2009 and earlier, which would put every
    trajectory on the wrong cycle grid.

    Returns the per-plot SDI tables and a **content fingerprint of the tree lists actually
    written** — the stand and tree rows, sorted and serialised, not the SQLite file, whose
    bytes need not be stable between two builds of identical content. The fingerprint is
    what lets `--reuse-raw` tell "the same library" from "the same library over different
    tree lists": a changed `FIA_5county_consolidated.db` alters no keyfile, so nothing else
    in the run set would notice it.
    """
    WORK.mkdir(parents=True, exist_ok=True)
    if FVS_DATA_DB.exists():
        FVS_DATA_DB.unlink()

    src = sqlite3.connect(FIA_DB)
    stand = pd.read_sql("SELECT * FROM FVS_STANDINIT_PLOT", src)
    tree = pd.read_sql("SELECT * FROM FVS_TREEINIT_PLOT", src)
    src.close()

    # AGENTS.md: never `.astype(str)` on an ID column whose dtype is not already guaranteed
    # exact. SQLite may hand back a numeric STAND_CN, and a PLT_CN that has been through a
    # float silently loses digits and then fails to join.
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

    digest = hashlib.sha256()
    for frame, keys in ((stand, ["STAND_CN"]), (tree, ["STAND_CN", "TREE"])):
        by = [k for k in keys if k in frame.columns]
        ordered = frame.sort_values(by).reindex(sorted(frame.columns), axis=1)
        digest.update(ordered.to_csv(index=False).encode())
    fingerprint = digest.hexdigest()[:16]

    log.info("FVS input DB: %d stands, %d trees, INV_YEAR=%d (content %s)",
             len(stand), len(tree), INV_YEAR, fingerprint)
    return stand_sdi_tables(tree), fingerprint


# --------------------------------------------------------------------------------------
# Stage D — render the keyfiles
# --------------------------------------------------------------------------------------

def render_batch(expanded: pd.DataFrame, sdi: dict[str, dict[str, float]]) -> pd.DataFrame:
    """One keyfile per `(plot, prescription variant)` run in the expanded library.

    The operations are rebuilt here rather than carried through the frame, because a
    `ThinDBH` is not a CSV cell: `shift_variant` returns the objects and they are handed
    straight to `render_keyfile`'s `thins`/`regen` override. That override is the documented
    way for a caller that assembled operations elsewhere to reuse the verified keyfile
    scaffolding, and it is what keeps the offsets from touching the renderer at all.
    """
    KEYFILE_DIR.mkdir(parents=True, exist_ok=True)
    runs = (expanded.dropna(subset=["prescription"])
            .assign(params=expanded["params"].fillna(""))
            .groupby(["PLT_CN", "prescription"], as_index=False)
            .agg(template=("template", "first"), params=("params", "first"),
                 base_prescription=("base_prescription", "first"),
                 offset_years=("offset_years", "first")))

    records, no_sdi = [], 0
    for run in runs.itertuples(index=False):
        params = parse_params(run.params)
        plot_sdi = sdi.get(str(run.PLT_CN))
        if plot_sdi:
            params["stand_sdi"] = plot_sdi
        else:
            no_sdi += 1
        stand_id = f"S{run.PLT_CN}"
        if build_thins(run.template, params):
            v = shift_variant(run.template, params, run.base_prescription, run.offset_years)
            if v.collapsed or v.prescription != run.prescription:
                raise AssertionError(
                    f"variant {run.prescription} no longer resolves from its own template "
                    f"and params; the library and the renderer disagree"
                )
            thins, regen, years = list(v.thins), list(v.regen), v.entry_years
        else:
            thins, regen, years = [], [], ()
        key = render_keyfile(stand_id=stand_id, stand_cn=str(run.PLT_CN),
                             regime=run.template, params=params, thins=thins, regen=regen,
                             inv_year=INV_YEAR, cycle_years=CYCLE_YEARS, num_cycle=NUM_CYCLE)
        path = KEYFILE_DIR / f"{stand_id}__{run.prescription}.key"
        path.write_text(key)
        records.append({"PLT_CN": run.PLT_CN, "prescription": run.prescription,
                        "base_prescription": run.base_prescription,
                        "offset_years": run.offset_years, "template": run.template,
                        "entry_years": ";".join(str(y) for y in years),
                        "keyfile": str(path),
                        "keyfile_sha256_16": hashlib.sha256(key.encode()).hexdigest()[:16]})
    out = pd.DataFrame(records)
    log.info("Rendered %d keyfiles (%d distinct by content); %d runs had no live-tree SDI "
             "table and fall back to a single planted record",
             len(out), out["keyfile_sha256_16"].nunique(), no_sdi)
    return out


# --------------------------------------------------------------------------------------
# Stage E — run the batch
# --------------------------------------------------------------------------------------

def _run_one(args: tuple[str, str, str]) -> tuple[str, str, str | None, list[dict]]:
    """Run one keyfile in an isolated temp dir; return its FVS_Summary2 rows.

    **Fails closed on abnormal termination**, exactly as 2026-08-31: FVS writes each cycle's
    summary as it goes, so a run killed mid-horizon leaves a *partial* trajectory, and
    accepting it would hand the scheduler a stand that quietly stops being harvested at the
    crash cycle. A run terminated by a signal (`returncode < 0`) is rejected however many
    rows it already wrote. A nonzero exit is not by itself a failure — FVS ends normally via
    a Fortran `STOP`, and both `STOP 0` and `STOP 10` occur across this batch.
    """
    plt_cn, prescription, keyfile = args
    tmp = Path(tempfile.mkdtemp(prefix="fvs_"))
    try:
        shutil.copy(keyfile, tmp / "run.key")
        os.symlink(FVS_DATA_DB, tmp / "FVS_Data.db")
        proc = subprocess.run([str(FVS_BIN), "--keywordfile=run.key"], cwd=tmp,
                              capture_output=True, text=True, timeout=900)
        out_db = tmp / "FVS_Out.db"
        if proc.returncode < 0 or proc.returncode not in FVS_OK_RETURNCODES:
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
                # nothing is written until main() finishes, so letting this propagate would
                # discard every result already collected.
                plt_cn, prescription = task[0], task[1]
                err, rows = f"{type(exc).__name__}: {exc}", []
            done += 1
            if err:
                failures.append({"PLT_CN": plt_cn, "prescription": prescription, "error": err})
            for row in rows:
                row["PLT_CN"] = plt_cn
                row["prescription"] = prescription
                cycles.append(row)
            if done % 1000 == 0:
                log.info("  %d/%d runs complete (%d failed)", done, len(tasks), len(failures))
    # Always the declared columns, even with nothing to report. A zero-column frame writes a
    # CSV that is a single newline — no header — and `pd.read_csv` then raises EmptyDataError
    # on it, so a *clean* batch would write a failure sidecar that the next `--reuse-raw` run
    # could not read. The success case must not be the one that breaks the cache.
    return pd.DataFrame(cycles), pd.DataFrame(failures, columns=FAILURE_COLUMNS)


def read_failures(path: Path) -> pd.DataFrame:
    """Read a failure sidecar, tolerating one written with no header.

    The sidecar's only job is to be readable: it is the sole record that a wholly-failed run
    existed, since such a run contributes no summary rows. A header-less file (what an
    older, zero-column frame wrote on a clean batch) means "nothing failed" — not "the cache
    is unreadable" — so it resolves to an empty frame with the declared schema rather than
    aborting a run that has a perfectly good cache beside it.
    """
    try:
        return pd.read_csv(path, dtype={"PLT_CN": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=FAILURE_COLUMNS)


def validate_runs(cyc: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Drop any trajectory that does not carry the whole 2022->2077 cycle grid.

    The scheduler reads a trajectory as a dense vector over cycles 1..10, filling absent
    cycles with zero, which is only sound when every accepted run is *complete*: a
    trajectory truncated by a crash would enter the objective as a stand that grows on
    untouched after the crash cycle — a silent data error rather than a missing option.
    Incomplete runs are removed and reported, never zero-filled. The grid checked here is
    the full eleven cycles, including the carrier cycle, because a run that died in it may
    have died *because of* an entry in 2072 and its 2072 removal cannot be trusted.
    """
    expected = set(range(0, NUM_CYCLE + 1))
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


def merge_harvest_year_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse each `(run, year)` to one row: removals from the cut, state from after it.

    A non-harvest year has a single `RmvCode = 0` row and passes through unchanged. A
    harvest year has `RmvCode = 1` (removals + pre-cut state) and `RmvCode = 2` (post-cut
    state, removals zeroed); the merged row takes the removal columns from the first and the
    state from the second, which is the only combination that describes the cycle.

    2026-08-31 fixed this and noted it changed none of its published numbers, because
    nothing in its library harvested in the final cycle. **This week it matters**: a 2072
    entry is exactly a harvest in the last scored cycle, and `ending_merch_cuft_per_ac` —
    what the `standing_volume` objective reads — is that cycle's post-cut state.
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


def build_library_tables(cycles: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`trajectory_cycles` and the narrow `trajectory_index`, per §5 of the design note.

    Volumes are per-acre; the scheduler multiplies by stand acres. `trajectory_index`
    summarises over the scored cycles 1..10 only — the eleventh is a carrier, not part of
    the horizon, and rolling it into `total_removed_merch_cuft_per_ac` would credit a
    trajectory with volume the plan never counts.
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
    cyc = cyc.rename(columns={"Year": "calendar_year",
                              "RMCuFt": "removed_merch_cuft_per_ac"})

    # `cycle` 0 is the 2022 inventory state, not a simulated cycle. Cycle 11 (2077) is the
    # carrier cycle, outside the 50-year horizon and outside the objective.
    scored = cyc[(cyc["cycle"] >= 1) & (cyc["cycle"] <= N_OBJECTIVE_CYCLES)]
    idx = (scored.groupby(["PLT_CN", "prescription"], as_index=False)
           .agg(cycles=("cycle", "nunique"),
                first_year=("calendar_year", "min"),
                last_year=("calendar_year", "max"),
                total_removed_merch_cuft_per_ac=("removed_merch_cuft_per_ac", "sum"),
                harvest_cycles=("removed_merch_cuft_per_ac", lambda s: int((s > 0).sum())),
                ending_ba=("BA", "last"),
                ending_merch_cuft_per_ac=("MCuFt", "last")))
    return cyc, idx


# --------------------------------------------------------------------------------------
# Stage F — the check that makes the expansion trustworthy
# --------------------------------------------------------------------------------------

def realised_menu(expanded: pd.DataFrame, idx: pd.DataFrame) -> pd.DataFrame:
    """Menu-size distribution as the *scheduler* sees it, not as the library intended it.

    `expanded` describes the decision space before simulation, so counting it directly
    credits a stand with an option whose FVS run was excluded — an option it cannot take.
    That is how the published `options_per_stand.csv` came to disagree with `library_size` in
    the plan and `options_per_stand` in the quality report, both of which are built from the
    trajectories that exist: one excluded run moved one stand from five options to four, and
    the two tables differed by exactly that stand.

    Restricting to `(plot, prescription)` pairs present in the library is the whole fix. The
    assertion guards the case the restriction could hide: exclusions emptying a stand's menu
    altogether, which would drop it from this report silently while the plan sees a stand
    with no trajectory at all.
    """
    realised = expanded.merge(idx[["PLT_CN", "prescription"]].drop_duplicates(),
                              on=["PLT_CN", "prescription"], how="inner")
    menu = (realised.groupby("unit_id").size().rename("options")
            .reset_index().groupby("options").size().rename("stands").reset_index())
    covered, total = int(menu["stands"].sum()), int(expanded["unit_id"].nunique())
    if covered != total:
        raise AssertionError(
            f"menu report covers {covered} stands but the library has {total}: an exclusion "
            f"has emptied a stand's menu entirely, which the plan would see as a stand with "
            f"no trajectory at all"
        )
    return menu


def reconcile_run_ledger(rendered: set[str], published: set[str], excluded: set[str]) -> None:
    """Every rendered run must be published or named as excluded — exactly one of the two.

    `validate_runs` can only judge runs that produced rows, and the exclusion gate can only
    acknowledge failures somebody recorded. A run that produced **no** rows and was never
    recorded as a failure appears in neither frame, so nothing downstream can notice that
    its option has quietly left the decision space. That is the gap this closes: it is a
    partition check, not a count check, so it also catches a run counted on both sides.
    """
    unaccounted = rendered - published - excluded
    double_counted = published & excluded
    if unaccounted or double_counted:
        raise SystemExit(
            f"the run ledger does not reconcile: {len(rendered)} runs rendered, "
            f"{len(published)} published, {len(excluded)} excluded. "
            f"Unaccounted for: {sorted(unaccounted)[:10]}. Both published and excluded: "
            f"{sorted(double_counted)[:10]}. Refusing to publish a library whose missing "
            f"trajectories nobody can name."
        )


def check_base_library(idx: pd.DataFrame, cyc: pd.DataFrame, runs: pd.DataFrame,
                       tol: float = 1e-6) -> dict:
    """Offset-0 must reproduce 2026-08-31 exactly, except where the terminal cycle fixes it.

    This is the claim the artifact rests on, and it covers both of this week's changes at
    once: if the timing grid had perturbed the base prescriptions, or if the eleventh cycle
    had altered anything inside the horizon, these 3,000-odd runs would disagree with last
    week's published `trajectory_index.csv`.

    **The exception is not a tolerance, it is a correction, and it is checked as one.** A run
    whose prescription schedules an entry in 2072 *must* differ, because last week that entry
    silently did not execute — the projection ended in the same year and FVS never carried it
    out (`probe_terminal_cycle.py`). Those runs were published carrying a prescription, a
    harvest year, and no harvest. So they are separated by `entry_years`, which the keyfile
    renderer reports, and each side gets the check that belongs to it:

    * **no 2072 entry** — equality on total removed volume, ending standing volume and
      harvest-cycle count. Nothing else may move at all.
    * **a 2072 entry** — the difference must be *exactly* the cycle-10 removal that last week
      dropped: `total_now == total_20260831 + cuft_cycle_10_now`, one more harvest cycle than
      before, and an ending standing volume no higher than before, since a cut cannot add
      wood. Anything else and the eleventh cycle is doing something other than executing the
      final year's entry.

    The tolerance is a CSV round-trip, not a modelling allowance. A run last week excluded
    (one died of a genuine div-by-zero in FVS's mortality routine) is absent from its index
    and simply does not take part.
    """
    prev = pd.read_csv(INDEX_2026_08_31, dtype={"PLT_CN": str})
    prev = prev[["PLT_CN", "prescription", "total_removed_merch_cuft_per_ac",
                 "ending_merch_cuft_per_ac", "harvest_cycles"]]
    mine = idx.copy()
    parts = mine["prescription"].map(base_of)
    mine["variant"] = mine["prescription"]
    mine["base_prescription"] = [p[0] for p in parts]
    mine["offset_years"] = [p[1] for p in parts]
    base = mine[mine["offset_years"] == 0].drop(columns=["prescription"]).rename(
        columns={"base_prescription": "prescription"})

    # The cycle-10 removal, per run: what last week's ten-cycle projection could not execute.
    terminal_cycle = N_OBJECTIVE_CYCLES
    c10 = (cyc[cyc["cycle"] == terminal_cycle]
           .set_index(["PLT_CN", "prescription"])["removed_merch_cuft_per_ac"])
    base = base.join(c10.rename("cuft_terminal_cycle"), on=["PLT_CN", "variant"])
    base["cuft_terminal_cycle"] = base["cuft_terminal_cycle"].fillna(0.0)

    # Which runs schedule an entry in the horizon's final year, from the rendered keyfiles.
    entries = runs.set_index(["PLT_CN", "prescription"])["entry_years"]
    has_terminal = entries.map(
        lambda s: str(LAST_ENTRY_YEAR) in str(s).split(";")).rename("has_terminal_entry")
    base = base.join(has_terminal, on=["PLT_CN", "variant"])
    base["has_terminal_entry"] = base["has_terminal_entry"].fillna(False)

    m = prev.merge(base, on=["PLT_CN", "prescription"], how="inner",
                   suffixes=("_20260831", "_now"))
    if m.empty:
        raise AssertionError("no offset-0 run matched 2026-08-31's index; the run keys have "
                             "drifted and the base-library check would vacuously pass")

    unchanged = m[~m["has_terminal_entry"]]
    corrected = m[m["has_terminal_entry"]]

    d_removed = (unchanged["total_removed_merch_cuft_per_ac_20260831"]
                 - unchanged["total_removed_merch_cuft_per_ac_now"]).abs()
    d_standing = (unchanged["ending_merch_cuft_per_ac_20260831"]
                  - unchanged["ending_merch_cuft_per_ac_now"]).abs()
    moved = (unchanged["harvest_cycles_20260831"] != unchanged["harvest_cycles_now"])
    bad = (d_removed > tol) | (d_standing > tol) | moved
    if bad.any():
        raise AssertionError(
            f"{int(bad.sum())} of {len(unchanged)} offset-0 runs without an entry in "
            f"{LAST_ENTRY_YEAR} do not reproduce 2026-08-31's published volumes. The timing "
            f"grid or the eleventh cycle has changed the base library, which it must not. "
            f"Sample:\n{unchanged[bad].head(10).to_string()}"
        )

    # The corrected runs: the difference must be the terminal-cycle removal and nothing else.
    expected = (corrected["total_removed_merch_cuft_per_ac_20260831"]
                + corrected["cuft_terminal_cycle"])
    off = (expected - corrected["total_removed_merch_cuft_per_ac_now"]).abs()
    cycles_off = (corrected["harvest_cycles_now"]
                  != corrected["harvest_cycles_20260831"] + 1)
    standing_up = (corrected["ending_merch_cuft_per_ac_now"]
                   > corrected["ending_merch_cuft_per_ac_20260831"] + tol)
    wrong = (off > tol) | cycles_off | standing_up
    if wrong.any():
        raise AssertionError(
            f"{int(wrong.sum())} of {len(corrected)} offset-0 runs with an entry in "
            f"{LAST_ENTRY_YEAR} differ from 2026-08-31 by something other than that entry "
            f"finally executing. The eleventh cycle is not doing only what it is for. "
            f"Sample:\n{corrected[wrong].head(10).to_string()}"
        )

    result = {
        "runs_in_20260831_index": int(len(prev)),
        "offset_0_runs_compared": int(len(m)),
        "runs_not_matched": int(len(prev) - len(m)),
        "runs_required_identical": int(len(unchanged)),
        "max_abs_diff_total_removed_cuft_per_ac": float(d_removed.max()) if len(d_removed) else 0.0,
        "max_abs_diff_ending_merch_cuft_per_ac": float(d_standing.max()) if len(d_standing) else 0.0,
        "runs_corrected_by_terminal_cycle": int(len(corrected)),
        "volume_recovered_cuft_per_ac": float(corrected["cuft_terminal_cycle"].sum()),
        "corrected_runs": sorted(corrected["PLT_CN"] + "::" + corrected["prescription"]),
    }
    log.info("Base-library check passed: %d of %d offset-0 runs are identical to "
             "2026-08-31 (max |diff| %.3g cuft/ac removed, %.3g standing)",
             len(unchanged), len(m), result["max_abs_diff_total_removed_cuft_per_ac"],
             result["max_abs_diff_ending_merch_cuft_per_ac"])
    if len(corrected):
        log.warning("%d offset-0 runs differ, all with an entry in %d that 2026-08-31 "
                    "published as a harvest that never happened. They now remove "
                    "%.1f cuft/ac in cycle %d: %s", len(corrected), LAST_ENTRY_YEAR,
                    result["volume_recovered_cuft_per_ac"], terminal_cycle,
                    ", ".join(result["corrected_runs"]))
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--limit", type=int, default=None,
                    help="smoke-test a subset of runs. Publishes NOTHING: no library "
                         "tables, no artifact CSVs, no raw cache and no manifest, because a "
                         "truncated run set is a different library and the manifest is what "
                         "tells the scheduler a library is complete.")
    ap.add_argument("--reuse-raw", action="store_true",
                    help="reuse cached raw FVS_Summary2 output if it was collected from "
                         "exactly this run set (same keyfile hashes). Used to re-run the "
                         "publishing half after acknowledging exclusions, without paying "
                         "for the batch twice. It never changes what is published.")
    ap.add_argument("--dry-run", action="store_true",
                    help="expand the library and report its size and shape, then stop "
                         "without touching FVS or the FIA database")
    ap.add_argument("--allow-excluded-runs", type=int, default=0,
                    help="publish even though this many (plot, prescription variant) "
                         "trajectories could not be simulated. The batch fails closed by "
                         "default; the number must be stated deliberately, every exclusion "
                         "is written to fvs_failures.csv, and a count that moves in either "
                         "direction stops publication until a human looks again.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Validated before any expensive work, and before `--limit` is consulted anywhere.
    # `--limit 0` must not mean "no limit": every guard below tests `is not None`, so a zero
    # would otherwise be a smoke run of nothing — but a truthiness test would have sent it
    # down the *publishing* path instead, running all 13,035 trajectories and republishing
    # the artifact for a caller who asked for none. A negative value would reach pandas'
    # `head(-N)`, which drops the last N rather than taking any.
    if args.limit is not None and args.limit < 1:
        raise SystemExit(
            f"--limit must be at least 1 (got {args.limit}); it selects how many runs to "
            f"smoke-test, and there is nothing to learn from zero. Omit it for a full run."
        )
    # Checked here rather than at the pool: `ProcessPoolExecutor` rejects a non-positive
    # `max_workers`, but only once the batch starts — after the input database is rebuilt and
    # 13,035 keyfiles are rendered. Worse, a cache hit skips the pool entirely, so the same
    # invalid command can appear to succeed depending on what is lying in the work directory.
    if args.workers < 1:
        raise SystemExit(
            f"--workers must be at least 1 (got {args.workers}); it is the size of the FVS "
            f"process pool."
        )
    if not args.dry_run and not FVS_BIN.exists():
        raise SystemExit(f"FVSsn not found at {FVS_BIN}; set FVSSN_BIN or build it (README)")

    # Invalidate the previous run's marker first: a rebuild that fails partway must not
    # leave a manifest vouching for a predecessor's files.
    WORK.mkdir(parents=True, exist_ok=True)
    if not args.dry_run:
        MANIFEST.unlink(missing_ok=True)

    stands, carved_lib = carved_landscape()
    expanded, acct = expand_library(carved_lib)

    if args.dry_run:
        runs = expanded.dropna(subset=["prescription"]).drop_duplicates(
            ["PLT_CN", "prescription"])
        log.info("DRY RUN: %d stands, %d library rows, %d FVS runs to make",
                 len(stands), len(expanded), len(runs))
        log.info("Options per stand:\n%s",
                 expanded.groupby("unit_id").size().value_counts().sort_index().to_string())
        log.info("Variant accounting:\n%s", acct.to_string(index=False))
        return

    plots = set(expanded["PLT_CN"].dropna().astype(str))
    sdi, input_fingerprint = build_input_db(plots)
    log.info("Species SDI tables built for %d/%d donor plots", len(sdi), len(plots))

    runs = render_batch(expanded, sdi)
    if args.limit is not None:
        runs = runs.head(args.limit)
        log.warning("SMOKE MODE: only %d runs", len(runs))

    # The batch is ~20 minutes for 13,035 runs on four cores, and the exclusion gate below
    # deliberately refuses to publish until an operator has stated the exclusion count
    # exactly — which means the first run of a new library always stops there. Raw
    # FVS_Summary2 is therefore cached the moment it is collected, so acknowledging those
    # exclusions costs a re-read rather than a re-run. The cache is keyed to the run set it
    # came from: a library whose runs have changed at all re-runs FVS.
    raw_cache = WORK / "raw_summary2.csv.gz"
    cache_key = WORK / "raw_summary2.key"
    # The cache holds FVS output, so its identity must cover **everything that can change
    # that output**, not only the run set. Three things can: the keyfiles, the tree lists
    # they are simulated against, and the binary simulating them. Hashing the keyfiles alone
    # would accept a cache built from a different `FIA_5county_consolidated.db` or a
    # rebuilt `FVSsn` — neither of which alters a single keyfile — and publish those stale
    # volumes under a fresh manifest.
    key = hashlib.sha256("\n".join([
        *sorted(runs["PLT_CN"] + "::" + runs["prescription"] + "::"
                + runs["keyfile_sha256_16"]),
        f"input_db::{input_fingerprint}",
        f"fvs_bin::{hashlib.sha256(FVS_BIN.read_bytes()).hexdigest()[:16]}",
        f"num_cycle::{NUM_CYCLE}",
    ]).encode()).hexdigest()[:16]
    raw_failures = WORK / "raw_failures.csv"
    # All three cache components are required. A run that failed outright contributes **no
    # summary rows at all**, so the failure sidecar is the only record that it ever existed:
    # treating a missing one as "no failures" would turn a lost file into a smaller decision
    # space, with the failed trajectory's option vanishing without ever reaching the
    # exclusion gate. A missing sidecar is therefore a cache miss, not an empty frame.
    cache_complete = raw_cache.exists() and cache_key.exists() and raw_failures.exists()
    if args.reuse_raw and cache_complete and cache_key.read_text().strip() == key:
        cycles = pd.read_csv(raw_cache, dtype={"PLT_CN": str})
        failures = read_failures(raw_failures)
        log.warning("Reusing cached FVS output for this exact run set: %d rows, %d failures. "
                    "No FVS run was made.", len(cycles), len(failures))
    else:
        if args.reuse_raw:
            log.warning("--reuse-raw: no cache for this run set; running FVS")
        log.info("Running %d FVS trajectories on %d workers (%d cycles each, 2022-%d)",
                 len(runs), args.workers, NUM_CYCLE, INV_YEAR + NUM_CYCLE * CYCLE_YEARS)
        cycles, failures = run_batch(runs, args.workers)
        log.info("Collected %d FVS_Summary2 rows; %d runs failed outright",
                 len(cycles), len(failures))
        if args.limit is None:
            # A smoke run must not touch the cache: its key belongs to a truncated run set,
            # and writing it would replace a good full cache with a partial one.
            cycles.to_csv(raw_cache, index=False)
            failures.to_csv(raw_failures, index=False)
            cache_key.write_text(key)

    cyc, idx = build_library_tables(cycles)
    cyc, incomplete = validate_runs(cyc)
    if len(incomplete):
        idx = idx.merge(incomplete[["PLT_CN", "prescription"]],
                        on=["PLT_CN", "prescription"], how="left", indicator=True)
        idx = idx[idx["_merge"] == "left_only"].drop(columns="_merge")
    excluded = pd.concat([failures, incomplete], ignore_index=True) if len(incomplete) \
        else failures

    # Every rendered run must end up on exactly one side of the ledger: published with a
    # complete trajectory, or excluded and named. Neither `validate_runs` nor the exclusion
    # gate can see a run that produced no rows *and* was never recorded as a failure — it
    # simply is not in either frame — so that run's option would vanish from the decision
    # space silently. This reconciliation is what makes "fails closed" mean the whole run
    # set rather than only the runs that reported something.
    rendered_keys = set(runs["PLT_CN"] + "::" + runs["prescription"])
    published_keys = set(idx["PLT_CN"] + "::" + idx["prescription"]) if len(idx) else set()
    excluded_ledger = set(excluded["PLT_CN"] + "::" + excluded["prescription"]) \
        if len(excluded) else set()
    reconcile_run_ledger(rendered_keys, published_keys, excluded_ledger)

    # A smoke run stops here, before anything at all is published — including the artifact
    # copy of `fvs_failures.csv` the gate below maintains. `--limit` changes the *run set*,
    # not just how long the batch takes: every check downstream would then validate a
    # truncated library, `check_base_library` would compare only the offset-0 runs that
    # happen to be in it, and the manifest it would write is the marker
    # `make_annealed_plan.py` reads as "this library is complete and safe to plan over".
    # Producing that marker from ten runs is the one way this driver could hand the
    # scheduler a decision space nobody chose. The exclusion gate is skipped rather than
    # applied, because an acknowledgement of the full library's failures says nothing about
    # a truncated one's.
    if args.limit is not None:
        log.warning("SMOKE MODE: %d runs simulated, %d complete, %d failed or incomplete. "
                    "Nothing written: no library tables, no artifact CSVs, no raw cache and "
                    "no manifest, and the exclusion acknowledgement is untouched. Note the "
                    "previous manifest was invalidated at startup, as on any run: a smoke "
                    "test therefore leaves the library unplannable until a full run "
                    "republishes it, which is the safe direction.",
                    len(runs), len(idx), len(excluded))
        return

    # Fail closed. A partial library is not a smaller library: the scheduler would read a
    # missing trajectory as an option the stand does not have, and the plan would be quietly
    # built over a decision space nobody chose.
    #
    # **What is acknowledged is the exclusion *set*, not its size.** A count cannot tell one
    # failing trajectory from another, so a run in which the known failure starts passing and
    # a different one starts failing would sail through an unchanged `--allow-excluded-runs
    # 1` and put a newly missing trajectory into the published decision space unreviewed.
    # The committed `fvs_failures.csv` is the record of what a human looked at and accepted,
    # so it is read *before* being overwritten and compared key by key.
    # The artifact copy is written only once the gate passes, so a refused run leaves the
    # acknowledged record intact — overwriting it first would make the *next* run compare
    # against the very set nobody has reviewed. Until then the failures live in the interim
    # directory, which is where an operator inspects them.
    failures_csv = OUT_DIR / "fvs_failures.csv"
    prior_keys = None
    if failures_csv.exists():
        prior = pd.read_csv(failures_csv, dtype={"PLT_CN": str})
        prior_keys = set(prior["PLT_CN"] + "::" + prior["prescription"])
    excluded_keys = set(excluded["PLT_CN"] + "::" + excluded["prescription"]) \
        if len(excluded) else set()
    excluded.to_csv(WORK / "excluded_runs.csv", index=False)

    if len(excluded):
        log.warning("%d trajectories could not be simulated; see %s",
                    len(excluded), WORK / "excluded_runs.csv")
        for row in excluded.head(50).itertuples(index=False):
            log.warning("  excluded %s / %s — %s", row.PLT_CN, row.prescription, row.error)
    if len(excluded) != args.allow_excluded_runs:
        raise SystemExit(
            f"{len(excluded)} trajectories failed or came back incomplete, but "
            f"--allow-excluded-runs declares {args.allow_excluded_runs}. Refusing to "
            f"publish: the exclusion set has changed since it was last acknowledged. "
            f"Inspect {WORK / 'excluded_runs.csv'}, then re-run with --allow-excluded-runs "
            f"{len(excluded)} if these exclusions are acceptable."
        )
    if prior_keys is not None and excluded_keys != prior_keys:
        added = sorted(excluded_keys - prior_keys)
        gone = sorted(prior_keys - excluded_keys)
        raise SystemExit(
            f"the exclusion set has changed identity, not only size. Newly failing: "
            f"{added or 'none'}. No longer failing: {gone or 'none'}. Refusing to publish: "
            f"{args.allow_excluded_runs} is the right count but not the acknowledged set, "
            f"and a trajectory nobody has looked at would enter the decision space as "
            f"'missing'. Inspect each new failure, then delete the committed "
            f"fvs_failures.csv to re-acknowledge from scratch and re-run — that committed "
            f"file *is* the acknowledgement, and it is left untouched by this refusal."
        )
    if len(excluded):
        excluded.to_csv(failures_csv, index=False)
    else:
        failures_csv.unlink(missing_ok=True)

    base_check = check_base_library(idx, cyc, runs)

    # --- interim tables the annealer reads (gitignored) --------------------------------
    # `excluded_runs.csv` is already on disk: it is written before the gate above, so a
    # refused run still leaves the failures somewhere an operator can read them.
    cyc.to_csv(WORK / "trajectory_cycles.csv", index=False)
    idx.to_csv(WORK / "trajectory_index.csv", index=False)
    stands.to_csv(WORK / "carved_stands.csv", index=False)
    expanded.to_csv(WORK / "expanded_library.csv", index=False)
    log.info("trajectory_cycles: %d rows; trajectory_index: %d runs", len(cyc), len(idx))

    # --- the artifact tables ----------------------------------------------------------
    # One row per FVS run, carrying §5's `harvest_cuft[cycle]` as columns rather than as
    # 12 rows per run: the expanded library is 3.5x last week's, and the long form would be
    # a 14 MB text file saying mostly zero. Same content as
    # `2026-08-31/trajectory_harvest_by_cycle.csv`, pivoted.
    wide = (cyc.pivot_table(index=["PLT_CN", "prescription"], columns="cycle",
                            values="removed_merch_cuft_per_ac", aggfunc="sum")
            .rename(columns=lambda c: f"cuft_cycle_{int(c)}").reset_index())
    parts = idx["prescription"].map(base_of)
    pub = idx.assign(fvs_run_id=idx["PLT_CN"] + "::" + idx["prescription"],
                     base_prescription=[p[0] for p in parts],
                     offset_years=[p[1] for p in parts]).merge(
        wide, on=["PLT_CN", "prescription"], how="left")
    order = ["fvs_run_id", "PLT_CN", "prescription", "base_prescription", "offset_years",
             "cycles", "first_year", "last_year", "total_removed_merch_cuft_per_ac",
             "harvest_cycles", "ending_ba", "ending_merch_cuft_per_ac"]
    cycle_cols = [f"cuft_cycle_{c}" for c in range(0, NUM_CYCLE + 1)
                  if f"cuft_cycle_{c}" in pub.columns]
    pub = pub[order + cycle_cols]
    pub.to_csv(OUT_DIR / "trajectory_index.csv", index=False)

    acct.to_csv(OUT_DIR / "library_expansion.csv", index=False)

    realised_menu(expanded, idx).to_csv(OUT_DIR / "options_per_stand.csv", index=False)

    # What the timing grid was built for: which cycles the library can now cut in at all.
    reach = (cyc[cyc["cycle"].between(1, N_OBJECTIVE_CYCLES)]
             .assign(cuts=lambda d: d["removed_merch_cuft_per_ac"] > 0)
             .groupby("cycle", as_index=False)
             .agg(runs_cutting=("cuts", "sum"),
                  removed_cuft_per_ac=("removed_merch_cuft_per_ac", "sum"))
             .assign(calendar_year=lambda d: INV_YEAR + d["cycle"] * CYCLE_YEARS))
    reach.to_csv(OUT_DIR / "library_reach_by_cycle.csv", index=False)
    log.info("Library reach by cycle (runs able to cut):\n%s", reach.to_string(index=False))

    MANIFEST.write_text(json.dumps({
        "completed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trajectory_cycles_rows": int(len(cyc)),
        "trajectory_index_rows": int(len(idx)),
        "carved_stands_rows": int(len(stands)),
        "expanded_library_rows": int(len(expanded)),
        "carved_library_rows_pre_expansion": int(len(carved_lib)),
        "excluded_runs": int(len(excluded)),
        "offsets_years": list(OFFSETS),
        "num_cycle": NUM_CYCLE,
        "objective_cycles": N_OBJECTIVE_CYCLES,
        "last_entry_year": LAST_ENTRY_YEAR,
        "inv_year": INV_YEAR,
        "base_library_check": base_check,
    }, indent=2))
    log.info("Wrote %s — the batch is complete and safe to plan over", MANIFEST.name)


if __name__ == "__main__":
    main()
