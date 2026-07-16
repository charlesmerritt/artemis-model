# FVS Restart Fidelity — Measured Findings

**Date:** 2026-07-16
**Branch:** `claude-code/parallel-fvs-runs`
**Spec:** `docs/superpowers/specs/2026-07-16-parallel-fvs-runs-design.md`
**Code:** `research/restart_fidelity/` · **Raw results:** `research/restart_fidelity/outputs/`

## Question

Does FVS stop/restart preserve FFE carbon state, and does in-process pause reproduce a
continuous run exactly? The answer selects the parallel-FVS orchestration architecture.

## Result table

Fixture: stand `43393151010478` (SN variant, `Inv_Year 1999`, 39 tree records), 1999–2019,
four 5-year cycles, **no management**. Base metrics = BA / Tpa / SDI.

| Arm | Mechanism | Base max \|Δ\| | Carbon max \|Δ\| | Verdict |
|---|---|---|---|---|
| **A** | Continuous 20-yr run | — | — | reference truth |
| **B** | In-process pause at 2004/09/14, resume same process | **0.0** | **0.0** | **exact — pause is transparent** |
| **C** | Stop/store → `--restart` chain across processes | **0.0** | **5.38** | base exact; **carbon corrupted** |
| **D** | In-memory tree-list rebuild | n/a | n/a | **mechanism does not compose** — see below |

## Arm B: in-process pause is exact

Pausing at stop point 2 and resuming in the same process reproduces a continuous 20-year run
**bit-for-bit**, including every carbon pool. Zero delta on all five cycles.

This is the load-bearing result. It means **segmentation itself is not a source of error** — a
20-year FVS run with `TimeInt 5` already executes four 5-year cycles internally, and pausing
between them changes nothing. The "advance 5 years → inspect state → apply management → resume"
loop is sound.

## Arm C: restart corrupts carbon, exactly one pool

Base metrics survive restart **exactly** (BA/Tpa/SDI delta = 0.0 across all cycles). Carbon does
not:

| Pool | Arm A (2014) | Arm C (2014) | Result |
|---|---|---|---|
| `Forest_Down_Dead_Wood` | 4.985489 | 4.985489 | survives ✓ |
| `Standing_Dead` | 2.038798 | 2.038798 | survives ✓ |
| `Forest_Shrub_Herb` | 5.40 | **0.02** | **collapses** ✗ |
| `Total_Stand_Carbon` | 64.524498 | 59.144497 | **−8.3%** |

`Forest_Shrub_Herb` collapses to a constant `0.02` after **every** restart (2004, 2009, 2014),
understating `Total_Stand_Carbon` by 3.93–5.38 tons/acre. Year 1999 matches, because it precedes
any restart. The error does not accumulate — it recurs at each barrier.

### Mechanism (strongly supported, not fully traced)

1. `putstd.f`/`getstd.f` share an identical 36-common list containing **no FFE commons**
   (`FMCOM`/`FMFCOM`/`FMPARM`/`FMPROP`/`FMSVCM`) — grep count 0, no FFE stash routine exists.
2. `FMCOM` holds `COVTYP`, "species number for the predominant cover type in the stand".
3. `fmcba.f:63-69`: *"FOR SOME ECOLOGICAL UNITS, THE HERB AND SHRUB VALUES ARE ONLY ESTIMATED
   FROM FOREST TYPE"*, with `DATA FULIV /0.1, 0.25 (pines), 0.01, 0.03 (hardwoods) …`.
4. `fmcrbout.f:89`: carbon = biomass × 0.5 for shrubs.
5. Lose `COVTYP` → the lookup falls to the hardwood default → `(0.01 + 0.03) × 0.5 = `**`0.02`** —
   exactly the observed constant.

**Prediction vs. outcome:** the spec predicted `CWD`/`CWD2B`/`ALLDWN` (down wood, snags) would
break, since those live in `FMCOM` too. They **survive exactly** — presumably recomputed from
preserved tree/mortality state. The direction of the finding held; the specific mechanism did
not. Do not trust the un-executed parts of the original source reading.

## Arm D: the in-memory rebuild path does not compose

`fvsSetTreeAttrs` rejected the tree list — *"Length of 'id' must be 324"* — because FVS triples
tree records during growth (108 captured vs 324 live slots). It cannot resize the stand. The
rejection is a **warning, not an error**, so the injection silently no-op'd and each segment ran
as an independent fresh run, appending duplicate rows (1999 ×3, 2004 ×2) to one DB.

**Arm D's numbers are confounded and must not be read as a measurement of rebuild divergence.**
This closes the rFVS in-memory tree-list path on *mechanism* grounds. It does not quantify what a
file-based (`.tre` regeneration) rebuild would cost. The architecture decision does not depend on
this arm.

## Operational finding: a negative restart code is a signal, not a result

`cmdline.f:295-300` — after `getstd` restores a stand, FVS sets
`restartcode = -originalRestartCode ! signal return to caller`. **`fvsRun()` reloads the stand and
returns without running.** The caller must call `fvsRun()` again to resume. rFVS's own
`fvsInteractRun` does exactly this (`if (stopPoint < 0) stopPoint = -stopPoint`).

Calling `fvsRun()` once per segment leaves the stand loaded but never grown, and silently
produces a **0-byte store file** that breaks the chain at the *next* segment with "Premature end
of data". Any future orchestrator must loop `fvsRun()` until the restart code is non-negative.

Codes observed: `-2` = restored, resume pending; `2` = stored at stop point; `100` = simulation
end; return `0` = good running state (**not** `2`, which the plan wrongly predicted for a
completed single-stand run).

## Architecture decision

Per the spec's falsification rules: **C carbon ≠ A carbon → the finding is confirmed.**
Restart-based **true global barriers are incompatible with valid carbon output**, and
`config/projection.yaml` requires `carbon_extension: true` with all five IPCC pools.

The project owner had chosen true global barriers over per-stand trajectories, reasoning that
county-level TPO constraints are coming and retrofitting later would be worse. That decision was
explicitly conditional on this spike. **The evidence does not support it as-is.** Options, in the
spec's terms:

1. **Candidate 1 — per-stand in-process trajectories.** Arm B proves these are exact and FFE-safe.
   Embarrassingly parallel, no restart files. Cost: no AOI-wide constraints.
2. **Outer-loop signalling.** Inner per-stand exact trajectories driven by a shadow-price/target
   signal; outer loop adjusts until AOI-wide targets are met. Global constraints without eviction.
3. **Patch `putstd`/`getstd`** to serialize the FM* commons and rebuild `FVSsn` from Open-FVS
   source. Restores Candidate 2, at the cost of a maintained FVS fork and a non-official binary.
4. **Global barriers with carbon accepted as invalid.** Contradicts `projection.yaml`. Not viable.

A narrower variant worth noting: only **one pool** is broken, via **one variable** (`COVTYP`).
Recomputing or carrying `COVTYP` across the barrier may be enough to make restart carbon-safe —
far cheaper than a full `putstd` patch. This is **untested** and should be verified before being
relied on.

## Limitations

- **One deterministic stand, 39 tree records, no management.** A *dirty* result is conclusive —
  restart demonstrably corrupts carbon. A *clean* result on any pool is **provisional**:
  `Forest_Down_Dead_Wood`/`Standing_Dead` surviving here does not prove they survive under
  mortality, establishment, fire, or salvage. Broader fixtures are required before building a
  global-barrier orchestrator on that.
- Single FVS version, SN variant, one extension subset. Equivalence claims do not generalize.
- Arm D measured nothing; the file-based rebuild path remains unquantified.
- The `COVTYP` mechanism is inferred from source + an exact numeric match (`0.02`), not traced
  through execution.

## Reproduce

```bash
# stage
mkdir -p /mnt/c/FVS/artemis_spike
cp /mnt/c/FVS/Artemis_project/FVS_Data.db /mnt/c/FVS/artemis_spike/FVS_Data.db
uv run python -c "
from research.restart_fidelity import make_keyfiles, paths
for a in ('a','b','c','d'):
    (paths.SPIKE_DIR_WSL / f'arm_{a}.key').write_text(make_keyfiles.build_keyfile(a, f'arm_{a}.db'))
"
# run arms (arm C is four separate processes: c1 c2 c3 c4)
cd /mnt/c/FVS/artemis_spike && /mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe \
  "$(wslpath -w <worktree>/research/restart_fidelity/run_arms.R)" a

uv run pytest tests/test_restart_fidelity.py -v   # 9 passed
```

## Possible paper

**Reproducible iterative coupling of FVS with spatial management policy.** Contribution: FVS's
official stop/restart facility preserves growth state exactly but silently drops FFE cover-type
state, understating total stand carbon by ~8% per barrier — with base metrics bit-identical, so
the corruption is invisible to any summary-level check. Identifies in-process pause as the exact
alternative. Reviewer concern: results are specific to one FVS version, variant, and extension
subset.
