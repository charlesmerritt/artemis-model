"""Stage 2 — the annealed plan over a decision space that finally carries timing.

`weekly-artifact/2026-08-31` produced ARTEMIS's first annealed plan and, with it, the finding
that mattered more than the plan: **47 of the 80 (dimension x cycle) targets were provably
unreachable from the library at any selection.** Not missed by the search — outside the range
the decision space can reach, by construction. Its diagnosis was timing, and
`notes/trajectory-library-and-annealing.md` §4 had said the same thing first: Diaz et al.
offered delayed activity starts *specifically* so the optimizer could choose both what and
when, and ARTEMIS offered almost no when.

`make_timing_library.py` in this directory built the when — four timing variants of every
cutting prescription, and the eleventh FVS cycle without which the horizon's final year
cannot be harvested at all. This script re-runs the §6 scheduler over that library, with
**every other input held fixed**: the same carved landscape, the same
`config/projection.yaml` weights and cooling schedule, the same `config/tpo_targets.yaml`
caps, the same five seeds, the same greedy initial solution from the repository's own
`harvest_scheduler.py`. The only thing that changed is the menu each stand chooses from, so
the difference between the two plans is attributable to the decision space rather than to
the search.

It writes `comparison_to_20260831.csv` for exactly that reason: the objective, the
unreachable-target count, and the per-cycle harvest of both plans side by side, read from
last week's committed artifact rather than from memory.

What is implemented and what is structurally unavailable is unchanged from 2026-08-31, and
so is the honesty about it. All four §6 objective forms are implemented; the two
`config/projection.yaml` selects are the ones exercised. The absolutes arrive enforced
*structurally* — a riparian stand's library is `{no_management}`, and this artifact gives
that claim a harder test than last week's did: the timing grid multiplied every other menu
by four and left the riparian menus at exactly one option, which the driver asserts. The two
spatial penalties — `adjacency_greenup` and `max_opening_size` — still cannot be evaluated
here, because a "stand" is still a pixel class rather than a polygon, and they are reported
as unavailable rather than given a manufactured number. That remains the single largest
caveat on the plan, and it is untouched by this week's work.

Usage:
    uv run python weekly-artifact/2026-09-14/make_annealed_plan.py [--restarts N]
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.s3_management import harvest_scheduler as hs  # noqa: E402

log = logging.getLogger("anneal")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"
WORK = DATA / "interim/timing_library"

MANIFEST = WORK / "batch_manifest.json"

PROJECTION = REPO / "config/projection.yaml"
TPO_TARGETS = REPO / "config/tpo_targets.yaml"

# Last week's committed artifact, read for the side-by-side comparison. Nothing here is
# recomputed from it: these are the numbers that artifact published.
PREV = REPO / "weekly-artifact/2026-08-31"
PREV_QUALITY = PREV / "solution_quality.json"
PREV_ENVELOPE = PREV / "attainable_envelope.csv"
PREV_BY_CYCLE = PREV / "harvest_by_cycle.csv"
PREV_PLAN_MIX = PREV / "prescription_mix.csv"

# `{prescription}@+{offset}` — the timing-variant id `make_timing_library.py` writes.
OFFSET_SEP = "@+"

CYCLE_YEARS = hs.DEFAULT_CYCLE_YEARS
# The TreeMap 2022 imputation anchor every cycle label is measured from.
INV_YEAR = 2022

PILOT_COUNTIES = ["Baker", "Columbia", "Hamilton", "Suwannee", "Union"]
# The TPO workbook spells Suwannee with one 'n' (pipeline.s3_management.tpo_targets).
COUNTY_TO_TPO = {c: ("Suwanee" if c == "Suwannee" else c) for c in PILOT_COUNTIES}

# The selector itself lives in pipeline.s3_management.annealer, promoted from this file.
from pipeline.s3_management.annealer import (  # noqa: E402,F401
    LEGACY_OWNER_CLASSES,
    Landscape,
    Objective,
    anneal,
    base_of,
    calibrate_t0,
    default_prescriptions,
    default_variant,
    effective_move_probabilities,
    evenflow_cost,
    greedy_seed,
    load_config,
    owner_class_in_policy,
    owner_group,
    plan_frame,
    random_choice,
    spatial_penalties_available,
    stands_age,
    stands_county,
    stands_owner,
    tpo_caps,
)


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
                        cycles: pd.DataFrame, cfg: dict) -> None:
    """The tables on disk must be the ones the manifest describes — and describe the same
    horizon the scheduler is about to score them over.

    Row counts alone establish that nobody rebuilt the tables; they say nothing about how
    those rows are to be *read*. The scheduler sizes every trajectory vector from
    `projection.n_cycles` as configured **now**, while the library was built and validated
    against the horizon recorded in the manifest, so a configuration edit between the two
    silently reinterprets a library that still matches on every count.

    That is not a hypothetical rounding of the last cycle here. `trajectory_cycles.csv`
    deliberately carries an eleventh cycle — 2077, the carrier that lets a 2072 entry execute
    — kept out of the objective by scoring ten. Raise `n_cycles` to 11 against this same
    library and `Landscape`'s `between(1, n_cycles)` pulls that carrier in, and out-of-horizon
    harvest enters the objective as though it were part of the plan. The whole terminal-cycle
    design rests on those two numbers agreeing, so they are checked rather than assumed.
    """
    actual = {
        "carved_stands_rows": len(stands),
        "expanded_library_rows": len(library),
        "trajectory_cycles_rows": len(cycles),
    }
    for key, got in actual.items():
        want = manifest.get(key)
        if want is not None and got != want:
            raise SystemExit(
                f"{key}: {got} rows on disk but the batch manifest records {want}. The "
                f"library has changed since the batch completed; re-run make_timing_library.py."
            )

    scored = manifest.get("objective_cycles")
    if scored is not None and scored != cfg["n_cycles"]:
        raise SystemExit(
            f"the batch was built and validated over {scored} objective cycles but "
            f"config/projection.yaml now declares n_cycles = {cfg['n_cycles']}. The library "
            f"on disk carries {manifest.get('num_cycle')} cycles including the carrier, so "
            f"scoring it over a different horizon would either truncate every trajectory or "
            f"read out-of-horizon harvest as part of the plan. Re-run make_timing_library.py "
            f"against the current configuration."
        )
    inv_year, last_entry = manifest.get("inv_year"), manifest.get("last_entry_year")
    if inv_year is not None and inv_year != INV_YEAR:
        raise SystemExit(
            f"the batch is anchored at inventory year {inv_year} but this driver labels "
            f"cycles from {INV_YEAR}; every calendar year it reports would be wrong."
        )
    if None not in (inv_year, last_entry, scored) \
            and last_entry != inv_year + scored * CYCLE_YEARS:
        raise SystemExit(
            f"the batch's horizon does not close: {inv_year} + {scored} x {CYCLE_YEARS} != "
            f"{last_entry}. Its cycle length and this driver's disagree."
        )
    log.info("Batch manifest verified (completed %s, %d excluded runs, %d objective cycles "
             "of %d run)", manifest.get("completed_utc", "?"),
             manifest.get("excluded_runs", 0), scored, manifest.get("num_cycle", 0))

def compare_to_previous(quality: dict, envelope: pd.DataFrame, per_cycle: pd.DataFrame,
                        viol: pd.DataFrame) -> pd.DataFrame:
    """This plan beside 2026-08-31's, on the measures the timing grid was meant to move.

    Every "before" figure is read from that artifact's own committed files — its
    `solution_quality.json`, `attainable_envelope.csv` and `harvest_by_cycle.csv` — rather
    than recomputed here or quoted from its prose, so the comparison cannot drift from what
    was published. The two runs share the landscape, the targets, the weights, the cooling
    schedule and the seeds; only the decision space differs.

    The objective values are comparable for the same reason: both are the §6 objective with
    the weights `config/projection.yaml` declares, over the same 80 (dimension, cycle)
    targets. A bigger menu cannot make the objective worse — last week's trajectories are
    all still in this library at offset 0 — so the comparison is a floor on what timing
    bought, not a like-for-like coin flip.
    """
    prev_q = json.loads(PREV_QUALITY.read_text())
    prev_env = pd.read_csv(PREV_ENVELOPE)
    prev_cyc = pd.read_csv(PREV_BY_CYCLE)

    rows = [
        {"measure": "even-flow cost (sum of squared relative deviations; exactly comparable)",
         "value_20260831": evenflow_cost(pd.read_csv(PREV / "constraint_violations.csv")),
         "value_20260914": evenflow_cost(viol)},
        {"measure": "objective (best of 5 seeds, lower is better)",
         "value_20260831": prev_q["objective_best"], "value_20260914": quality["objective_best"]},
        {"measure": "objective, greedy baseline",
         "value_20260831": prev_q["objective_greedy_baseline"],
         "value_20260914": quality["objective_greedy_baseline"]},
        {"measure": "objective, random baseline (mean of 5)",
         "value_20260831": prev_q["objective_random_baseline_mean"],
         "value_20260914": quality["objective_random_baseline_mean"]},
        {"measure": "relaxation bound",
         "value_20260831": prev_q["relaxation_bound"],
         "value_20260914": quality["relaxation_bound"]},
        {"measure": "targets proven unreachable (of 80)",
         "value_20260831": prev_q["targets_unreachable_from_library"],
         "value_20260914": quality["targets_unreachable_from_library"]},
        {"measure": "stands with more than one trajectory",
         "value_20260831": prev_q["stands_with_a_choice"],
         "value_20260914": quality["stands_with_a_choice"]},
        {"measure": "seed spread (max - min objective)",
         "value_20260831": prev_q["seed_spread"]["range"],
         "value_20260914": quality["seed_spread"]["range"]},
    ]
    for c in range(1, 11):
        prev_v = float(prev_cyc.loc[prev_cyc["cycle"] == c, "cuft"].iloc[0])
        now_v = float(per_cycle.loc[per_cycle["cycle"] == c, "cuft"].iloc[0])
        rows.append({"measure": f"harvest volume, cycle {c} ({INV_YEAR + CYCLE_YEARS * c}), cuft",
                     "value_20260831": prev_v, "value_20260914": now_v})
    for c in range(1, 11):
        pv = int((~prev_env.loc[prev_env["cycle"] == c, "target_within_envelope"]).sum())
        nv = int((~envelope.loc[envelope["cycle"] == c, "target_within_envelope"]).sum())
        rows.append({"measure": f"targets proven unreachable, cycle {c} (of 8)",
                     "value_20260831": pv, "value_20260914": nv})

    out = pd.DataFrame(rows)
    out["change"] = out["value_20260914"] - out["value_20260831"]
    return out


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
    library = pd.read_csv(WORK / "expanded_library.csv",
                          dtype={"PLT_CN": str, "unit_id": str})
    cycles = pd.read_csv(WORK / "trajectory_cycles.csv", dtype={"PLT_CN": str})
    check_batch_matches(manifest, stands, library, cycles, cfg)

    land = Landscape(stands, library, cycles, cfg["n_cycles"], counties=caps[hs.COUNTY].keys())
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
    unreachable = int((~envelope["target_within_envelope"]).sum())
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
        "targets_unreachable_is_a_proof": True,
        "targets_within_envelope_note": (
            "membership in the relaxed envelope does not prove a target attainable — the "
            "choices are discrete and the relaxation lets a stand vary per cycle and per "
            "dimension; only the unreachable count is a proof"
        ),
        "objective_evenflow_term": evenflow_cost(viol),
        "objective_comparability_note": (
            "`objective_evenflow_term` is the weight-6 even-flow cost and is exactly "
            "comparable across libraries. The total objective's second term "
            "(standing_volume, weight 1) is normalised by this library's own attainable "
            "maximum, which a larger menu can raise, so totals from two different libraries "
            "differ by up to the standing weight for reasons of denominator rather than plan"
        ),
        "timing_grid": {
            "offsets_years": manifest.get("offsets_years"),
            "num_cycle_run": manifest.get("num_cycle"),
            "objective_cycles": manifest.get("objective_cycles"),
            "last_entry_year": manifest.get("last_entry_year"),
            "library_rows": int(len(library)),
            "library_rows_pre_expansion": manifest.get("carved_library_rows_pre_expansion"),
            "fvs_runs": manifest.get("trajectory_index_rows"),
            "options_per_stand": {str(k): int(v) for k, v in
                                  sorted(pd.Series([len(o) for o in land.options])
                                         .value_counts().items())},
            "base_library_check": manifest.get("base_library_check"),
        },
    }
    (OUT_DIR / "solution_quality.json").write_text(json.dumps(quality, indent=2))

    # Per-cycle landscape summary, the headline table.
    summary = (plan.melt(id_vars=["stand_id", "county", "owner_group", "unit_class"],
                         value_vars=[f"cuft_cycle_{c}" for c in range(1, cfg["n_cycles"] + 1)],
                         var_name="cycle", value_name="cuft")
               .assign(cycle=lambda d: d["cycle"].str.replace("cuft_cycle_", "").astype(int)))
    per_cycle = (summary.groupby("cycle", as_index=False)["cuft"].sum()
                 .assign(calendar_year=lambda d: INV_YEAR + d["cycle"] * CYCLE_YEARS,
                         target_cuft=caps[hs.TOTAL][""]))
    per_cycle["deviation_pct"] = 100 * (per_cycle["cuft"] - per_cycle["target_cuft"]) / per_cycle["target_cuft"]
    per_cycle.to_csv(OUT_DIR / "harvest_by_cycle.csv", index=False)

    # The comparison the artifact is for. Written after `quality`, from `quality`, so the
    # two files cannot disagree about this run.
    comparison = compare_to_previous(quality, envelope, per_cycle, viol)
    comparison.to_csv(OUT_DIR / "comparison_to_20260831.csv", index=False)
    prev_q = json.loads(PREV_QUALITY.read_text())
    log.info("Against 2026-08-31: objective %.3f -> %.3f; targets proven unreachable "
             "%d -> %d of 80", prev_q["objective_best"], best_obj,
             prev_q["targets_unreachable_from_library"], unreachable)

    mix = (plan.groupby(["prescription", "base_prescription", "timing_offset_years",
                         "unit_class"], as_index=False)
           .agg(stands=("stand_id", "count"), acres=("acres", "sum"),
                removed_cuft=("total_removed_cuft", "sum")))
    mix.to_csv(OUT_DIR / "prescription_mix.csv", index=False)

    # What the scheduler did with the new axis: how much of the landscape it chose to delay.
    timing = (plan[plan["base_prescription"] != "no_management"]
              .groupby("timing_offset_years", as_index=False)
              .agg(stands=("stand_id", "count"), acres=("acres", "sum"),
                   removed_cuft=("total_removed_cuft", "sum")))
    timing.to_csv(OUT_DIR / "timing_offsets_chosen.csv", index=False)
    log.info("Timing offsets chosen (cutting stands only):\n%s", timing.to_string(index=False))

    by_dim = (plan.groupby(["county", "owner_group"], as_index=False)
              .agg(stands=("stand_id", "count"), acres=("acres", "sum"),
                   removed_cuft=("total_removed_cuft", "sum")))
    by_dim.to_csv(OUT_DIR / "plan_by_dimension.csv", index=False)

    log.info("Wrote plan (%d stands), violations, seed spread, quality report", len(plan))


if __name__ == "__main__":
    main()
