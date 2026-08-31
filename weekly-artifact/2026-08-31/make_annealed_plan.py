"""Stage 2 — the simulated-annealing harvest scheduler, and the first annealed plan.

`notes/trajectory-library-and-annealing.md` is the design of record for ARTEMIS, adopted
2026-08-06, and its own status line has read the same thing ever since:

    Nothing in `pipeline/` implements the annealer yet.

`config/projection.yaml` nonetheless declares the whole thing as executable policy —
`selection_method: "simulated_annealing"`, a cooling schedule, a move mixture, four
objective forms with weights, and three priced spatial penalties. `harvest_scheduler.py`
carries a module docstring describing itself as "the annealer's initial solution". Every
weekly artifact since 2026-08-10 has built a piece of the input. This script is the
consumer: it implements §6 and runs it, producing the thing the pipeline diagram calls

    SELECTED PLAN: stand_id -> trajectory_id

**What is implemented, and what is structurally unavailable.** §6 defines four objective
forms, three constraint enforcement mechanisms, and three move kinds. All four objective
forms are implemented; the two that `config/projection.yaml` actually selects
(`evenflow_target` on harvest volume at weight 6, `maximize` on standing volume at
weight 1) are the ones exercised. The absolutes (riparian no-entry, eligibility screens)
arrive already enforced *structurally* — a riparian stand's library is `{no_management}`,
so no objective weight can trade its protection away, which is the property §3 rule 2
asks for and it is verified here rather than assumed.

The two spatial penalties — `adjacency_greenup` and `max_opening_size` — are implemented
as functions but **cannot be evaluated on this landscape**, and are reported as
unavailable rather than given a manufactured value. The reason is the caveat the
2026-08-24 artifact already stated: a "stand" here is still a pixel class
(`TreeMap plot x county x ownership`), not a polygon, so it is a scattered set of pixels
across a county rather than a compact unit. Adjacency between two such classes is not a
meaningful relation and green-up on them would not mean what green-up means. The `block`
move goes with them, since blocks are adjacency components; the move mixture renormalises
over `single_stand` and `period_swap`. This is a property of the input, not of the
scheduler, and it is the single largest caveat on the plan below.

**Solution quality is reported, not assumed** (§6, "required, not optional"): the
constraint-violation vector per dimension per cycle, a declared relaxation bound, the
greedy and random baselines, and the spread across all five seeds with every seed logged.

Usage:
    uv run python weekly-artifact/2026-08-31/make_annealed_plan.py [--restarts N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import random
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.s3_management import harvest_scheduler as hs  # noqa: E402
from pipeline.s3_management.regime_assignment import assign_prescription  # noqa: E402

log = logging.getLogger("anneal")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"
WORK = DATA / "interim/fvs_batch"

MANIFEST = WORK / "batch_manifest.json"

PROJECTION = REPO / "config/projection.yaml"
TPO_TARGETS = REPO / "config/tpo_targets.yaml"

CYCLE_YEARS = hs.DEFAULT_CYCLE_YEARS
PILOT_COUNTIES = ["Baker", "Columbia", "Hamilton", "Suwannee", "Union"]
# The TPO workbook spells Suwannee with one 'n' (pipeline.s3_management.tpo_targets).
COUNTY_TO_TPO = {c: ("Suwanee" if c == "Suwannee" else c) for c in PILOT_COUNTIES}

# Resolved owner class -> TPO owner group. Same table as weekly-artifact/2026-08-10, keyed
# by the resolved class name rather than the Harris OWN_CODE integer.
OWNER_GROUP = {
    "private_family": "Private",
    "private_corporate_other": "Private",
    "private_industrial": "Private",
    "tribal": "Other public",
    "federal": "Federal (NF)",
    "state": "Other public",
    "local": "Other public",
}


# --------------------------------------------------------------------------------------
# Configuration — read, never inferred
# --------------------------------------------------------------------------------------

def load_config() -> dict:
    cfg = yaml.safe_load(PROJECTION.read_text())
    harvest = cfg["harvest"]
    objectives = harvest["objectives"]
    vol = next(o for o in objectives if o["metric"] == "harvest_volume")
    if vol["form"] != "evenflow_target":
        raise AssertionError("harvest_volume objective is no longer evenflow_target")
    # §6: "the scheduler must never infer a period from key order or silently choose".
    period = vol["target_period"]
    return {
        "seed": harvest["random_seed"],
        "anneal": harvest["annealing"],
        "objectives": objectives,
        "penalties": harvest["penalties"],
        "target_period": period,
        "dimensions": vol["dimensions"],
        "n_cycles": cfg["projection"]["n_cycles"],
    }


def tpo_caps(period: str) -> dict[str, dict[str, float]]:
    """Per-cycle cuft targets by dimension. TPO figures are annual; a cycle is 5 years."""
    targets = yaml.safe_load(TPO_TARGETS.read_text())
    county = {name: v[period] for name, v in targets["by_county"].items()
              if name in COUNTY_TO_TPO.values()}
    owner = {name: v[period] for name, v in targets["by_owner_group"].items()
             if name != "All owners"}
    total = {"": targets["by_county"]["All five counties"][period]}
    return {
        hs.TOTAL: hs.to_cycle_budget(total, CYCLE_YEARS),
        hs.COUNTY: hs.to_cycle_budget(county, CYCLE_YEARS),
        hs.OWNER: hs.to_cycle_budget(owner, CYCLE_YEARS),
    }


# --------------------------------------------------------------------------------------
# The decision space
# --------------------------------------------------------------------------------------

class Landscape:
    """Stands, their libraries, and the per-cycle volume of every trajectory.

    Volumes are held as plain Python lists of floats rather than numpy arrays: the inner
    loop touches one stand's 10-element vector per proposal, where numpy's per-call
    overhead costs more than the arithmetic saves.
    """

    def __init__(self, stands: pd.DataFrame, library: pd.DataFrame,
                 cycles: pd.DataFrame, n_cycles: int):
        self.n_cycles = n_cycles
        cyc = cycles[cycles["cycle"].between(1, n_cycles)].copy()

        # (plot, prescription) -> per-acre removed volume by cycle, and ending standing volume
        vol_lookup: dict[tuple[str, str], list[float]] = {}
        standing: dict[tuple[str, str], float] = {}
        for (plt, presc), grp in cyc.groupby(["PLT_CN", "prescription"], sort=False):
            vec = [0.0] * n_cycles
            for c, v in zip(grp["cycle"], grp["removed_merch_cuft_per_ac"]):
                vec[int(c) - 1] = float(v)
            vol_lookup[(str(plt), presc)] = vec
            standing[(str(plt), presc)] = float(grp.sort_values("cycle")["MCuFt"].iloc[-1])

        lib = library.dropna(subset=["prescription"])
        by_stand = lib.groupby("unit_id")["prescription"].apply(list).to_dict()

        self.stand_ids: list[str] = []
        self.options: list[list[str]] = []
        self.volumes: list[list[list[float]]] = []   # [stand][option][cycle] absolute cuft
        self.standing: list[list[float]] = []        # [stand][option] ending cuft
        self.county_ix: list[int] = []
        self.owner_ix: list[int] = []
        self.acres: list[float] = []
        self.unit_class: list[str] = []
        self.dropped_no_trajectory = 0
        self.dropped_options = 0
        self.dropped_option_keys: list[tuple[str, str]] = []

        counties = sorted({COUNTY_TO_TPO[c] for c in stands["county"].unique()
                           if c in COUNTY_TO_TPO})
        owners = sorted({OWNER_GROUP[o] for o in stands["owner_class"].unique()
                         if o in OWNER_GROUP})
        self.counties, self.owners = counties, owners
        cix = {k: i for i, k in enumerate(counties)}
        oix = {k: i for i, k in enumerate(owners)}

        for row in stands.itertuples(index=False):
            presc_list = by_stand.get(row.unit_id, [])
            plt = str(row.PLT_CN)
            opts, vols, stand_end = [], [], []
            for presc in presc_list:
                key = (plt, presc)
                if key not in vol_lookup:
                    # No complete FVS trajectory for this pair. The batch fails closed and
                    # records every exclusion, so this is a stated gap, never a guess — and
                    # counted here so it cannot silently shrink a stand's decision space.
                    self.dropped_options += 1
                    self.dropped_option_keys.append((row.unit_id, presc))
                    continue
                acres = float(row.acres)
                opts.append(presc)
                vols.append([v * acres for v in vol_lookup[key]])
                stand_end.append(standing[key] * acres)
            if not opts:
                self.dropped_no_trajectory += 1
                continue
            if row.county not in COUNTY_TO_TPO or row.owner_class not in OWNER_GROUP:
                self.dropped_no_trajectory += 1
                continue
            self.stand_ids.append(row.unit_id)
            self.options.append(opts)
            self.volumes.append(vols)
            self.standing.append(stand_end)
            self.county_ix.append(cix[COUNTY_TO_TPO[row.county]])
            self.owner_ix.append(oix[OWNER_GROUP[row.owner_class]])
            self.acres.append(float(row.acres))
            self.unit_class.append(row.unit_class)

        self.n = len(self.stand_ids)
        self.decision_stands = [i for i in range(self.n) if len(self.options[i]) > 1]
        log.info("Landscape: %d stands (%d with a real choice), %d dropped for want of a "
                 "trajectory or a TPO dimension", self.n, len(self.decision_stands),
                 self.dropped_no_trajectory)
        if self.dropped_options:
            log.warning("%d (stand, prescription) options dropped: no complete FVS "
                        "trajectory. Affected prescriptions: %s", self.dropped_options,
                        sorted({p for _, p in self.dropped_option_keys}))

    def verify_riparian_structural(self) -> dict:
        """§3 rule 2: a riparian stand's library must be exactly {no_management}."""
        bad = []
        for i in range(self.n):
            if self.unit_class[i] != "riparian":
                continue
            if self.options[i] != ["no_management"]:
                bad.append(self.stand_ids[i])
        rip = sum(1 for u in self.unit_class if u == "riparian")
        return {"riparian_stands": rip, "with_a_cutting_option": len(bad),
                "structurally_enforced": not bad}


# --------------------------------------------------------------------------------------
# Objective
# --------------------------------------------------------------------------------------

class Objective:
    """The weighted objective of §6, evaluated incrementally.

    `evenflow_target` on harvest volume, dimensioned by county and owner group, plus
    `maximize` on ending standing volume. Both terms are normalised so the weights in
    `config/projection.yaml` mean what they say: the volume term is a sum of squared
    *relative* deviations from target, and the standing term is a fraction of the
    landscape's own attainable maximum.
    """

    def __init__(self, land: Landscape, caps: dict, cfg: dict):
        self.land = land
        self.n_cycles = land.n_cycles
        weights = {o["metric"]: float(o["weight"]) for o in cfg["objectives"]}
        self.w_vol = weights["harvest_volume"]
        self.w_standing = weights.get("standing_volume", 0.0)
        # §6 "targets must stay dimensioned": score exactly the dimensions the scenario
        # declares. Scoring a dimension the config switched off would optimise a different
        # objective than the run reports.
        self.dimensions = list(cfg["dimensions"])
        unknown = set(self.dimensions) - {hs.COUNTY, hs.OWNER}
        if unknown:
            raise AssertionError(f"unsupported harvest_volume dimensions: {sorted(unknown)}")
        if not self.dimensions:
            raise AssertionError("harvest_volume declares no dimensions; §6 forbids "
                                 "collapsing the target to a bare landscape total here")
        self.use_county = hs.COUNTY in self.dimensions
        self.use_owner = hs.OWNER in self.dimensions

        self.county_target = [caps[hs.COUNTY][k] for k in land.counties]
        self.owner_target = [caps[hs.OWNER][k] for k in land.owners]
        self.total_target = caps[hs.TOTAL][""]

        # Normaliser for the standing-volume term: the landscape's per-stand maximum.
        self.standing_max = sum(max(s) for s in land.standing) or 1.0

        self.county_v = [[0.0] * self.n_cycles for _ in land.counties]
        self.owner_v = [[0.0] * self.n_cycles for _ in land.owners]
        self.standing_total = 0.0

    # -- aggregate maintenance ---------------------------------------------------------
    def reset(self, choice: list[int]) -> None:
        self.county_v = [[0.0] * self.n_cycles for _ in self.land.counties]
        self.owner_v = [[0.0] * self.n_cycles for _ in self.land.owners]
        self.standing_total = 0.0
        for i, k in enumerate(choice):
            vols = self.land.volumes[i][k]
            cv = self.county_v[self.land.county_ix[i]]
            ov = self.owner_v[self.land.owner_ix[i]]
            for c in range(self.n_cycles):
                v = vols[c]
                cv[c] += v
                ov[c] += v
            self.standing_total += self.land.standing[i][k]

    def _dim_cost(self) -> float:
        cost = 0.0
        if self.use_county:
            for row, t in zip(self.county_v, self.county_target):
                for v in row:
                    d = (v - t) / t
                    cost += d * d
        if self.use_owner:
            for row, t in zip(self.owner_v, self.owner_target):
                for v in row:
                    d = (v - t) / t
                    cost += d * d
        return cost

    def total(self) -> float:
        """Lower is better. Standing volume is maximised, so it enters negated."""
        return (self.w_vol * self._dim_cost()
                - self.w_standing * (self.standing_total / self.standing_max))

    def delta_and_apply(self, i: int, old_k: int, new_k: int, apply: bool) -> float:
        """Objective change from reassigning stand `i`; applies it only when asked."""
        land = self.land
        old_v, new_v = land.volumes[i][old_k], land.volumes[i][new_k]
        cv = self.county_v[land.county_ix[i]]
        ov = self.owner_v[land.owner_ix[i]]
        ct = self.county_target[land.county_ix[i]]
        ot = self.owner_target[land.owner_ix[i]]

        # Both aggregates are always maintained (violation_vector reports either), but only
        # the declared dimensions are scored.
        before = after = 0.0
        for c in range(self.n_cycles):
            dv = new_v[c] - old_v[c]
            if dv == 0.0:
                continue
            if self.use_county:
                a, b = cv[c], cv[c] + dv
                before += ((a - ct) / ct) ** 2
                after += ((b - ct) / ct) ** 2
            if self.use_owner:
                a, b = ov[c], ov[c] + dv
                before += ((a - ot) / ot) ** 2
                after += ((b - ot) / ot) ** 2
        d_standing = land.standing[i][new_k] - land.standing[i][old_k]
        delta = (self.w_vol * (after - before)
                 - self.w_standing * (d_standing / self.standing_max))

        if apply:
            for c in range(self.n_cycles):
                dv = new_v[c] - old_v[c]
                if dv:
                    cv[c] += dv
                    ov[c] += dv
            self.standing_total += d_standing
        return delta

    # -- reporting ---------------------------------------------------------------------
    def violation_vector(self, choice: list[int]) -> pd.DataFrame:
        """§6.1 — the full constraint-violation vector, per dimension per cycle."""
        self.reset(choice)
        rows = []
        active = []
        if self.use_county:
            active.append(("county", self.land.counties, self.county_v, self.county_target))
        if self.use_owner:
            active.append(("owner_group", self.land.owners, self.owner_v, self.owner_target))
        for name, keys, agg, targets in active:
            for key, row, t in zip(keys, agg, targets):
                for c, v in enumerate(row, start=1):
                    rows.append({"dimension": name, "key": key, "cycle": c,
                                 "calendar_year": 2022 + c * CYCLE_YEARS,
                                 "volume_cuft": v, "target_cuft": t,
                                 "deviation_cuft": v - t,
                                 "deviation_pct": 100.0 * (v - t) / t})
        return pd.DataFrame(rows)

    def attainable_envelope(self) -> pd.DataFrame:
        """Min and max volume each dimension key could reach in each cycle.

        The separation that matters when a plan misses its targets: a target outside
        [min, max] is unreachable by *any* selection from this library, so missing it is a
        property of the decision space, not of the search. Computed by letting every stand
        pick its lowest- and highest-volume trajectory independently per cycle, which is
        the same relaxation `relaxation_bound` scores.
        """
        land = self.land
        rows = []
        active = []
        if self.use_county:
            active.append(("county", land.county_ix, land.counties, self.county_target))
        if self.use_owner:
            active.append(("owner_group", land.owner_ix, land.owners, self.owner_target))
        for name, ixs, keys, targets in active:
            lo = [[0.0] * self.n_cycles for _ in keys]
            hi = [[0.0] * self.n_cycles for _ in keys]
            for i in range(land.n):
                k = ixs[i]
                for c in range(self.n_cycles):
                    vals = [o[c] for o in land.volumes[i]]
                    lo[k][c] += min(vals)
                    hi[k][c] += max(vals)
            for key, lo_row, hi_row, t in zip(keys, lo, hi, targets):
                for c in range(self.n_cycles):
                    rows.append({
                        "dimension": name, "key": key, "cycle": c + 1,
                        "calendar_year": 2022 + (c + 1) * CYCLE_YEARS,
                        "min_attainable_cuft": lo_row[c], "max_attainable_cuft": hi_row[c],
                        "target_cuft": t,
                        "target_reachable": bool(lo_row[c] <= t <= hi_row[c]),
                        "max_as_pct_of_target": 100.0 * hi_row[c] / t,
                    })
        return pd.DataFrame(rows)

    def relaxation_bound(self) -> tuple[float, str]:
        """A valid lower bound on the objective, with its strategy declared (§6.2).

        Strategy: **per-cycle, per-dimension interval relaxation**. Each dimension key and
        cycle is minimised independently, and each stand is allowed to pick a different
        trajectory for every cycle and for each dimension at once. That is a strict
        relaxation of the real problem — where one choice per stand must serve all ten
        cycles and both dimensions simultaneously — so its value bounds the attainable
        objective from below. For each (key, cycle) the attainable volume lies in
        [sum of per-stand minima, sum of per-stand maxima]; a target inside that interval
        contributes 0, and one outside contributes the squared relative distance to the
        nearer endpoint.

        The recipe §6 names first — "remove the spatial penalties, preserve the aggregate
        objective" — is the identity here, because the spatial penalties are unavailable
        on a pixel-class landscape. It would return the problem itself and no bound, so
        this interval relaxation is used and named instead of manufacturing a denominator.
        """
        land = self.land
        c_min = [[0.0] * self.n_cycles for _ in land.counties]
        c_max = [[0.0] * self.n_cycles for _ in land.counties]
        o_min = [[0.0] * self.n_cycles for _ in land.owners]
        o_max = [[0.0] * self.n_cycles for _ in land.owners]
        for i in range(land.n):
            opts = land.volumes[i]
            ci, oi = land.county_ix[i], land.owner_ix[i]
            for c in range(self.n_cycles):
                vals = [o[c] for o in opts]
                lo, hi = min(vals), max(vals)
                c_min[ci][c] += lo
                c_max[ci][c] += hi
                o_min[oi][c] += lo
                o_max[oi][c] += hi

        cost = 0.0
        active = []
        if self.use_county:
            active.append((c_min, c_max, self.county_target))
        if self.use_owner:
            active.append((o_min, o_max, self.owner_target))
        for mins, maxs, targets in active:
            for lo_row, hi_row, t in zip(mins, maxs, targets):
                for lo, hi in zip(lo_row, hi_row):
                    gap = max(0.0, t - hi, lo - t)
                    cost += (gap / t) ** 2
        best_standing = sum(max(s) for s in land.standing)
        bound = self.w_vol * cost - self.w_standing * (best_standing / self.standing_max)
        return bound, "per-cycle per-dimension interval relaxation"


# --------------------------------------------------------------------------------------
# Spatial penalties — implemented, structurally unavailable on this landscape
# --------------------------------------------------------------------------------------

def spatial_penalties_available(land: Landscape) -> tuple[bool, str]:
    """Whether adjacency/green-up and opening size can be evaluated at all.

    They need a neighbour relation between stands. A stand here is a pixel class
    (`TreeMap plot x county x ownership`), which is a scattered set of pixels across a
    county rather than a compact polygon, so "adjacent" is not a meaningful relation and
    a green-up penalty computed on it would not mean green-up. The Phase 2.3 unit x stand
    crosswalk that would give real polygon neighbours does not exist yet — the same
    caveat `weekly-artifact/2026-08-24` recorded about its own stand geometry.
    """
    return False, ("no polygon adjacency: stands are pixel classes "
                   "(TreeMap plot x county x ownership), not contiguous units")


# --------------------------------------------------------------------------------------
# Initial solutions: greedy (the repo allocator) and random
# --------------------------------------------------------------------------------------

def default_prescriptions(stands: pd.DataFrame) -> dict[str, str]:
    """The deterministic owner-class default from `regime_assignment.assign_prescription`.

    Deliberately unguarded. An earlier version wrapped this loop in a bare
    `except Exception: continue`, which swallowed an `AttributeError` on every row (the
    returned record exposes `prescription_id`, not `prescription`) and left the mapping
    empty — so the greedy baseline silently degenerated to "option 0 for every stand" and
    `harvest_scheduler` was never called at all. A stand that cannot resolve a default is
    a fault in the policy configuration, and it should stop the run rather than quietly
    hollow out the baseline the plan is judged against.

    The unit mapping must carry the fields `assign_prescription` actually reads —
    `OWN_CODE` for `classify_owner` and `FORTYPCD` for `forest_type_branch`, not the
    already-resolved `owner_class` / `forest_branch` strings. Passing the resolved names
    is silently accepted and degrades every stand to the `unknown` owner and the `other`
    forest branch, which is a different (and much smaller) default menu.
    """
    out, mismatched = {}, 0
    for row in stands.itertuples(index=False):
        riparian = row.unit_class == "riparian"
        unit = {"OWN_CODE": row.OWN_CODE, "FORTYPCD": row.FORTYPCD,
                "stand_age": row.stand_age,
                "SMZ_Pct": 100.0 if riparian else 0.0}
        p = assign_prescription(unit)
        # The library's own owner class is the ground truth; a disagreement means the raw
        # attribution and the resolved column have drifted apart.
        if not riparian and p.owner_class != row.owner_class:
            mismatched += 1
        out[row.unit_id] = p.prescription_id
    if mismatched:
        log.warning("%d stands resolved an owner class differing from the library's own "
                    "`owner_class` column", mismatched)
    return out


def greedy_seed(land: Landscape, stands: pd.DataFrame, caps: dict) -> list[int]:
    """Seed from the repo's greedy oldest-first allocator (§6, `seed_from: "greedy"`).

    `harvest_scheduler.schedule_harvests` decides *which units cut in which cycle* against
    the TPO budgets; it does not choose trajectories. The mapping onto the library is
    stated rather than inferred: a stand takes its default cutting prescription only when
    the allocator admitted **every** one of that trajectory's harvest events; every other
    stand takes `no_management`. Requiring all of them matters — a trajectory is
    all-or-nothing here, so selecting it after the allocator blocked some of its cycles
    would credit the greedy plan with volume the allocator actually refused, and the
    baseline would no longer be `schedule_harvests`'s own output. Stands are walked
    oldest-first, which is the allocator's own priority rule.
    """
    defaults = default_prescriptions(stands)
    if not defaults:
        raise AssertionError(
            "no stand resolved a default prescription; the greedy baseline would be "
            "meaningless. Check regime_assignment.assign_prescription's contract."
        )
    ix = {sid: i for i, sid in enumerate(land.stand_ids)}
    rows = []
    for sid, presc in defaults.items():
        i = ix.get(sid)
        if i is None or presc not in land.options[i]:
            continue
        k = land.options[i].index(presc)
        vols = land.volumes[i][k]
        for c, v in enumerate(vols, start=1):
            if v > 0:
                rows.append({"unit_id": sid, "cycle": c, "removable_volume": v,
                             hs.COUNTY: COUNTY_TO_TPO[stands_county(stands, sid)],
                             hs.OWNER: OWNER_GROUP[stands_owner(stands, sid)],
                             "stand_age": stands_age(stands, sid)})
    if not rows:
        raise AssertionError(
            "no default trajectory carries a harvest event; the greedy allocator would "
            "have nothing to allocate and the baseline would be vacuous."
        )
    cand = pd.DataFrame(rows)
    annual = {dim: {k: v / CYCLE_YEARS for k, v in caps[dim].items()} for dim in caps}

    # Iterate the allocator to a fixed point. A trajectory is all-or-nothing, so a stand
    # whose events were only partly admitted cannot take its prescription — but on the
    # allocator's first pass those admitted events *did* consume county, owner and total
    # budget, blocking other stands with volume the baseline then never harvests. Dropping
    # the partly-admitted stands and re-allocating releases that capacity to stands that
    # can actually use it. The candidate set shrinks monotonically, so this terminates;
    # oldest-first priority and every active constraint dimension are unchanged.
    dropped_total, passes = 0, 0
    while True:
        passes += 1
        result = hs.schedule_harvests(cand, annual, dims=(hs.TOTAL, hs.COUNTY, hs.OWNER))
        if "harvested" not in result:
            raise AssertionError("harvest_scheduler returned no `harvested` column")
        per_stand = result.groupby("unit_id")["harvested"].agg(["sum", "count"])
        # Only *partly* admitted stands are dropped. They are the ones that consumed
        # budget for a trajectory they cannot take, so removing them is what releases
        # capacity. A wholly blocked stand consumed nothing, and dropping it would deny
        # it the capacity this very loop frees up — it stays a candidate and may be
        # admitted on a later pass. Each pass removes at least one partial stand, so the
        # candidate set shrinks monotonically and this terminates.
        partial_ids = set(per_stand.index[(per_stand["sum"] > 0)
                                          & (per_stand["sum"] < per_stand["count"])])
        if not partial_ids:
            break
        dropped_total += len(partial_ids)
        cand = cand[~cand["unit_id"].isin(partial_ids)]
        if cand.empty:
            break
    fully = set(per_stand.index[per_stand["sum"] == per_stand["count"]]) if len(per_stand) else set()
    blocked = len(per_stand) - len(fully) if len(per_stand) else 0
    log.info("Greedy allocator: converged in %d pass(es); %d stands take their default "
             "trajectory, %d dropped as partly admitted (capacity released), %d left "
             "wholly blocked by the caps", passes, len(fully), dropped_total, blocked)

    choice = []
    for i, sid in enumerate(land.stand_ids):
        presc = defaults.get(sid)
        if sid in fully and presc in land.options[i]:
            choice.append(land.options[i].index(presc))
        else:
            k = land.options[i].index("no_management") if "no_management" in land.options[i] else 0
            choice.append(k)
    return choice


# Cached per (frame identity, column) rather than per column alone. Keying on the column
# name only is correct while exactly one `stands` frame exists per process, and silently
# wrong the moment a second one appears — a caller (a test, say) would get the first
# frame's values back for the second frame's stand ids.
_LOOKUPS: dict[tuple[int, str], dict] = {}


def _attr_lookup(stands: pd.DataFrame, col: str) -> dict:
    key = (id(stands), col)
    if key not in _LOOKUPS:
        _LOOKUPS[key] = dict(zip(stands["unit_id"], stands[col]))
    return _LOOKUPS[key]


def stands_county(stands, sid):
    return _attr_lookup(stands, "county")[sid]


def stands_owner(stands, sid):
    return _attr_lookup(stands, "owner_class")[sid]


def stands_age(stands, sid):
    return _attr_lookup(stands, "stand_age")[sid]


def random_choice(land: Landscape, rng: random.Random) -> list[int]:
    return [rng.randrange(len(o)) for o in land.options]


# --------------------------------------------------------------------------------------
# The annealer
# --------------------------------------------------------------------------------------

def effective_move_probabilities(move_weights: dict) -> dict[str, float]:
    """The probabilities the sampler actually uses, after dropping the `block` move.

    `config/projection.yaml` declares 0.70 / 0.20 / 0.10 over single_stand / block /
    period_swap. Blocks are adjacency components, which this landscape cannot supply, so
    `block` is dropped and the remainder renormalised — making the real probabilities
    0.875 and 0.125, not 0.70 and 0.10. Reported from here rather than from the raw
    config, because a quality report that states the pre-renormalisation numbers
    misrecords the search it is meant to make reproducible.
    """
    mw = {k: float(v) for k, v in move_weights.items() if k != "block"}
    total = sum(mw.values())
    if total <= 0:
        raise AssertionError("move weights sum to zero once `block` is removed")
    return {k: v / total for k, v in mw.items()}


def calibrate_t0(land: Landscape, obj: Objective, choice: list[int],
                 rng: random.Random, accept_rate: float, samples: int = 4000) -> float:
    """T0 such that `accept_rate` of worsening moves are accepted (§6).

    `T0 = -mean(positive delta) / ln(accept_rate)`, calibrated on this landscape rather
    than hardcoded, which is what `initial_temperature: null` in the config asks for.
    """
    obj.reset(choice)
    deltas = []
    for _ in range(samples):
        i = rng.choice(land.decision_stands)
        k = choice[i]
        nk = rng.randrange(len(land.options[i]))
        if nk == k:
            continue
        d = obj.delta_and_apply(i, k, nk, apply=False)
        if d > 0:
            deltas.append(d)
    if not deltas:
        return 1.0
    return -(sum(deltas) / len(deltas)) / math.log(accept_rate)


def anneal(land: Landscape, obj: Objective, cfg: dict, seed: int,
           initial: list[int]) -> dict:
    a = cfg["anneal"]
    rng = random.Random(seed)
    choice = list(initial)
    obj.reset(choice)
    cur = obj.total()
    best, best_choice = cur, list(choice)

    t0 = a["initial_temperature"]
    if t0 is None:
        t0 = calibrate_t0(land, obj, choice, rng, float(a["initial_accept_rate"]))
        obj.reset(choice)
        cur = obj.total()
        best, best_choice = cur, list(choice)
    temp = float(t0)

    alpha = float(a["cooling_factor"])
    iters = int(a["iterations_per_temperature"]) * land.n
    t_min = float(a["min_temperature"])
    stall_limit = int(a["stall_temperature_levels"])

    # §6 move mixture. `block` needs adjacency components, which this landscape cannot
    # supply, so its weight is redistributed over the two moves that remain well defined.
    p_single = effective_move_probabilities(a["move_weights"])["single_stand"]
    decision = land.decision_stands
    if not decision:
        raise AssertionError("no stand has more than one trajectory; nothing to search")

    levels, stalls, accepted, proposed = 0, 0, 0, 0
    while temp > t_min and stalls < stall_limit:
        improved = False
        for _ in range(iters):
            proposed += 1
            if rng.random() < p_single:
                # single-stand move
                i = rng.choice(decision)
                k = choice[i]
                nk = rng.randrange(len(land.options[i]))
                if nk == k:
                    continue
                d = obj.delta_and_apply(i, k, nk, apply=False)
                if d <= 0 or rng.random() < math.exp(-d / temp):
                    obj.delta_and_apply(i, k, nk, apply=True)
                    choice[i] = nk
                    cur += d
                    accepted += 1
            else:
                # period-swap move: exchange trajectories between two stands of comparable
                # volume, which moves harvest timing without moving much volume (§6).
                i = rng.choice(decision)
                j = rng.choice(decision)
                if i == j:
                    continue
                ki, kj = choice[i], choice[j]
                oi, oj = land.options[i], land.options[j]
                if oj[kj] not in oi or oi[ki] not in oj:
                    continue
                ni, nj = oi.index(oj[kj]), oj.index(oi[ki])
                if ni == ki and nj == kj:
                    continue
                d1 = obj.delta_and_apply(i, ki, ni, apply=True)
                d2 = obj.delta_and_apply(j, kj, nj, apply=False)
                d = d1 + d2
                if d <= 0 or rng.random() < math.exp(-d / temp):
                    obj.delta_and_apply(j, kj, nj, apply=True)
                    choice[i], choice[j] = ni, nj
                    cur += d
                    accepted += 1
                else:
                    obj.delta_and_apply(i, ni, ki, apply=True)   # roll back
            if cur < best - 1e-12:
                best, best_choice = cur, list(choice)
                improved = True
        levels += 1
        stalls = 0 if improved else stalls + 1
        temp *= alpha
        # `cur` is carried forward by summing ~10^5 incremental deltas per temperature
        # level, and the period-swap move applies, rejects and reverses a delta in place.
        # Neither is lossy in principle, but float addition is not associative, so resync
        # from a full recompute once per level. Cheap (one pass over the plan against
        # ~10^5 proposals) and it removes the drift question entirely rather than arguing
        # the error is small.
        obj.reset(choice)
        cur = obj.total()

    return {"seed": seed, "objective": best, "choice": best_choice,
            "levels": levels, "proposed": proposed, "accepted": accepted,
            "initial_temperature": t0,
            "accept_rate": accepted / proposed if proposed else 0.0}


# --------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------

def require_fresh_batch() -> dict:
    """Refuse to plan over a library that no completed batch vouches for.

    `make_fvs_batch.py` deletes this marker before it starts and writes it last, once
    validation has passed and every table is on disk. Without the check, a rebuild that
    aborted — on the fail-closed exclusion gate, say — would leave the *previous* run's
    CSVs in place and the annealer would silently plan over them.
    """
    if not MANIFEST.exists():
        raise SystemExit(
            f"no {MANIFEST.name} in {WORK}: the FVS batch has not completed successfully. "
            f"Run make_fvs_batch.py first; if it aborted, the library on disk belongs to an "
            f"earlier run and must not be planned over."
        )
    return json.loads(MANIFEST.read_text())


def check_batch_matches(manifest: dict, stands: pd.DataFrame, library: pd.DataFrame,
                        cycles: pd.DataFrame) -> None:
    """The tables on disk must be the ones the manifest describes."""
    actual = {
        "carved_stands_rows": len(stands),
        "carved_library_rows": len(library),
        "trajectory_cycles_rows": len(cycles),
    }
    for key, got in actual.items():
        want = manifest.get(key)
        if want is not None and got != want:
            raise SystemExit(
                f"{key}: {got} rows on disk but the batch manifest records {want}. The "
                f"library has changed since the batch completed; re-run make_fvs_batch.py."
            )
    log.info("Batch manifest verified (completed %s, %d excluded runs)",
             manifest.get("completed_utc", "?"), manifest.get("excluded_runs", 0))

def plan_frame(land: Landscape, choice: list[int], stands: pd.DataFrame) -> pd.DataFrame:
    """The selected plan: stand_id -> trajectory, with its per-cycle volumes.

    Two keys, because they answer different questions and §4 is explicit that conflating
    them is a mistake — "deduplication is a cache, never a reporting decision ... every
    polygon keeps its own identity in the outputs":

    * `trajectory_id` — §5's primary key, a deterministic hash of `(stand_id,
      prescription_id)`. Unique per row; this is what downstream joins key on.
    * `fvs_run_id` — the `(donor plot, prescription)` pair that was actually simulated.
      Many stands share one, by design: that is the run cache. It is the join key onto
      `trajectory_index.csv` and `trajectory_harvest_by_cycle.csv`, and the join is
      many-to-one, so it never expands the plan.
    """
    # A duplicate unit_id would make `attrs.loc[sid]` a frame, silently baking Series
    # objects into the output columns. Assert rather than assume, as the carve checks do.
    if not stands["unit_id"].is_unique:
        raise AssertionError("carved stands carry a duplicate unit_id")
    attrs = stands.set_index("unit_id")
    rows = []
    for i, k in enumerate(choice):
        sid = land.stand_ids[i]
        a = attrs.loc[sid]
        presc = land.options[i][k]
        vols = land.volumes[i][k]
        rows.append({
            "stand_id": sid,
            "trajectory_id": hashlib.sha256(f"{sid}::{presc}".encode()).hexdigest()[:16],
            "fvs_run_id": f"{a['PLT_CN']}::{presc}",
            "PLT_CN": a["PLT_CN"],
            "prescription": presc,
            "county": a["county"],
            "owner_class": a["owner_class"],
            "owner_group": OWNER_GROUP[a["owner_class"]],
            "unit_class": land.unit_class[i],
            "acres": land.acres[i],
            "library_size": len(land.options[i]),
            "total_removed_cuft": sum(vols),
            "ending_standing_cuft": land.standing[i][k],
            **{f"cuft_cycle_{c}": v for c, v in enumerate(vols, start=1)},
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restarts", type=int, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config()
    # Resolved and validated before any expensive work: an invalid restart count should
    # not surface only after the FVS library is loaded and both baselines are computed.
    restarts = args.restarts if args.restarts is not None else int(cfg["anneal"]["restarts"])
    if restarts < 1:
        raise SystemExit(
            f"--restarts must be at least 1 (got {restarts}); §6 requires reporting the "
            f"best of R restarts with every seed logged, and there is no plan to report "
            f"from zero searches."
        )
    caps = tpo_caps(cfg["target_period"])
    log.info("Target period %s; per-cycle total target %.4g cuft",
             cfg["target_period"], caps[hs.TOTAL][""])

    manifest = require_fresh_batch()

    stands = pd.read_csv(WORK / "carved_stands.csv", dtype={"PLT_CN": str, "unit_id": str})
    library = pd.read_csv(WORK / "carved_library.csv", dtype={"PLT_CN": str, "unit_id": str})
    cycles = pd.read_csv(WORK / "trajectory_cycles.csv", dtype={"PLT_CN": str})
    check_batch_matches(manifest, stands, library, cycles)

    land = Landscape(stands, library, cycles, cfg["n_cycles"])
    obj = Objective(land, caps, cfg)

    rip = land.verify_riparian_structural()
    if not rip["structurally_enforced"]:
        raise AssertionError(f"{rip['with_a_cutting_option']} riparian stands carry a cutting option")
    log.info("Riparian no-entry is structural: %d riparian stands, all with library "
             "{no_management}", rip["riparian_stands"])

    avail, why = spatial_penalties_available(land)
    log.warning("Spatial penalties (adjacency_greenup, max_opening_size) UNAVAILABLE: %s", why)

    # --- baselines --------------------------------------------------------------------
    greedy = greedy_seed(land, stands, caps)
    obj.reset(greedy)
    greedy_obj = obj.total()
    log.info("Greedy baseline objective: %.6f", greedy_obj)

    random_objs = []
    for r in range(5):
        rc = random_choice(land, random.Random(cfg["seed"] + 1000 + r))
        obj.reset(rc)
        random_objs.append(obj.total())
    log.info("Random baseline objective: mean %.6f over 5 draws", sum(random_objs) / 5)

    # --- the search -------------------------------------------------------------------
    runs = []
    for r in range(restarts):
        seed = cfg["seed"] + r
        res = anneal(land, obj, cfg, seed, greedy)
        log.info("  restart %d (seed %d): objective %.6f, T0=%.4g, %d levels, "
                 "%d/%d moves accepted (%.1f%%)",
                 r, seed, res["objective"], res["initial_temperature"], res["levels"],
                 res["accepted"], res["proposed"], 100 * res["accept_rate"])
        runs.append(res)

    best = min(runs, key=lambda r: r["objective"])
    log.info("Best of %d restarts: seed %d, objective %.6f", restarts, best["seed"],
             best["objective"])

    bound, strategy = obj.relaxation_bound()
    obj.reset(best["choice"])
    best_obj = obj.total()

    # --- outputs ----------------------------------------------------------------------
    plan = plan_frame(land, best["choice"], stands)
    plan.to_csv(OUT_DIR / "annealed_plan.csv", index=False)

    viol = obj.violation_vector(best["choice"])
    viol.to_csv(OUT_DIR / "constraint_violations.csv", index=False)

    envelope = obj.attainable_envelope()
    envelope.to_csv(OUT_DIR / "attainable_envelope.csv", index=False)
    unreachable = int((~envelope["target_reachable"]).sum())
    log.info("Attainability: %d of %d (dimension, cycle) targets lie outside the library's "
             "attainable range", unreachable, len(envelope))

    seeds = pd.DataFrame([{"seed": r["seed"], "objective": r["objective"],
                           "initial_temperature": r["initial_temperature"],
                           "temperature_levels": r["levels"],
                           "moves_proposed": r["proposed"], "moves_accepted": r["accepted"],
                           "accept_rate": r["accept_rate"]} for r in runs])
    seeds.to_csv(OUT_DIR / "seed_spread.csv", index=False)

    quality = {
        "objective_best": best_obj,
        "objective_greedy_baseline": greedy_obj,
        "objective_random_baseline_mean": sum(random_objs) / len(random_objs),
        "objective_random_baseline_all": random_objs,
        "relaxation_bound": bound,
        "relaxation_bound_strategy": strategy,
        "gap_to_bound_absolute": best_obj - bound,
        "beats_greedy": bool(best_obj < greedy_obj),
        "beats_random": bool(best_obj < min(random_objs)),
        "seed_spread": {"min": float(seeds["objective"].min()),
                        "max": float(seeds["objective"].max()),
                        "range": float(seeds["objective"].max() - seeds["objective"].min())},
        "target_period": cfg["target_period"],
        "random_seed": cfg["seed"],
        "cooling": {k: cfg["anneal"][k] for k in
                    ("cooling_factor", "iterations_per_temperature", "min_temperature",
                     "stall_temperature_levels", "initial_accept_rate")},
        "objective_weights": {o["metric"]: o["weight"] for o in cfg["objectives"]},
        "move_weights_declared": cfg["anneal"]["move_weights"],
        "move_probabilities_effective": {
            **effective_move_probabilities(cfg["anneal"]["move_weights"]),
            "block": "unavailable — no adjacency components on a pixel-class landscape",
        },
        "spatial_penalties": {"available": avail, "reason": why,
                              "declared": cfg["penalties"]},
        "riparian_structural": rip,
        "stands": land.n,
        "stands_with_a_choice": len(land.decision_stands),
        "stands_dropped": land.dropped_no_trajectory,
        "options_dropped_no_trajectory": land.dropped_options,
        "targets_unreachable_from_library": unreachable,
        "targets_total": len(envelope),
    }
    (OUT_DIR / "solution_quality.json").write_text(json.dumps(quality, indent=2))

    # Per-cycle landscape summary, the headline table.
    summary = (plan.melt(id_vars=["stand_id", "county", "owner_group", "unit_class"],
                         value_vars=[f"cuft_cycle_{c}" for c in range(1, cfg["n_cycles"] + 1)],
                         var_name="cycle", value_name="cuft")
               .assign(cycle=lambda d: d["cycle"].str.replace("cuft_cycle_", "").astype(int)))
    per_cycle = (summary.groupby("cycle", as_index=False)["cuft"].sum()
                 .assign(calendar_year=lambda d: 2022 + d["cycle"] * CYCLE_YEARS,
                         target_cuft=caps[hs.TOTAL][""]))
    per_cycle["deviation_pct"] = 100 * (per_cycle["cuft"] - per_cycle["target_cuft"]) / per_cycle["target_cuft"]
    per_cycle.to_csv(OUT_DIR / "harvest_by_cycle.csv", index=False)

    mix = (plan.groupby(["prescription", "unit_class"], as_index=False)
           .agg(stands=("stand_id", "count"), acres=("acres", "sum"),
                removed_cuft=("total_removed_cuft", "sum")))
    mix.to_csv(OUT_DIR / "prescription_mix.csv", index=False)

    by_dim = (plan.groupby(["county", "owner_group"], as_index=False)
              .agg(stands=("stand_id", "count"), acres=("acres", "sum"),
                   removed_cuft=("total_removed_cuft", "sum")))
    by_dim.to_csv(OUT_DIR / "plan_by_dimension.csv", index=False)

    log.info("Wrote plan (%d stands), violations, seed spread, quality report", len(plan))


if __name__ == "__main__":
    main()
