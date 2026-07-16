# Design: Parallel FVS runs and iterative coupling

**Date:** 2026-07-16
**Status:** Draft for review
**Branch:** `claude-code/parallel-fvs-runs` (worktree `.claude/worktrees/parallel-fvs-runs`)
**Deliverable (this spec):** `experiments/spike_restart_fidelity/` — a decisive experiment that
selects the orchestration architecture. No orchestrator is built until it reports.

## Problem

ARTEMIS must project a 5-county Florida AOI under an iterative coupling loop: run FVS for 5
years, evaluate management thresholds, apply treatments, repeat to a 50-year horizon
(`config/projection.yaml`: `horizon_years: 50`, `cycle_years: 5`). Rather than one FVS
instance over the whole AOI, we want many instances running in parallel over spatial
partitions, orchestrated together.

`main.py` already sketches the intended shape (`parallel()`, `project(5)`, threshold check,
prescription application), but no `Artemis` implementation exists.

Two things must be true for this to be publishable:

1. **Parallel/segmented execution must not change results** versus an uninterrupted run.
2. **State must survive the 5-year barriers** — including carbon/fuels, which
   `projection.yaml` requires (`carbon_extension: true`, all five IPCC pools).

## Framing correction

A 20-year FVS run with `TimeInt 5` **already executes four 5-year growth cycles internally**.
Segmenting the run does not change the growth mathematics; it inserts an external pause
between iterations of a loop FVS runs anyway.

Therefore the baseline comparison (5+5+5+5 vs 20) is **not** testing whether segmentation
perturbs growth. It is testing **state-transfer fidelity across the pause**. This reframes
the whole question:

| Mechanism | Equivalence to continuous run |
|---|---|
| Pause in-process (state never leaves memory) | Exact by construction |
| Stop/store → restart file | Must be proven; depends on `putstd`/`getstd` coverage |
| Rebuild input from exported tree list | Expected to diverge; loses non-tree state |

## Confirmed decisions

Carried forward from prior research and this session's review:

- Engine stays behind a **narrow simulation interface**. Partitioning, orchestration, policy,
  persistence, and comparison logic must not import or expose engine-specific types.
- Multiple engines/adapters will be **evaluated empirically**, not chosen upfront: official
  Windows `FVSsn.dll` via `rFVS`; Linux Open-FVS via fvs2py or a native wrapper; FVSjl via its
  explicit `StandState`; and the FVS stop/restart CLI path.
- Every engine consumes the same normalized run request and returns the same normalized cycle
  result, or an **explicit unsupported-capability result**.
- **Policy scope:** thresholds are evaluated per partition, independent of other partitions.
- **Partition unit:** stand / restartable bundle (not county), respecting FVS per-instance limits.
- **Barrier state surface:** summary + tree list + removals. Carbon/fuels deferred from the
  barrier payload, but see the finding below — carbon *correctness* is not deferrable.
- **Barrier semantics:** the project owner chose to **keep true global barriers** rather than
  collapse them, even though per-partition-independent policy does not require them. Rationale:
  county-level TPO constraints (`notes/management-pipeline-plan.md`) are expected to bind, and
  retrofitting a global barrier later was judged worse than paying for it now. This decision is
  what makes restart fidelity load-bearing, and is therefore what the spike tests. If the spike
  falsifies restart fidelity, this decision must be revisited — it is a preference about
  sequencing, not a claim about FVS behaviour.

## Data layer: DuckDB

**Decision:** DuckDB is the database and aggregation engine for this project. All result
aggregation, cross-arm comparison, and any work over large databases goes through DuckDB
rather than pandas-in-memory or raw SQLite queries.

FVS itself writes **SQLite** (`FVSOut.db`) and that is not negotiable — it is the engine's
output format. DuckDB consumes it directly via the `sqlite` extension, so no export step is
needed:

```sql
INSTALL sqlite; LOAD sqlite;
ATTACH 'FVSOut.db' AS fvs (TYPE sqlite, READ_ONLY);
SELECT Year, AVG(BA) FROM fvs.FVS_Summary2 GROUP BY Year;
```

**Verified 2026-07-16** against the real `/mnt/c/FVS/Artemis_project/FVSOut.db` (14 MB):
attach, schema introspection, and per-cycle aggregation all work. `duckdb==1.5.4` added to
`pyproject.toml`.

This suits the spike well: each arm produces its own `FVSOut.db`, and DuckDB can attach all
four simultaneously and diff them in one query, rather than loading each into memory.

`FVS_Summary2` columns (verified): `CaseID, StandID, Year, RmvCode, Age, Tpa, TPrdTpa, BA,
SDI, ZeideSDI, ReinekeSDI, SDIMax, RDSDI, CCF, ...`. `RmvCode` supports the removals ledger.

The longer-term target schema is already specified in `notes/duckdb-iterative-coupling-cells.md`
— three pillars: `fvs_cycle_change` (5-year state transition ledger), `fvs_removals`
(management/removal ledger), and `fvs_spatial_crosswalk` (spatial unit → FVS simulation unit).
The spike's comparison views are the first concrete step toward `fvs_cycle_change`.

**Confirming observation:** the existing `FVSOut.db` contains `FVS_Cases`, `FVS_Error`,
`FVS_InvReference`, `FVS_Summary2`, and the derived `fvs_trajectory` tables — but **no
`FVS_Carbon` table**, because the current keyfile enables no carbon extension. This
independently corroborates that the spike must add `FMIn`/`CarbRept` + `CARBREDB`.

## Verified findings

All verified on 2026-07-16 by reading Open-FVS source at
`~/projects/ForestVegetationSimulator/bin/FVSsn_buildDir` and by executing the Windows bridge.

### The WSL2 → Windows bridge works

`/mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe` runs from WSL2, `library(rFVS)` loads,
and the package's Rd docs are readable. Prior research also confirmed `rFVS=2024.7.1`,
`FVSsn.dll` loading, and dimensions `maxtrees=3000, maxspecies=90, maxplots=500, maxcycles=40`.

### Stop point codes (from `rFVS::fvsRun` docs)

```
 0  Never stop.
-1  Stop at every stop location.
 1  Stop just before the first call to the Event Monitor.
 2  Stop just after the first call to the Event Monitor.     <- management injection point
 3  Stop just before the second call to the Event Monitor.
 4  Stop just after the second call to the Event Monitor.
 5  Stop after growth and mortality computed, prior to applying them.
 6  Stop just before the ESTAB routines are called.
 7  Stop just after input is read, before imputation and calibration (year ignored).
```

`fvsRun` return code **2** = "FVS has finished processing all the stands; **new input can be
specified**". This confirms a single worker process can complete one stand and re-initialize a
fresh one — worker reuse is supported.

### The restart mechanism is reachable from rFVS

`fvsSetCmdLine` parses `--stoppoint=<code>,<year>,<file>` and `--restart=<file>`
(`cmdline.f:125-140`). The in-process API and the file-based restart path are therefore *not*
separate worlds; rFVS can drive both.

`--restart=` ignores any keyword file (`cmdline.f:148-152`).

### The restart file is a multi-stand store

`fvsStopPoint` (`cmdline.f:400-456`) writes a one-time header then calls `putstd` **per stand**,
appending to the same stream unit. `cmdline.f:179` comments: *"store the last used restart code
that was used to store all the stands."* A multi-stand run accumulates every stand into one
restart file. A global barrier is therefore mechanically implementable.

### **`putstd`/`getstd` omit all FFE (fire/fuels/carbon) state**

`putstd.f` (896 lines) and `getstd.f` (900 lines) include an **identical set of 36 commons**:

```
ARRAYS CALCOM CALDEN COEFFS CONTRL CWDCOM DBSTK ECON ESCOM2 ESCOMN ESHAP ESHAP2
ESPARM ESRNCM ESTCOR ESTREE FVSSTDCM GGCOM HTCAL MULTCM OPCOM OUTCOM PDEN PLOT
PRGPRM RANCOM SCREEN SSTGMC STDSTK SUMTAB SVDATA SVDEAD SVRCOM VARCOM VOLSTD
```

Base state is well covered: `RANCOM` (RNG), `CALCOM` (calibration), `ESTREE`/`ESCOMN`
(establishment), `ECON`, `MULTCM`.

**The FFE commons — `FMCOM`, `FMFCOM`, `FMPARM`, `FMPROP`, `FMSVCM` — appear in neither file**
(grep count 0). No FFE-specific stash routine exists; `cmdline.f` is the only caller of
`putstd`/`getstd`.

`FMCOM` holds precisely the cross-cycle state that would be lost:

- `CWD` — coarse woody debris, tons/acre
- `CWD2B` — *"debris-in-waiting: dead crown material which is scheduled to become down debris
  at appropriate points in the future"*
- `ALLDWN` — time by which the last 5% of large snags have fallen
- `BURNYR`, `CROWNW`, `CURKIL`

`CWD2B` is future-scheduled state; discarding it should perturb the carbon/fuels trajectory
after every restart, silently and without error.

**Ruled out as a false positive:** `CWDCOM` *is* included in `putstd`, but it holds per-species
decay **coefficients** (`CWDL0..CWDL3`, `CWDS0..CWDS3`, `CWTDBH`), not the FFE dynamic pools.

**Status: strong source evidence, not yet executed.** This spec exists to test it.

### Carbon observable

`CARBREDB` (`dbsin.f` option 29) routes carbon reports to the output database as table
**`FVS_Carbon`**, written by `fmcrbout.f:212 → dbsfmcrpt.f`. This is the measurable that
decides the architecture.

## Consequence: the architecture is contested, so measure

If the `putstd` reading is right, a **true global barrier is incompatible with valid carbon
output**: holding ~693 stands paused simultaneously is infeasible in live processes (FVS keeps
stand state in global commons — one stand per process; 693 × ~150MB ≈ 100GB), so a global
barrier requires eviction → restart files → corrupted carbon.

Two candidate architectures follow, selected by the spike:

**Candidate 1 — per-stand in-process trajectories.** Because policy is per-partition
independent, no stand's decision depends on another stand's state, so stands never need to be
paused simultaneously. Each stand runs its full coupled trajectory inside one live process:

```
load stand -> [ advance 5yr -> read state -> evaluate thresholds -> fvsCutNow() ] xN -> write -> next stand
```

State never serializes. Exact by construction, embarrassingly parallel, FFE intact. A "bundle"
is then only a work-queue chunk that amortizes DLL load, not a simultaneity requirement.
Cost: no AOI-wide constraints.

**Candidate 2 — true global barriers.** Gather → decide → scatter with restart-file state
transfer. Supports AOI-wide/TPO constraints directly. Cost: depends entirely on restart
fidelity; if the finding holds, requires either patching `putstd`/`getstd` to add the FM*
commons and rebuilding `FVSsn` (a maintained FVS fork, non-official binary), or accepting
invalid carbon.

A third path, if global constraints are needed but restart is unsafe: **outer-loop signalling**
— inner per-stand exact trajectories driven by a shadow-price/target signal, outer loop adjusts
the signal until AOI-wide targets are met (Lagrangian-style, standard in forest planning).
Recorded here as an option; out of scope for this spec.

## The spike

**Question:** does stop/restart preserve FFE carbon state, and does in-process pause reproduce
a continuous run exactly?

**Fixture:** stand `43393151010478` (from the existing `/mnt/c/FVS/Artemis_project/` keyfile),
SN variant, `InvYear 1999`, `TimeInt 5`, `NumCycle 4` → 1999–2019, exactly four 5-year cycles.
**No management**, to isolate state transfer from treatment effects. Carbon enabled
(`FMIn`/`CarbRept` + `CARBREDB`). Outputs: `FVS_Summary2` and `FVS_Carbon`.

Input DB: `/mnt/c/FVS/Artemis_project/FVS_Data.db` (1.0 GB, present).

### Arms

| Arm | Mechanism | Prediction |
|---|---|---|
| **A** Continuous | `fvsRun()`, 1999→2019, no stops | reference truth |
| **B** In-process pause | `fvsRun(2, yr)` at 2004/2009/2014, resume in same process; never serialized | **exact match to A** |
| **C** Stop/restart | `--stoppoint=2,yr,file` → `--restart=file`, chained across processes | base metrics match A; **carbon diverges** |
| **D** Tree-list rebuild | export tree list each segment, rebuild FVS input, re-run | diverges broadly (calibration, RNG, estab, FFE) |

### Falsification

The spike is informative in every direction:

- **B ≠ A** → in-process pause is broken; **both** candidate architectures collapse. This is the
  highest-value single assertion in the experiment.
- **C carbon ≡ A carbon** → the `putstd` source reading is **wrong**; global barriers are viable
  and Candidate 2 proceeds.
- **C carbon ≠ A carbon** → finding confirmed; choose between Candidate 1, outer-loop
  signalling, or patching `putstd`.
- **D** quantifies the cost of tree-list rebuild, closing that option with evidence rather than
  assertion.

### Reporting requirement

Carbon must be diffed **separately** from base metrics. A summary-only comparison would show
arm C passing while carbon is silently corrupt — the exact failure this spike exists to catch.

Per-arm comparison, executed in DuckDB by attaching all four arms' `FVSOut.db` at once:

- **Exact equality** where transparency is expected (A vs B).
- Absolute and relative differences for TPA, BA, SDI, QMD, total/merch volume.
- Carbon pools reported as their own table, per cycle.
- Runtime and restart-file size (operational cost of each mechanism).

Because each arm writes its own SQLite database, the diff is a single DuckDB query joining
attached catalogs on `(StandID, Year)` — no per-arm export or in-memory merge:

```sql
ATTACH 'arm_a/FVSOut.db' AS a (TYPE sqlite, READ_ONLY);
ATTACH 'arm_b/FVSOut.db' AS b (TYPE sqlite, READ_ONLY);
SELECT a.Year, a.BA - b.BA AS ba_delta, ...
FROM a.FVS_Summary2 a JOIN b.FVS_Summary2 b USING (StandID, Year);
```

### Layout

```
experiments/spike_restart_fidelity/
  make_keyfiles.py    # emit A/B/C/D keyfiles from the fixture stand
  run_arms.R          # rFVS driver, executed on Windows via Rscript.exe
  compare_arms.py     # DuckDB: attach arm DBs, diff FVS_Summary2 and FVS_Carbon
  README.md           # how to run, and the recorded results
```

WSL↔Windows path translation is confined to a single helper. FVS must execute from a
Windows-visible working directory.

## Verification

- The comparison logic is separated from I/O: arm databases are attached by a thin loader, and
  the diff/tolerance logic is unit-tested without FVS against small fixture tables built
  in-memory in DuckDB (`tests/test_spike_restart_fidelity.py`).
- The A-vs-B exact-equality assertion is the primary automated check.
- A guard test asserts the carbon diff is reported even when the base-metric diff is empty —
  the silent-corruption failure mode this spike exists to catch.
- A precondition check fails loudly if an arm's `FVSOut.db` lacks `FVS_Carbon`, since a missing
  carbon table would otherwise read as "no carbon difference".
- Results are recorded in the experiment README and promoted to `notes/` with the
  architecture decision.

## Limitations

- Source-read evidence only, until the spike executes. The FFE claim is a prediction.
- Whether `rFVS` and `--restart` interoperate cleanly (in-process API + file restart) is
  **untested** and is itself a spike outcome.
- Multi-stand restart round-trip is inferred from `cmdline.f`, not executed.
- A single deterministic stand may not exercise the state that actually breaks. Arm D and the
  carbon arms partly mitigate; broader fixtures (mortality, establishment, FFE fuels) are a
  follow-up before any architectural claim is published.
- Equivalence results may be specific to one FVS version, variant, and extension subset.

## Open questions

1. Which FVS stop point is the correct cycle boundary for a store: 2 (after first Event
   Monitor, where `fvsCutNow` applies) or another? Arm C should confirm 2 round-trips.
2. If restart fidelity fails, is a patched/rebuilt `FVSsn` acceptable for publication, or does
   the official binary constrain us to Candidate 1 / outer-loop?
3. Do county-level TPO constraints from `notes/management-pipeline-plan.md` actually bind in
   the first prototype, or can they be deferred?

## Possible paper

**Reproducible iterative coupling of FVS with spatial management policy.**
Contribution: quantify when cycle-segmented FVS execution is equivalent to a continuous
projection, and identify the state variables required for faithful coupling — including
extension state that the official stop/restart facility does not preserve.
Strongest reviewer concern: results may be specific to one FVS version, variant, or extension
subset.
