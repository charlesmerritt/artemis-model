# FVS fidelity proof — `fvs_fidelity_proof.csv`

This is the evidence table behind one claim ARTEMIS depends on:

> **We can pause an FVS projection at a fixed calendar year, apply a harvest to the
> stands we choose, and resume — and the model behaves exactly as if the cut had
> been written into the keyword file from the start.**

That is the mechanism the constrained harvest scheduler is built on. A county-level
TPO volume cap is a *global* constraint: at each 5-year barrier the scheduler must
look across every stand, decide which ones to cut so the county stays under its cap,
apply those cuts, and let the rest keep growing. This table shows each piece of that
loop was measured against an authoritative baseline and matched it to the last digit.

## Where the numbers come from

Every row is a measured FVS result, not an estimate. Sources, all in the repo:

- `research/restart_fidelity/outputs/*.txt` — raw arm-by-arm comparison output.
- `research/restart_fidelity/outputs/gate_cut_injection.txt` — the management-injection gate.
- `notes/restart-fidelity-findings.md` — the full write-up, mechanism notes, and caveats.
- `tests/test_restart_fidelity.py`, `tests/test_cut_injection.py` — the tracked tests.

Runs used the FVS Southern (SN) variant, FS2026.1, on real ARTEMIS pilot stands.
`|delta|` is the largest absolute difference across all compared cycles and metrics.

## Columns

| Column | Meaning |
|---|---|
| `check_id` | Short label for the check (matches the arm/gate names in the source files). |
| `category` | `restart_fidelity` = does stop/restart preserve state? `management_injection` = is a runtime-applied cut faithful? |
| `claim` | The plain-English statement the row tests. |
| `fixture` | The stand(s), variant, year span, and treatment used. |
| `metric` | What was compared. Stand values = basal area (BA), trees per acre (TPA), stand density index (SDI). |
| `expected` | What a faithful mechanism must produce. |
| `observed` | What FVS actually produced. |
| `result` | PASS / FAIL. |
| `in_scope` | Whether the metric is inside ARTEMIS v1 scope (carbon is currently out — see below). |

## How to read it

- **Stand values survive restart exactly.** Rows B, C, E, N: pausing a run — in one
  process or across a stop/restart barrier, one stand or a five-stand bundle — changes
  BA/TPA/SDI by `0.0`. The multi-stand row (N) is the one the scheduler actually needs:
  a global barrier stores *all* stands in one file and rehydrates them, and all 25
  stand-cycles came back identical.
- **A runtime cut is the real cut.** Rows `G_tpa`/`G_ba`: a 30% thin removes exactly
  30% of TPA and BA. Rows `G_inject_vs_native` / `G_restart_vs_inproc`: injecting that
  cut at a barrier — whether in-process or after a restart — matches the native FVS
  `ThinDBH` keyword at `0.0`.
- **Selective harvest works.** Rows `G_selective_*`: in a two-stand bundle you can cut
  one stand by exactly 30% and leave the other bit-identical to its no-cut baseline.
  This is what "cut the stands the allocation selects, leave the rest" requires.

## The one FAIL, and why it's not a blocker

Row `C_carbon` is the honest exception. Stop/restart silently corrupts one FFE carbon
pool (`Forest_Shrub_Herb` collapses to a constant `0.02`), understating total stand
carbon by ~8% at every barrier — while stand values stay bit-identical, so a
summary-level check would never catch it. Carbon was consciously set **out of scope**
for v1 (`config/projection.yaml: carbon_extension: false`, with a tripwire test that
fails if the flag is re-enabled). The corruption must be resolved before any carbon
result is reported externally. On the metrics in scope, restart is exact.

## Caveats (carried from the source notes, don't drop them)

- Narrow fixtures: 6 stands total across all arms, one variant (SN), one FVS version,
  no fire. A *clean* result on any single pool is provisional until tested on broader
  stands, mortality, establishment, and salvage.
- Exactness is conditional on no fire coupling FFE state back into growth. The FFE
  live-fuel state *is* known to be corrupted by restart, so enabling BURN/SALVAGE could
  propagate that into stand values.
- `fvsCutNow` works only at stop point 2 and not immediately after a restore; the
  restart path uses `fvsAddActivity`. `FVS_Summary2` emits two rows per cut year
  (RmvCode 1 pre-removal, RmvCode 2 post-removal) — joins must key on
  `(StandID, Year, RmvCode)` or pre/post rows cross-join and manufacture false deltas.
