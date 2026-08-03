"""
Fixed fallback tree lists (policy layer over `config/fallback_treelists.yaml`).

Decides what a stand is initialized from when it has no tree list of its own, in the two
situations that produce one:

    INITIALIZATION GAP (year 0) — the pixel is forest under the mask but TreeMap gives it
        nothing usable: nodata under the mask, a TM_ID with no crosswalk row, or a donor
        plot with no live tree records. :func:`resolve_initialization` walks the ladder in
        the config: a nearby same-type unit first, a very close any-type unit second, and
        a pinned fixed list last.

    REGENERATION (mid-run) — the scheduler applied a stand-replacing entry and the stand is
        now empty. The prescription names a regeneration slot (see
        `config/management_regimes.yaml`) and the coupling loop restarts the stand from
        that fixed list. FVS PLANT/NATREGEN keywords are deliberately not used: their field
        layouts are unverified here, whereas external cut injection and restart are
        verified exact on stand values
        (`research/restart_fidelity/outputs/gate_cut_injection.txt`).

Every fallback is a **real, unmodified FIA tree list** — one plot per slot, chosen by the
deterministic median-basal-area rule and pinned by ``PLT_CN`` in
`config/fallback_treelists.lock.yaml`. Nothing here invents a tree.

The decision functions and the donor-selection rule are pure and unit-tested. Only
``--resolve`` touches the FIA database.

Usage:
    from pipeline.s4_fvs.fallback_treelists import resolve_initialization
    decision = resolve_initialization(fortypcd=607, donor_distance_m=None)
    decision.slot          # 'bottomland_hardwood_established'
    decision.tree_source   # 'FALLBACK_FIXED'

    uv run python -m pipeline.s4_fvs.fallback_treelists --resolve
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = _REPO_ROOT / "config" / "fallback_treelists.yaml"

# TREE_SOURCE tag values (mirrors `tree_source_values` in the config).
SOURCE_DIRECT = "FIA_WEIGHTED_DIRECT"
SOURCE_NEAREST = "IMPUTED_NEAREST"
SOURCE_FALLBACK = "FALLBACK_FIXED"
SOURCE_REGEN = "REGEN_FIXED"

NONSTOCKED_FORTYPCD = 999


@dataclass(frozen=True)
class FallbackDecision:
    """Which ladder rung fired for one unit, and what it means downstream."""

    rung: str
    method: str
    tree_source: str
    slot: str | None = None
    max_distance_m: float | None = None

    @property
    def uses_fixed_list(self) -> bool:
        return self.method == "fallback_slot"


@lru_cache(maxsize=None)
def load_fallback_policy(path: str | None = None) -> dict:
    """Load and cache `config/fallback_treelists.yaml`."""
    with open(Path(path) if path else CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_lock(policy: dict | None = None) -> dict | None:
    """Load the pinned-PLT_CN lock file, or ``None`` when the slots are unresolved."""
    policy = policy or load_fallback_policy()
    lock_path = _REPO_ROOT / policy["lock_file"]
    if not lock_path.exists():
        return None
    with open(lock_path) as f:
        return yaml.safe_load(f)


# ---- forest-type routing --------------------------------------------------------------

def forest_type_group_code(fortypcd) -> int | None:
    """
    FIA forest type group for a FORTYPCD: the code floored to the nearest ten.

    141 (longleaf) → 140, 161 (loblolly) → 160, 607 (baldcypress/water tupelo) → 600.
    999 (nonstocked) is its own group and is returned unchanged.
    """
    try:
        code = int(float(fortypcd))
    except (TypeError, ValueError):
        return None
    if code == NONSTOCKED_FORTYPCD:
        return NONSTOCKED_FORTYPCD
    return (code // 10) * 10


def forest_type_group(fortypcd, policy: dict | None = None) -> str | None:
    """Broad group name — ``pine``, ``mixed``, ``hardwood``, ``nonstocked`` — or ``None``."""
    policy = policy or load_fallback_policy()
    try:
        code = int(float(fortypcd))
    except (TypeError, ValueError):
        return None
    for name, span in policy["forest_type_groups"].items():
        if span["min"] <= code <= span["max"]:
            return name
    return None


def is_bottomland_hardwood(fortypcd, policy: dict | None = None) -> bool:
    """True for the oak/gum/cypress and elm/ash/cottonwood groups."""
    policy = policy or load_fallback_policy()
    group = forest_type_group_code(fortypcd)
    return group in policy["bottomland_hardwood_groups"]


def ladder_type_key(fortypcd, policy: dict | None = None) -> str | None:
    """
    The key the ladder's `fixed_slot_by_forest_type` mapping is looked up by.

    Splits hardwood into bottomland and upland; everything else passes through as its
    broad group name.
    """
    policy = policy or load_fallback_policy()
    group = forest_type_group(fortypcd, policy)
    if group is None:
        return None
    if group == "hardwood":
        return "hardwood_bottomland" if is_bottomland_hardwood(fortypcd, policy) else "hardwood_upland"
    return group


# ---- the resolution ladder ------------------------------------------------------------

def resolve_initialization(
    *,
    fortypcd=None,
    donor_distance_m: float | None = None,
    donor_same_forest_type: bool = False,
    policy: dict | None = None,
) -> FallbackDecision:
    """
    Walk `initialization_ladder` for a unit with no tree list of its own.

    ``donor_distance_m`` is the distance to the nearest runnable unit, or ``None`` when
    there is none. ``donor_same_forest_type`` says whether that donor shares the
    recipient's broad forest-type group. The first rung whose constraints are satisfied
    wins; the ladder always terminates on the default slot.

    The returned rung must be recorded on every resulting tree row — see
    `required_reporting` in the config. A landscape where 8% of acres came from a fixed
    list is a different result from one where 0.3% did.
    """
    policy = policy or load_fallback_policy()

    for rung in policy["initialization_ladder"]:
        method = rung["method"]

        if method == "nearest_runnable_unit":
            constraints = rung.get("constraints", {})
            max_distance = constraints.get("max_distance_m")
            if donor_distance_m is None or donor_distance_m > max_distance:
                continue
            if constraints.get("require_same_forest_type_group") and not donor_same_forest_type:
                continue
            return FallbackDecision(
                rung=rung["rung"], method=method, tree_source=SOURCE_NEAREST,
                max_distance_m=max_distance,
            )

        if method == "fallback_slot":
            slot = rung.get("slot")
            if slot is None:
                key = ladder_type_key(fortypcd, policy)
                slot = rung["mapping"].get(key)
                if slot is None:
                    continue          # unknown forest type: fall through to the default rung
            return FallbackDecision(
                rung=rung["rung"], method=method, tree_source=SOURCE_FALLBACK, slot=slot,
            )

        raise ValueError(f"unknown ladder method {method!r} in fallback_treelists.yaml")

    raise ValueError(
        "initialization_ladder did not terminate — its last rung must be an unconditional "
        "`fallback_slot` with an explicit `slot`"
    )


def resolve_regeneration(slot: str, policy: dict | None = None) -> FallbackDecision:
    """
    The decision for a stand-replacing entry, given the prescription's regeneration slot.

    The slot name comes from `config/management_regimes.yaml`
    (``prescriptions.<name>.regen.treelist_slot``).
    """
    policy = policy or load_fallback_policy()
    spec = policy["slots"].get(slot)
    if spec is None:
        raise ValueError(f"unknown fallback slot {slot!r}; choices: {sorted(policy['slots'])}")
    if spec["use"] != "regeneration":
        raise ValueError(
            f"slot {slot!r} is a {spec['use']} slot and cannot be used for regeneration"
        )
    return FallbackDecision(
        rung="regeneration", method="fallback_slot", tree_source=SOURCE_REGEN, slot=slot,
    )


def plt_cn_for_slot(slot: str, policy: dict | None = None, lock: dict | None = None) -> str:
    """
    The pinned donor ``PLT_CN`` for a slot.

    Raises with the resolver command when the slots are unresolved. Failing loudly is the
    point: substituting an arbitrary tree list for a missing pin would be invisible in
    every downstream summary.
    """
    policy = policy or load_fallback_policy()
    lock = lock if lock is not None else load_lock(policy)
    if not lock or slot not in lock.get("slots", {}):
        raise RuntimeError(
            f"fallback slot {slot!r} is not resolved. Run:\n"
            f"    uv run python -m pipeline.s4_fvs.fallback_treelists --resolve\n"
            f"(needs the FIA SQLite from config/data_paths.yaml)"
        )
    return str(lock["slots"][slot]["plt_cn"])


# ---- donor-plot selection (pure) ------------------------------------------------------

def filter_candidates(plots, slot_filter: dict):
    """
    Apply one slot's FIA filter to a candidate plot table.

    ``plots`` needs ``PLT_CN`` and whichever of ``FORTYPCD``, ``STDORGCD``, ``STDAGE`` the
    filter references. Missing required columns raise rather than silently widening the
    filter.
    """
    import pandas as pd

    df = plots.copy()
    needed = {"PLT_CN"}
    if "fortypcd_min" in slot_filter or "fortypcd_max" in slot_filter or "fortypcd_in_groups" in slot_filter:
        needed.add("FORTYPCD")
    if "stdorgcd" in slot_filter:
        needed.add("STDORGCD")
    if "stdage_min" in slot_filter or "stdage_max" in slot_filter:
        needed.add("STDAGE")
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"candidate table is missing required columns: {sorted(missing)}")

    if "fortypcd_min" in slot_filter:
        df = df[pd.to_numeric(df["FORTYPCD"], errors="coerce") >= slot_filter["fortypcd_min"]]
    if "fortypcd_max" in slot_filter:
        df = df[pd.to_numeric(df["FORTYPCD"], errors="coerce") <= slot_filter["fortypcd_max"]]
    if "fortypcd_in_groups" in slot_filter:
        groups = set(slot_filter["fortypcd_in_groups"])
        df = df[df["FORTYPCD"].map(lambda c: forest_type_group_code(c) in groups)]
    if "stdorgcd" in slot_filter:
        df = df[pd.to_numeric(df["STDORGCD"], errors="coerce") == slot_filter["stdorgcd"]]
    if "stdage_min" in slot_filter:
        df = df[pd.to_numeric(df["STDAGE"], errors="coerce") >= slot_filter["stdage_min"]]
    if "stdage_max" in slot_filter:
        df = df[pd.to_numeric(df["STDAGE"], errors="coerce") <= slot_filter["stdage_max"]]
    return df.reset_index(drop=True)


def select_donor_plot(candidates, policy: dict | None = None) -> str:
    """
    The median-basal-area plot from a filtered candidate set, as a ``PLT_CN`` string.

    Ordered by ``BALIVE`` then ``PLT_CN`` (string, never numeric — PLT_CN is 15 digits and
    rounding it silently corrupts the join), taking the lower median on an even count.
    Deterministic and reproducible from the FIA database alone. Raises when the candidate
    pool is below `selection_rule.candidate_pool.min_candidates`, because a "median plot"
    drawn from a handful of plots is one arbitrary plot wearing a rule.
    """
    import pandas as pd

    policy = policy or load_fallback_policy()
    minimum = policy["selection_rule"]["candidate_pool"]["min_candidates"]
    if len(candidates) < minimum:
        raise ValueError(
            f"only {len(candidates)} candidate plots (minimum {minimum}); "
            f"widen the slot filter or the candidate pool rather than lowering the floor"
        )

    df = candidates.copy()
    df["PLT_CN"] = df["PLT_CN"].astype(str)
    df["_balive"] = pd.to_numeric(df["BALIVE"], errors="coerce")
    df = df.dropna(subset=["_balive"])
    if len(df) < minimum:
        raise ValueError(f"only {len(df)} candidates have a usable BALIVE (minimum {minimum})")

    df = df.sort_values(["_balive", "PLT_CN"], kind="stable").reset_index(drop=True)
    return str(df.loc[(len(df) - 1) // 2, "PLT_CN"])


# ---- resolver -------------------------------------------------------------------------

def resolve_all_slots(plots, policy: dict | None = None) -> dict:
    """
    Pin every slot from a candidate plot table. Returns the lock-file body.

    ``plots`` is the FIA plot-level table for the candidate pool (``PLT_CN, FORTYPCD,
    STDORGCD, STDAGE, BALIVE``). Any slot that cannot resolve raises — a partially pinned
    lock file would be worse than none.
    """
    policy = policy or load_fallback_policy()
    resolved = {}
    for slot, spec in policy["slots"].items():
        candidates = filter_candidates(plots, spec["filter"])
        plt_cn = select_donor_plot(candidates, policy)
        row = candidates[candidates["PLT_CN"].astype(str) == plt_cn].iloc[0]
        resolved[slot] = {
            "plt_cn": plt_cn,
            "n_candidates": int(len(candidates)),
            "fortypcd": int(float(row["FORTYPCD"])),
            "balive": float(row["BALIVE"]),
        }
        logger.info("slot %s → PLT_CN %s (from %d candidates)", slot, plt_cn, len(candidates))
    return {
        "version": policy["version"],
        "selection_rule": policy["selection_rule"]["method"],
        "slots": resolved,
    }


def _load_fia_candidates(fia_db: Path, policy: dict):
    """
    Query the candidate plot pool from FIA `COND` / `PLOT`.

    `COND` is the authoritative source for the four fields the slot filters use —
    ``FORTYPCD``, ``STDORGCD``, ``STDAGE``, ``BALIVE`` (live basal area per acre). The pool
    is restricted to accessible forest land (``COND_STATUS_CD = 1``) on essentially
    single-condition plots (``CONDPROP_UNADJ >= 0.95``), so a plot's condition attributes
    describe the whole plot, and to plots that actually have an FVS-ready tree list.

    Duplicate remeasurements are collapsed to the most recent ``INVYR`` per plot location
    in pandas rather than in SQL — the correlated subquery version of that filter is slow
    on the full CONUS database.
    """
    import sqlite3

    import pandas as pd

    pool = policy["selection_rule"]["candidate_pool"]
    state_codes = [int(s) for s in pool["states"]]
    placeholders = ",".join("?" for _ in state_codes)
    query = f"""
        SELECT c.PLT_CN      AS PLT_CN,
               c.FORTYPCD    AS FORTYPCD,
               c.STDORGCD    AS STDORGCD,
               c.STDAGE      AS STDAGE,
               c.BALIVE      AS BALIVE,
               p.INVYR       AS INVYR,
               p.STATECD     AS STATECD,
               p.UNITCD      AS UNITCD,
               p.COUNTYCD    AS COUNTYCD,
               p.PLOT        AS PLOT
        FROM COND c
        JOIN PLOT p ON c.PLT_CN = p.CN
        WHERE p.STATECD IN ({placeholders})
          AND c.COND_STATUS_CD = 1
          AND c.CONDPROP_UNADJ >= 0.95
          AND c.PLT_CN IN (SELECT STAND_CN FROM FVS_STANDINIT_PLOT)
    """
    with sqlite3.connect(f"file:{fia_db}?mode=ro", uri=True) as con:
        df = pd.read_sql_query(query, con, params=state_codes)

    df["PLT_CN"] = df["PLT_CN"].astype(str)
    location = ["STATECD", "UNITCD", "COUNTYCD", "PLOT"]
    df = (
        df.sort_values([*location, "INVYR"], kind="stable")
        .drop_duplicates(subset=location, keep="last")
        .reset_index(drop=True)
    )
    logger.info("Candidate pool: %d plots from states %s", len(df), state_codes)
    return df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="ARTEMIS fixed fallback tree lists")
    parser.add_argument("--resolve", action="store_true",
                        help="Pin each slot's donor PLT_CN and write the lock file")
    parser.add_argument("--fia-db", type=Path, default=None,
                        help="FIA SQLite path (default: config/data_paths.yaml)")
    parser.add_argument("--candidates-csv", type=Path, default=None,
                        help="Resolve from a candidate CSV instead of the FIA DB")
    args = parser.parse_args()

    policy = load_fallback_policy()

    if not args.resolve:
        lock = load_lock(policy)
        print(f"fallback_treelists.yaml v{policy['version']} status={policy['status']}")
        for slot, spec in policy["slots"].items():
            pinned = (lock or {}).get("slots", {}).get(slot, {}).get("plt_cn", "UNRESOLVED")
            print(f"  {slot:<34} use={spec['use']:<14} plt_cn={pinned}")
        return

    import pandas as pd

    if args.candidates_csv:
        plots = pd.read_csv(args.candidates_csv, dtype={"PLT_CN": str})
    else:
        fia_db = args.fia_db
        if fia_db is None:
            with open(_REPO_ROOT / "config" / "data_paths.yaml") as f:
                fia_db = Path(yaml.safe_load(f)["raw"]["fia_sqlite"]["db"])
        if not Path(fia_db).exists():
            raise SystemExit(
                f"FIA SQLite not found at {fia_db} — mount the data drive or pass "
                f"--candidates-csv. See config/data_paths.yaml."
            )
        plots = _load_fia_candidates(Path(fia_db), policy)

    lock_body = resolve_all_slots(plots, policy)
    lock_path = _REPO_ROOT / policy["lock_file"]
    with open(lock_path, "w") as f:
        yaml.safe_dump(lock_body, f, sort_keys=False)
    logger.info("Wrote %s — now set `status: resolved` in %s", lock_path, CONFIG_PATH.name)


if __name__ == "__main__":
    main()
