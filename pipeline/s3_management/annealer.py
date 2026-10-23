"""
Simulated-annealing trajectory selection: one precomputed FVS trajectory per stand.

The search of `docs/architecture/presentation.html#search`. A :class:`Landscape` holds every
stand's eligible trajectories and their per-cycle removed volume; an :class:`Objective` scores a
plan against the TPO even-flow targets (`config/tpo_targets.yaml`) by county and owner group,
plus ending standing volume (`config/projection.yaml` ``harvest``); :func:`anneal` searches,
seeded by :func:`greedy_seed` (`harvest_scheduler`'s oldest-first allocator).

Promoted from `weekly-artifact/2026-09-14/make_annealed_plan.py`, which the 2026-09-28,
2026-10-08, 2026-10-12 and 2026-10-19 schedules all loaded by path. The one change is that
counties are no longer the five-county pilot's hardcoded list: every county
`config/tpo_targets.yaml` carries is a target, and :func:`tpo_county` applies the workbook's
spelling.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from collections.abc import Collection
from pathlib import Path

import pandas as pd
import yaml

from pipeline.s3_management import harvest_scheduler as hs
from pipeline.s3_management.owner_classes import load_ownership_policy, tpo_group_for
from pipeline.s3_management.regime_assignment import assign_prescription

log = logging.getLogger("anneal")

REPO = Path(__file__).resolve().parents[2]
PROJECTION = REPO / "config/projection.yaml"
TPO_TARGETS = REPO / "config/tpo_targets.yaml"

# `{prescription}@+{offset}` — the timing-variant id the trajectory library writes.
OFFSET_SEP = "@+"

CYCLE_YEARS = hs.DEFAULT_CYCLE_YEARS
# The TreeMap 2022 imputation anchor every cycle label is measured from.
INV_YEAR = 2022
OWNERSHIP_POLICY = load_ownership_policy()

# The TPO workbook spells Suwannee with one 'n' (pipeline.s3_management.tpo_targets).
TPO_COUNTY_SPELLING = {"Suwannee": "Suwanee"}


def tpo_county(county: str) -> str:
    """A county's name as `config/tpo_targets.yaml` spells it."""
    return TPO_COUNTY_SPELLING.get(county, county)


# Owner class -> TPO owner group. Read from `config/ownership_policy.yaml` through the
# repository's own `tpo_group_for`, rather than the hardcoded table this series carried from
# weekly-artifact/2026-08-10 onwards. That table had drifted from the config it was meant to
# mirror: it mapped `tribal` to "Other public" where the policy says "Private" (FIA places
# Native American land in owner group 40), and omitted `unknown` entirely, which meant an
# unknown-ownership stand was silently dropped from the landscape rather than budgeted. The
# five-county pilot carries neither class — `OWN_CODE` is only ever 3, 4, 6, 7 or 8 here — so
# neither error reached a published number, and the five classes that do appear resolve
# identically under the table and under the policy. Reading the policy removes the copy that
# can drift instead of correcting it once.
#
# LEGACY_OWNER_CLASSES is the other half. The committed 2026-08-17 library — a frozen,
# dated artifact — records owner classes in the vocabulary of its own run. When ownership is
# ported to exactly the seven Harris et al. (2025) RDS-2025-0045 classes, the policy's keys
# change under it and those recorded names no longer resolve. The alias table is consulted
# only when a name is absent from the current policy, so this driver reads the frozen library
# correctly under both vocabularies and needs no rewrite of a dated record.
LEGACY_OWNER_CLASSES = {
    "private_family": "family",              # Harris 3
    "private_industrial": "corporate",       # Harris 4
    "private_corporate_other": "corporate",  # the parcel refinement of Harris 4, now gone
}


def owner_class_in_policy(owner_class: str) -> str:
    """The name the *current* ownership policy uses for this class."""
    if owner_class in OWNERSHIP_POLICY["classes"]:
        return owner_class
    return LEGACY_OWNER_CLASSES.get(owner_class, owner_class)


def owner_group(owner_class: str) -> str:
    """The TPO owner group this class is budgeted against.

    Raises rather than returning `None` for a class the policy does not define. The previous
    hardcoded table was consulted with `in`, so an unrecognised class quietly removed its
    stand from the landscape — a stand that exists, carries acres, and has trajectories,
    dropped from the plan because a lookup table was missing a row. A class nobody can
    resolve is a fault in the configuration, and it should stop the run.
    """
    resolved = owner_class_in_policy(owner_class)
    group = tpo_group_for(resolved, OWNERSHIP_POLICY)
    if group is None:
        raise AssertionError(
            f"owner class {owner_class!r} (resolved to {resolved!r}) has no TPO group in "
            f"config/ownership_policy.yaml; its stands cannot be budgeted against a target. "
            f"Policy classes: {sorted(OWNERSHIP_POLICY['classes'])}"
        )
    return group


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
    # The workbook's all-counties row ("All five counties" for the pilot) is the total.
    totals = [name for name in targets["by_county"] if name.startswith("All ")]
    if len(totals) != 1:
        raise AssertionError(f"expected one all-counties row in {TPO_TARGETS.name}, got {totals}")
    county = {name: v[period] for name, v in targets["by_county"].items() if name != totals[0]}
    owner = {name: v[period] for name, v in targets["by_owner_group"].items()
             if name != "All owners"}
    total = {"": targets["by_county"][totals[0]][period]}
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
                 cycles: pd.DataFrame, n_cycles: int, counties: Collection[str] | None = None):
        """``counties`` are the TPO-spelled counties with a target; a stand elsewhere is
        dropped and counted. ``None`` keeps every county the stands carry."""
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

        targeted = (set(counties) if counties is not None
                    else {tpo_county(c) for c in stands["county"].unique()})
        counties = sorted({tpo_county(c) for c in stands["county"].unique()} & targeted)
        owners = sorted({owner_group(o) for o in stands["owner_class"].unique()})
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
            # `owner_group` raises for a class the policy cannot resolve, rather than
            # dropping the stand: only an unmapped *county* is a silent exclusion here.
            if tpo_county(row.county) not in cix:
                self.dropped_no_trajectory += 1
                continue
            self.stand_ids.append(row.unit_id)
            self.options.append(opts)
            self.volumes.append(vols)
            self.standing.append(stand_end)
            self.county_ix.append(cix[tpo_county(row.county)])
            self.owner_ix.append(oix[owner_group(row.owner_class)])
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
                                 "calendar_year": INV_YEAR + c * CYCLE_YEARS,
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

        **The bound is one-directional, and the column name says so.** Because the interval
        comes from a relaxation — each stand free to choose per cycle and per dimension,
        which the real problem forbids — and because the choices are discrete, membership
        proves nothing: the attainable set inside [min, max] has gaps, and a target sitting
        in one of them has no plan that hits it. So `target_within_envelope = False` is a
        proof of unreachability, while `True` means only "not proven unreachable". The
        headline count this artifact reports is the `False` side, which is the sound one.
        Deciding attainability exactly is a subset-sum problem per (dimension, cycle) and
        is not attempted here rather than approximated and labelled as fact.
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
                        "calendar_year": INV_YEAR + (c + 1) * CYCLE_YEARS,
                        "min_attainable_cuft": lo_row[c], "max_attainable_cuft": hi_row[c],
                        "target_cuft": t,
                        "target_within_envelope": bool(lo_row[c] <= t <= hi_row[c]),
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
        # attribution and the resolved column have drifted apart. Both sides are put into the
        # current policy's vocabulary first: `assign_prescription` speaks whatever ownership
        # is configured today, while the recorded column speaks the frozen 2026-08-17
        # library's vocabulary, and comparing those directly would report every private stand
        # as a mismatch the moment the classes are renamed — a warning about a rename rather
        # than about the attribution drift this check exists to catch.
        if not riparian and owner_class_in_policy(p.owner_class) != \
                owner_class_in_policy(row.owner_class):
            mismatched += 1
        out[row.unit_id] = p.prescription_id
    if mismatched:
        log.warning("%d stands resolved an owner class differing from the library's own "
                    "`owner_class` column", mismatched)
    return out


def base_of(variant: str) -> tuple[str, int]:
    """`"public_thin_restore@+10"` -> `("public_thin_restore", 10)`. Unoffset ids give 0."""
    if OFFSET_SEP not in variant:
        return variant, 0
    base, off = variant.rsplit(OFFSET_SEP, 1)
    return base, int(off)


def default_variant(base: str, options: list[str]) -> str | None:
    """The timing variant of `base` that carries the *deterministic* entry years.

    `assign_prescription` names a prescription, not a timing: it resolves entry years from
    stand age and from the fixed offsets in `config/management_regimes.yaml`, which is
    exactly the `@+0` variant. So the greedy baseline plans over the same trajectories it
    planned over last week, and the timing grid is available to the annealer alone. Giving
    the baseline a free choice of timing would make it a different algorithm and the
    week-on-week comparison meaningless.

    Returns `None` when this stand's menu does not contain that variant at all — which
    happens when the deterministic timing collapses to no entry inside the horizon.
    """
    if base in options:            # no_management carries no timing variants
        return base
    v = f"{base}{OFFSET_SEP}0"
    return v if v in options else None


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
    for sid, base in defaults.items():
        i = ix.get(sid)
        if i is None:
            continue
        presc = default_variant(base, land.options[i])
        if presc is None:
            continue
        k = land.options[i].index(presc)
        vols = land.volumes[i][k]
        for c, v in enumerate(vols, start=1):
            if v > 0:
                rows.append({"unit_id": sid, "cycle": c, "removable_volume": v,
                             hs.COUNTY: tpo_county(stands_county(stands, sid)),
                             hs.OWNER: owner_group(stands_owner(stands, sid)),
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
        base = defaults.get(sid)
        presc = default_variant(base, land.options[i]) if base is not None else None
        if sid in fully and presc is not None:
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
        base, offset = base_of(presc)
        rows.append({
            "stand_id": sid,
            "trajectory_id": hashlib.sha256(f"{sid}::{presc}".encode()).hexdigest()[:16],
            "fvs_run_id": f"{a['PLT_CN']}::{presc}",
            "PLT_CN": a["PLT_CN"],
            "prescription": presc,
            "base_prescription": base,
            "timing_offset_years": offset,
            "county": a["county"],
            "owner_class": a["owner_class"],
            "owner_group": owner_group(a["owner_class"]),
            "unit_class": land.unit_class[i],
            "acres": land.acres[i],
            "library_size": len(land.options[i]),
            "total_removed_cuft": sum(vols),
            "ending_standing_cuft": land.standing[i][k],
            **{f"cuft_cycle_{c}": v for c, v in enumerate(vols, start=1)},
        })
    return pd.DataFrame(rows)


def evenflow_cost(viol: pd.DataFrame) -> float:
    """The §6 `evenflow_target` term itself: the sum of squared relative deviations.

    This is the objective's weight-6 term, and the one number that is **exactly** comparable
    between two different libraries. The total objective is not, quite: its second term
    (`standing_volume`, weight 1) is normalised by `sum(max(standing))` — the landscape's own
    attainable maximum — which a larger menu can raise, so the same plan can score a slightly
    different total under two libraries. The effect is bounded by the standing weight (1.0 in
    absolute objective units, against totals of order 100), but it is a real difference in the
    denominator rather than in the plan, and it is why the comparison reports this term beside
    the totals instead of only the totals.

    Computed from a committed violation vector, so it can be recovered for either week
    without re-running anything.
    """
    d = (viol["volume_cuft"] - viol["target_cuft"]) / viol["target_cuft"]
    return float((d ** 2).sum())
