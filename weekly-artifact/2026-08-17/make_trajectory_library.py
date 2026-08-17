"""
Enumerate the ARTEMIS trajectory library for the five-county Florida pilot —
the decision space the simulated-annealing scheduler will search.

`notes/trajectory-library-and-annealing.md` (adopted 2026-08-06) is the design of
record: ownership class decides which prescriptions a stand is *eligible* for, FVS
runs once per `(stand, prescription)` pair offline, and the annealer then picks one
trajectory per stand. §3-§4 of that note are the library-build stage. Nothing had
ever enumerated it against the real landscape, so the only number the repository
carried was `config/management_regimes.yaml`'s declared upper bound:

    si_bins: 3
    estimated_max_runs_pilot: 16632      # 693 x 8 x 3

That is a worst case assuming every stand is eligible for every prescription. This
driver replaces the estimate with the measured library: for every attributed unit on
the pilot landscape it resolves the owner-class menu, filters it by forest branch,
resolves each surviving prescription through the repository's own resolver, and
deduplicates to the `(FIA plot, prescription)` key the FVS batch is actually keyed on.

Every modelling decision belongs to committed repository code —

  * `pipeline.s3_management.owner_classes`     : Harris class -> ARTEMIS owner class
  * `pipeline.s3_management.regime_assignment` : eligible menu, forest branch, and the
                                                 schedule/template/params resolution
  * `pipeline.s4_fvs.regime_templates`         : the FVS keyfile each pair renders to
  * `pipeline.s4_fvs.paint_fvs_to_raster`      : the TM_ID -> PLT_CN crosswalk loader

...and the landscape attribution is reused verbatim from the previous artifact's
driver (`weekly-artifact/2026-08-10/make_schedule.py`), so the unit table this reports
on is the same one the Phase 4.1 schedule was built from.

Two mechanics are worth stating plainly, because they are this driver's own choices:

1. **Resolving a non-default prescription.** `assign_prescription` resolves whatever
   `config["owner_classes"][owner]["default"][branch]` names. To resolve an *eligible*
   prescription through that same code path rather than reimplementing it, the driver
   hands the resolver a config copy whose default for that owner/branch slot is the
   prescription in question. The resolver is pure with respect to its config argument,
   so this exercises the real schedule/template/params/regen path exactly.

2. **What counts as one FVS run.** Units sharing a TreeMap plot share a tree list, so
   the library is keyed by `(PLT_CN, prescription)` — not by unit. That is the
   distinction between the naive decision-space size and the batch that actually has
   to be run, and quantifying it is the point of the artifact.

Usage (from the repo root, with the R2 inputs staged under ./data — see the artifact
README for the exact keys):

    uv run python weekly-artifact/2026-08-17/make_trajectory_library.py
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import logging
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.s3_management.owner_classes import MASKED, classify_owner  # noqa: E402
from pipeline.s3_management.regime_assignment import (  # noqa: E402
    assign_prescription,
    eligible_prescriptions,
    forest_type_branch,
    load_regimes_config,
)
from pipeline.s4_fvs.regime_templates import DEFAULT_INV_YEAR, build_thins, render_keyfile  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("make_trajectory_library")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"
TRAJECTORY = DATA / "interim/no_management_fl5co_fvs_output/fvs_trajectory.csv"
KEYFILE_DIR = DATA / "interim/trajectory_library_keyfiles"   # gitignored; not committed
SAMPLE_DIR = OUT_DIR / "sample_keyfiles"


def load_prior_driver():
    """Import the 2026-08-10 driver by path — its directory name is not a Python identifier."""
    path = REPO / "weekly-artifact/2026-08-10/make_schedule.py"
    spec = importlib.util.spec_from_file_location("make_schedule_20260810", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------
# Stage A — the landscape, and each stand's age at the inventory year
# --------------------------------------------------------------------------------------

def stand_ages(inv_year: int = DEFAULT_INV_YEAR) -> pd.DataFrame:
    """Each FVS stand's age at (or last before) the inventory year, from the baseline run."""
    traj = pd.read_csv(TRAJECTORY, dtype={"stand_cn": "string", "stand_id": "string"})
    log.info("FVS baseline: %d rows, %d stands, %d-%d", len(traj), traj["stand_cn"].nunique(),
             traj["calendar_year"].min(), traj["calendar_year"].max())
    at_inv = (
        traj[traj["calendar_year"] <= inv_year]
        .sort_values("calendar_year")
        .groupby("stand_cn", as_index=False)
        .last()[["stand_cn", "age", "calendar_year"]]
        .rename(columns={"stand_cn": "PLT_CN", "age": "stand_age",
                         "calendar_year": "age_source_year"})
    )
    log.info("Stand ages at %d: %d stands, age %.0f-%.0f (median %.0f)", inv_year, len(at_inv),
             at_inv["stand_age"].min(), at_inv["stand_age"].max(), at_inv["stand_age"].median())
    return at_inv


# --------------------------------------------------------------------------------------
# Stage B — enumerate (unit x eligible prescription)
# --------------------------------------------------------------------------------------

def resolve_pair(owner_class: str, branch: str, prescription: str, stand_age: float | None,
                 base_cfg: dict, cache: dict) -> dict:
    """Resolve one (owner, branch, prescription, age) through the repo's own resolver.

    See the module docstring, mechanic 1: the prescription under test is written into
    the default slot of a config copy so `assign_prescription` resolves it.
    """
    key = (owner_class, branch, prescription, stand_age)
    if key in cache:
        return cache[key]

    cfg = copy.deepcopy(base_cfg)
    cfg["owner_classes"][owner_class]["default"][branch] = prescription
    # `assign_prescription` re-derives owner and branch from the unit mapping, so hand it
    # a mapping that reproduces this (owner, branch, age) exactly.
    unit = {**_unit_for(owner_class, branch), "stand_age": stand_age, "SMZ_Pct": 0.0}
    pres = assign_prescription(unit, config=cfg)

    thins = build_thins(pres.template, pres.params) if pres.template != "no_management" else []
    entry_years = sorted({t.year for t in thins})
    out = {
        "template": pres.template,
        "entry_years": ";".join(str(y) for y in entry_years),
        "n_entries": len(entry_years),
        "first_entry_year": entry_years[0] if entry_years else None,
        "last_entry_year": entry_years[-1] if entry_years else None,
        "cuts": bool(entry_years),
        "regen_slot": pres.regen_slot or "",
        "collapsed_to_no_management": pres.template == "no_management" and prescription != "no_management",
        "notes": "; ".join(pres.notes),
        "params": ";".join(f"{k}={v}" for k, v in sorted(pres.params.items())),
    }
    cache[key] = out
    return out


# Harris/LETO OWN_CODE and FORTYPCD values that reproduce each (owner class, branch)
# through `classify_owner` / `forest_type_branch`. Chosen to be the plainest members of
# each class: 161 loblolly-shortleaf for pine, 503 oak-hickory for hardwood, 999 for other.
_OWN_CODE_FOR = {
    "private_industrial": 4, "private_family": 3, "tribal": 5,
    "federal": 6, "state": 7, "local": 8,
}
_FORTYPCD_FOR = {"pine": 161, "hardwood": 503, "other": 999}


def _unit_for(owner_class: str, branch: str) -> dict:
    """A minimal unit mapping that `classify_owner` resolves to ``owner_class``."""
    if owner_class in _OWN_CODE_FOR:
        return {"OWN_CODE": _OWN_CODE_FOR[owner_class], "FORTYPCD": _FORTYPCD_FOR[branch]}
    raise ValueError(f"no OWN_CODE reproduces owner class {owner_class!r}")


def enumerate_library(units: pd.DataFrame) -> pd.DataFrame:
    """One row per (unit x eligible prescription) — the decision space, per unit."""
    base_cfg = load_regimes_config()
    cache: dict = {}
    rows = []

    for unit in units.itertuples(index=False):
        mapping = {"OWN_CODE": unit.OWN_CODE, "FORTYPCD": unit.FORTYPCD, "SMZ_Pct": 0.0}
        assignment = classify_owner(mapping)
        if assignment.owner_class == MASKED:
            continue
        owner_class = assignment.owner_class
        branch = forest_type_branch(mapping)
        menu = eligible_prescriptions(owner_class, branch)
        age = None if pd.isna(unit.stand_age) else float(unit.stand_age)

        for prescription in menu:
            resolved = resolve_pair(owner_class, branch, prescription, age, base_cfg, cache)
            rows.append({
                "unit_id": unit.unit_id,
                "tm_id": unit.tm_id,
                "PLT_CN": unit.PLT_CN,
                "county": unit.county,
                "owner_class": owner_class,
                "owner_name": unit.owner_name,
                "OWN_CODE": unit.OWN_CODE,
                "forest_branch": branch,
                "FORTYPCD": unit.FORTYPCD,
                "ForTypName": getattr(unit, "ForTypName", None),
                "acres": unit.acres,
                "stand_age": age,
                "prescription": prescription,
                **resolved,
            })

    lib = pd.DataFrame(rows)
    log.info("Decision space: %d (unit x prescription) rows over %d units, %d stands",
             len(lib), lib["unit_id"].nunique(), lib["PLT_CN"].nunique())
    log.info("Resolution cache: %d distinct (owner, branch, prescription, age) combinations",
             len(cache))
    return lib


def run_manifest(lib: pd.DataFrame, si_bins: int) -> pd.DataFrame:
    """Deduplicate the decision space to the FVS batch: one run per (PLT_CN, prescription)."""
    runs = (
        lib.groupby(["PLT_CN", "prescription"], as_index=False)
        .agg(template=("template", "first"),
             entry_years=("entry_years", "first"),
             n_entries=("n_entries", "first"),
             cuts=("cuts", "first"),
             regen_slot=("regen_slot", "first"),
             stand_age=("stand_age", "first"),
             units=("unit_id", "nunique"),
             acres=("acres", "sum"),
             owner_classes=("owner_class", lambda s: ";".join(sorted(set(s)))),
             counties=("county", lambda s: ";".join(sorted(set(s)))))
    )
    runs["si_expanded_runs"] = si_bins
    log.info("FVS batch: %d distinct (stand x prescription) runs over %d stands "
             "(x %d site-index bins = %d worst-case runs)",
             len(runs), runs["PLT_CN"].nunique(), si_bins, len(runs) * si_bins)
    return runs


# --------------------------------------------------------------------------------------
# Stage C — render the keyfiles the batch would submit
# --------------------------------------------------------------------------------------

def render_keyfiles(runs: pd.DataFrame, lib: pd.DataFrame) -> pd.DataFrame:
    """Render every run's FVS keyfile. Proves the library is renderable, not just countable."""
    KEYFILE_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    # One representative unit per (stand, prescription) supplies template + params.
    params = (lib.groupby(["PLT_CN", "prescription"], as_index=False)
                 .agg(template=("template", "first"), params=("params", "first")))
    param_map = {(r.PLT_CN, r.prescription): (r.template, r.params)
                 for r in params.itertuples(index=False)}

    records, sample_taken = [], set()
    for run in runs.itertuples(index=False):
        template, param_str = param_map[(run.PLT_CN, run.prescription)]
        kwargs = {}
        for item in filter(None, param_str.split(";")):
            k, v = item.split("=", 1)
            kwargs[k] = float(v) if "." in v else int(v)
        stand_id = f"S{run.PLT_CN}"
        key = render_keyfile(stand_id=stand_id, stand_cn=str(run.PLT_CN),
                             regime=template, params=kwargs, inv_year=DEFAULT_INV_YEAR)
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        (KEYFILE_DIR / f"{stand_id}__{run.prescription}.key").write_text(key)

        # Commit one keyfile per prescription as the reviewable sample.
        if run.prescription not in sample_taken:
            (SAMPLE_DIR / f"{run.prescription}.key").write_text(key)
            sample_taken.add(run.prescription)

        records.append({"PLT_CN": run.PLT_CN, "prescription": run.prescription,
                        "template": template, "keyfile_sha256_16": digest,
                        "keyfile_bytes": len(key)})

    rendered = pd.DataFrame(records)
    log.info("Rendered %d keyfiles (%d distinct by content hash); %d committed as samples",
             len(rendered), rendered["keyfile_sha256_16"].nunique(), len(sample_taken))
    return rendered


# --------------------------------------------------------------------------------------
# Stage D — summaries
# --------------------------------------------------------------------------------------

def summarize(lib: pd.DataFrame, runs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    # `lib` repeats a unit's acres once per eligible prescription, so unit-level acreage is
    # taken from the deduplicated unit table rather than summed off the library.
    unit_level = lib.drop_duplicates("unit_id")
    by_owner = (
        unit_level.groupby(["owner_class", "forest_branch"], as_index=False)
        .agg(units=("unit_id", "nunique"), stands=("PLT_CN", "nunique"), acres=("acres", "sum"))
    )
    counts = (lib.groupby(["owner_class", "forest_branch"])
                 .agg(library_rows=("prescription", "size"),
                      menu_size=("prescription", "nunique")).reset_index())
    by_owner = by_owner.merge(counts, on=["owner_class", "forest_branch"])

    by_prescription = (
        lib.groupby("prescription", as_index=False)
        .agg(units_eligible=("unit_id", "nunique"), stands_eligible=("PLT_CN", "nunique"),
             acres_eligible=("acres", "sum"), cuts=("cuts", "first"),
             collapsed=("collapsed_to_no_management", "sum"))
        .sort_values("acres_eligible", ascending=False)
    )
    by_prescription["fvs_runs"] = by_prescription["prescription"].map(
        runs.groupby("prescription")["PLT_CN"].nunique())

    menu_realized = (
        lib.groupby(["owner_class", "forest_branch"])["prescription"]
        .agg(lambda s: " | ".join(sorted(set(s)))).reset_index()
        .rename(columns={"prescription": "eligible_menu"})
    )
    menu_realized = menu_realized.merge(
        by_owner[["owner_class", "forest_branch", "units", "stands", "acres",
                  "menu_size", "library_rows"]],
        on=["owner_class", "forest_branch"])

    return {"by_owner": by_owner, "by_prescription": by_prescription,
            "menu_realized": menu_realized.sort_values("acres", ascending=False)}


def main() -> None:
    prior = load_prior_driver()
    log.info("Attributing the pilot landscape via %s", prior.__file__)
    units = prior.build_units()

    ages = stand_ages()
    units = units.merge(ages, on="PLT_CN", how="left")
    missing_age = units["stand_age"].isna()
    if missing_age.any():
        log.warning("%d units (%.0f ac) have no FVS stand age; they fall back to "
                    "offset-based scheduling, which is the resolver's documented behaviour",
                    int(missing_age.sum()), units.loc[missing_age, "acres"].sum())

    lib = enumerate_library(units)
    cfg = load_regimes_config()
    runs = run_manifest(lib, si_bins=cfg["si_bins"])
    rendered = render_keyfiles(runs, lib)
    runs = runs.merge(rendered[["PLT_CN", "prescription", "keyfile_sha256_16"]],
                      on=["PLT_CN", "prescription"], how="left")
    summaries = summarize(lib, runs)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lib.to_csv(OUT_DIR / "trajectory_library.csv", index=False)
    runs.to_csv(OUT_DIR / "fvs_run_manifest.csv", index=False)
    for name, df in summaries.items():
        df.to_csv(OUT_DIR / f"library_{name}.csv", index=False)

    declared = cfg["estimated_max_runs_pilot"]
    measured = len(runs) * cfg["si_bins"]
    log.info("=" * 78)
    log.info("Declared upper bound (config): %d runs", declared)
    log.info("Measured library:              %d runs (%.1f%% of the bound)",
             measured, 100 * measured / declared)
    log.info("=" * 78)
    for name, df in summaries.items():
        log.info("%s\n%s", name, df.to_string(index=False))


if __name__ == "__main__":
    main()
