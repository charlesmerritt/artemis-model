# Design sketch: ARTEMIS orchestrator (bundle-per-ownership, even-flow)

**Date:** 2026-07-17
**Status:** Sketch — depends on an unproven mechanism (see Gate). Not yet a buildable spec.
**Branch:** `claude-code/parallel-fvs-runs`
**Predecessors:** `2026-07-16-parallel-fvs-runs-design.md` (spike),
`notes/restart-fidelity-findings.md` (measured results).

## Goal

Project the AOI under iterative coupling where each worker owns a **bundle of stands grouped
by ownership type** (federal, state, tribal, local, and private classes from the Harris 2025
raster, per `config/projection.yaml`). The objective is to **optimize harvest with respect to
even flow per ownership type** — a smooth, non-declining harvest volume within each owner across
the horizon.

## Why bundle-per-ownership is the right decomposition

Even flow is a constraint **within** an ownership group (federal harvest period t ≈ t+1) and
**independent across** groups (federal's target is unrelated to state's). This maps exactly onto
the parallelism the spike proved:

- **Across owners** → embarrassingly parallel. One worker/process per ownership class, run
  concurrently. Validated: `research/restart_fidelity/parallel_demo.py` ran 5 isolated
  concurrent FVS processes, each correct and bit-identical to sequential.
- **Within an owner** → stands are coupled (they must sum to a smooth flow), so the bundle needs
  a synchronized barrier where all its stands are visible at once for the harvest allocation.

## The within-bundle even-flow loop

```
per ownership bundle (independent, concurrent):
  segment: run ALL bundle stands to barrier t  (--stoppoint, store every stand in ONE restart file)
      -> arm N proved this round-trips exactly on stand values
  gather:  read all stands' state at t via DuckDB over the bundle's FVSOut.db
  solve:   even-flow harvest allocation for THIS owner (which stands to cut, how much)
  apply:   restart, inject per-stand cuts at stop point 2 (fvsCutNow), advance to t+1
      -> NOT YET PROVEN. This is the gate below.
```

The restart file is what **synchronizes** stands at a common year: FVS processes one stand fully
before the next, so within a single process stands are *not* aligned in time — the stop/restart
barrier is what stores every stand at year t so the bundle can be seen as of t.

**Carbon being out of scope is what makes this safe.** The spike found restart corrupts FFE
carbon but preserves stand values (BA/Tpa/SDI/volume) exactly. Even-flow optimizes harvest
volume, so restart is faithful for everything this objective touches. `carbon_extension` stays
`false` (enforced by the config tripwire test).

## Confirmed decisions (2026-07-17)

1. **Even-flow enforcement: rolling horizon (MPC-style).** At each barrier, project the bundle
   forward a few periods with a cheap no-cut lookahead (a throwaway FVS projection, or FVSjl),
   solve a short harvest-scheduling LP, apply only the first period's cuts, then re-solve at the
   next barrier. Handles even flow properly and adapts to realized growth. Rejected: myopic fixed
   target (can't adapt) and outer-loop dual price (most machinery; revisit if MPC underperforms).

2. **First build: the management-injection spike** (see Gate). The orchestrator is not buildable
   until the apply step is proven.

3. **Ambiguous-stand assignment: dominant-owner with threshold.** Assign a stand to its plurality
   ownership class only if that class clears a confidence threshold (e.g. >70% of the stand's
   pixels); otherwise **exclude and log**. Keeps clean cases, drops genuinely ambiguous ones. The
   threshold value is itself a parameter to justify and tune. Excluded stands are a reviewable
   list, never silent drops.

## The Gate: management-injection spike — PASSED (2026-07-17)

**Result: PASS.** See `notes/restart-fidelity-findings.md` and
`research/restart_fidelity/outputs/gate_cut_injection.txt`. A 30% proportional thin, three
mechanisms: native `ThinDBH` keyword (G1) ≡ `fvsCutNow` in-process (G2) ≡ `fvsAddActivity`
after a restart (G3), all exact on stand values (max |Δ| = 0.0). Per-stand targeting also
proven: cutting one stand in a 2-stand bundle left the other bit-identical to a no-cut baseline.

Two mechanism findings the orchestrator must respect:
- `fvsCutNow` works only at stop point 2 and **not** right after a restart restore; the restart
  path uses `fvsAddActivity(year, "base_thindbh", ...)` instead.
- `fvsRun(2, year)` re-stops at a stand's stop point 2 *after* a cut, so a naive loop
  double-cuts; guard each stand to one cut per barrier.

The original gate description follows for context.

Everything above rests on an **unproven** step: applying `fvsCutNow` at a barrier and resuming,
and applying *different* cuts to *different* stands within one restart segment. The spike so far
validated only the management-free transport. This must be proven before the orchestrator means
anything.

Minimal experiment, in the shape of the existing arms:

- Take a small bundle (the 5-stand fixture, `Inv_Year 2019`).
- **Arm CUT-INPROC:** in-process pause at stop point 2, `fvsCutNow` a known proportion on selected
  stands, resume. Compare against a keyword-scheduled `Thin*`/cut of the same proportion (the
  authoritative in-FVS path). Expect: match.
- **Arm CUT-RESTART:** same cut applied across a stop/restart barrier. Compare against
  CUT-INPROC. Expect: stand values match (carbon ignored).
- Verify per-stand: the cut proportion actually removed the intended TPA/BA, `FVS_Summary2`
  `RmvCode`/removed volume reflect it, and *un*-cut stands in the bundle are untouched.

Falsification: if restart can't carry a scheduled/injected cut faithfully, or per-stand cut
targeting leaks across stands, the restart-based barrier is not viable for management and the
architecture moves to in-process-only bundles (smaller bundles, no eviction).

## Stand selection layer ("be careful what we grab")

Bundle membership is the input to the whole optimization; a mis-assigned stand corrupts an
owner's flow. Guards to build (before or alongside the orchestrator):

1. **Ownership assignment** — dominant-owner-with-threshold vote over the Harris 2025 raster
   within each stand's footprint; sub-threshold stands excluded and logged.
2. **Operability mask** — ownership class ≠ harvestable. Federal wilderness is federal but
   reserved. Layer reserve status, and later slope/access, min harvest age, residual stocking.
3. **Crosswalk-vintage safety** — the TreeMap 2022-vs-2020 trap (693 vs 688 stands via
   TM_ID→PLT_CN→stand_cn) documented in `notes/fvs-to-raster-painting.md`. Pin one vintage;
   assert stand-count/coverage before a run.
4. **FVS instance limits** — `maxstands 500`, `maxtrees 3000` per process. An owner exceeding 500
   stands forces **sub-bundling**: multiple workers for one owner, and the even-flow solve must
   then gather across those sub-bundles. This is the one place "one owner = one worker" breaks and
   the allocation becomes cross-process for that owner.

## Proven vs. unproven

| Piece | Status |
|---|---|
| Concurrent isolated workers | **Proven** (`parallel_demo`, 5 processes) |
| Multi-stand restart barrier, exact on stand values | **Proven** (arm N) |
| In-process pause exact | **Proven** (arm B) |
| Restart preserves carbon | **Disproven** — out of scope, `carbon_extension=false` |
| Management injection at a barrier | **Proven (2026-07-17)** — gate passed |
| Per-stand selective cut within a bundle | **Proven (2026-07-17)** — non-target untouched |
| Even-flow allocation / rolling-horizon LP | Not started |
| Stand selection / bundling layer | Not started |

## Open questions

1. Rolling-horizon lookahead engine: throwaway FVS no-cut runs (authoritative, slow) or FVSjl
   (fast, needs the SN-variant validation the spike deferred)?
2. Sub-bundling threshold and how the cross-process even-flow solve gathers for a >500-stand owner.
3. The harvest-scheduling LP itself: objective (maximize NPV? volume?) subject to non-declining
   flow, per-stand operability, and min/max harvest age — formulation deferred until the Gate
   passes.
4. Tribal class: `projection.yaml` already notes falling back to a pooled estimate when the
   training sample is thin — does that interact with bundle formation?
