# FVSjl as an ARTEMIS growth engine — evaluation and integration design

Status: design, not built. Nothing in `pipeline/` changes on this branch.
Evidence: `research/fvsjl_engine_differential/` (runnable; every number below came from it).

## 1. What FVSjl is, and the recommendation

FVSjl (`github.com/bahaelaila7/FVSjl`, local clone at `/home/chazm/projects/FVSjl`) is a
Julia reimplementation of FVS that reads the same `.key`/`.tre` inputs and writes the same
`.sum`/SQLite outputs. It claims all 24 variants, including Southern (SN), validated
bit-exact-or-explained against a freshly relinked Fortran oracle, plus FFE/carbon, ECON,
Event Monitor, establishment, and the recovered PPE landscape harness.

**Recommendation: add FVSjl behind one engine seam; do not make it the default yet, and do
not run it as one process per keyfile.** Three findings drive that:

1. **In-process it is 12x faster per core than FVSsn, and ~8x faster than 16 FVSsn
   processes** — but only when its summaries are collected in memory rather than written
   through the `DSNOut` SQLite writer (§3).
2. **One Julia process per stand is a non-starter**: 27–31 s of JIT compilation before the
   first stand runs, and `fvsjl-run.jl` on a single stand took 57 s wall.
3. **Fidelity on ARTEMIS's own keyfiles is close but not identical** to this FVSsn build,
   and two artemis-visible output gaps remain open (§4, B1–B2).

A second point in FVSjl's favour: **it runs stands this FVSsn build cannot run at all.**

## 2. Method

Fixture: 200 random SN plots from `/mnt/c/FVS/Artemis_project/FVS_Data.db` (seed 7), each
rendered by the production renderer `pipeline.s4_fvs.regime_templates.render_keyfile` under
three regimes — `no_management`, `thin_from_below` (2032, DBH<=8, 40%), and
`plantation_rotation` (thin 2032, clearcut 2047, `Estab`/`Plant` 2048) — 600 runs total,
10 cycles each, reading the same input database through the `DataBase`/`DSNIn` block.

Environment: WSL2, 32 logical cores, repo on ext4, `/tmp` on **tmpfs**; FVSsn built from
`/usr/local/src/open-fvs` (`/usr/local/bin/FVSsn`); Julia 1.12.6; FVSjl at `1b28a1d4`
(2026-09-22). An earlier pass on this same fixture used FVSjl `773139a3`; where the two
disagree it is called out, because one blocker closed between them.

Reproduce:

```bash
PYTHONPATH=. uv run python -m research.fvsjl_engine_differential.make_fixture
research/fvsjl_engine_differential/run_fortran.sh
JULIA_DEPOT_PATH=~/.julia julia -O1 --project=/path/to/FVSjl \
    research/fvsjl_engine_differential/run_fvsjl.jl            # DSNOut path — slow, see §3
JULIA_DEPOT_PATH=~/.julia julia -O1 -t 16 --project=/path/to/FVSjl \
    research/fvsjl_engine_differential/run_fvsjl_threads.jl    # the production-shaped path
JULIA_DEPOT_PATH=~/.julia julia -O1 --project=/path/to/FVSjl \
    research/fvsjl_engine_differential/io_ablation.jl
PYTHONPATH=. uv run python -m research.fvsjl_engine_differential.compare
```

Note `JULIA_DEPOT_PATH`: this workstation exports `/workspace/.julia_depot`, a path that
does not exist outside the FVSjl author's container, and Julia fails to instantiate until
it is overridden.

## 3. Throughput — and the I/O trap that governs it

All figures are the same 600 runs on the same machine, fixture on ext4
(`outputs/fortran_timing.txt`, `outputs/fvsjl_threads.txt`, `outputs/fvsjl_timing.txt`).

| Engine and mode | runs/s |
|---|---|
| FVSsn, one process per keyfile (what `04_fvs_run.py` does today) | 9.2 |
| FVSsn, 16 parallel shards | 77 |
| FVSjl in-process, summaries as CSV text, 1 thread | 112–116 |
| FVSjl in-process, summaries as CSV text, 8 threads | 510–589 |
| FVSjl in-process, summaries as CSV text, 16 threads | 596–774 |
| **FVSjl writing `DSNOut` SQLite per stand, on ext4** | **1.0–1.5** |
| FVSjl cold start before the first stand (JIT) | 27–31 s |

**The DSNOut writer is the trap.** `io_ablation.jl` holds the input database fixed and
varies only where the output database lives (`outputs/io_ablation.txt`):

```
in=disk  out=none (summary text only): 140.2 runs/s
in=disk  out=DSNOut on disk:             1.2 runs/s
in=disk  out=DSNOut on tmpfs:          109.4 runs/s
```

Reading the input SQLite per stand is cheap (140 runs/s straight off ext4). Writing
`DSNOut` to a real disk costs ~115x, because each stand opens the output database several
times (`write_dbs_cases!`, `write_dbs_invref!`, `write_dbs_summary!`) and inserts row by
row with a durable commit each time. FVSsn's own DBS writer, writing its `out.db` into the
same ext4 directory, sustains 9 runs/s — so this is FVSjl-specific, not a filesystem fact.

Two consequences, both binding on the design in §5:

- The worker must run with `output = :csv` and hand rows back in memory. ARTEMIS bulk-loads
  them into DuckDB. `DSNOut` is for one-off debugging only.
- Any benchmark staged under `/tmp` on this machine is measuring tmpfs and will overstate
  FVSjl by roughly two orders of magnitude. The first pass of this evaluation did exactly
  that; `io_ablation.jl` exists so the mistake cannot be repeated silently.

Scale, at 16 threads and 600 runs/s versus 16 FVSsn processes at 77 runs/s: the
16,632-run pilot goes from ~3.6 min to ~28 s (plus 30 s of JIT); a 1.6M-run
region (200k stands x 8 trajectories) from ~5.8 h to ~45 min.

## 4. Fidelity against FVSsn

`outputs/comparison.txt`, FVSjl `1b28a1d4`. Fortran rows are restricted to `RmvCode` 0/1
because FVSjl writes one row per cycle where `FVS_Summary2` writes a pre-cut and a post-cut
row; comparing without that filter manufactures a large fake divergence at every cut year.

| regime | rows | TPA within 1% | BA within 1% | TCuFt within 1% | final-year TCuFt total, FVSsn vs FVSjl |
|---|---|---|---|---|---|
| `no_management` | 1838 | 95.0% | 96.7% | 97.2% | 825,767 vs 825,848 (+0.01%) |
| `thin_from_below` | 1808 | 88.6% | 90.8% | 91.2% | 808,427 vs 808,208 (−0.03%) |
| `plantation_rotation` | 1786 | 97.5% | 84.4% | 91.4% | 409,976 vs 409,296 (−0.17%) |

Rows differing by more than 5% are 0.0–1.0% of each regime. At the landscape totals ARTEMIS
actually optimises against, the two engines agree to better than 0.2%. This is *not*
bit-exactness: FVSjl's own validation is against a different FVS build than
`/usr/local/bin/FVSsn`, and ARTEMIS's historical outputs came from a third
(`FVSsn.dll` via rFVS 2024.7.1 on Windows). Whichever engine ARTEMIS runs, the reference
build must be pinned and results re-baselined once.

### Stands FVSsn cannot finish

FVSsn aborted with SIGFPE (exit 136) on **33 of 200 stands unmanaged, 37 with the thin, 42
with the plantation rotation** (16–21%); FVSjl completed all 200 in every regime. FVSjl's
own bug ledger documents the cause as FVS bug D38 — a missing underflow guard in SN volume
initialisation (`r9ht`) for stands with short trees, which `r9cuft` already has.

This is the finding with the widest consequences, and it is **not** primarily about FVSjl:

- If the Windows `FVSsn.dll` behaves the same way, ARTEMIS is silently dropping ~1 stand in
  6 from its trajectory libraries — and disproportionately the seedling-heavy,
  recently-regenerated stands, which is exactly the post-clearcut population the scheduler
  reasons about. `run_shard` counts these as `failed` and moves on.
- Verify against the Windows DLL before drawing conclusions; this open-fvs build may have
  been compiled with FP traps the production DLL does not enable.

## 5. Open blockers

| # | Blocker | Status at `1b28a1d4` | What ARTEMIS must do |
|---|---|---|---|
| **B1** | **No `FVS_Summary2`; stand identity dropped.** FVSjl writes `FVS_Summary` (one row per cycle, no pre/post-cut pair). Its `FVS_Cases` row carries `Stand_CN=''`, `MgmtID='NONE'`, empty `RunTitle`, where FVSsn carries the `StandCN`, `A001` and the regime title artemis emits. | Open | Never recover trajectory identity from FVS output — carry it on the job. Map `FVS_Summary` to the `CycleRow` contract in the adapter, and derive removals from the cut-year row rather than a post-cut row. |
| **B2** | **The semantic-YAML treatment schema names the wrong keyword fields.** `thindbh`/`thinht` map `species` to field 7, which the engine reads as residual BA; the `thinbba` family maps `species`/`plot` to fields 6–7, which are min/max height; `thinsdi`/`thincc`/`thinrden` are shifted by one. FVSjl's own `KEYWORDS.md` (the shared `THIN*` layout table) records the mismatch; the `.key` reader is correct. | Open | Keep emitting `.key` text from `regime_templates`. Do not adopt the YAML `treatments:` form until each field is pinned by a test. This is precisely the silent-field class `config/fvs_keywords.yaml` exists to police. |
| **B3** | **Baseline shift.** Neither engine matches the build ARTEMIS's published numbers came from (§4). | Open by definition | Pin one reference build; re-baseline once, deliberately. |
| **B4** | **Volume on bare ground after clearcut + `Plant`.** At FVSjl `773139a3`, 106 of 200 plantation stands reported large volume in the cycle after planting (stand `90822958010478`: BA 1, 42,959 ft³, where FVSsn reports 0), inflating landscape final-year volume by 20%. | **Closed** at `1b28a1d4` — same fixture, same keyfile, now 0 ft³, matching FVSsn; 0 affected stands across all 200. The fixing commit was not identified among the 131 commits between. | Keep the tripwire: `compare.py` fails any FVSjl row with `BA <= 5` and `TCuFt > 500`. Pin the FVSjl commit so a regression cannot arrive silently. |
| **B5** | **JIT crashes.** At `773139a3` on Julia 1.12.6, the first `run_keyfile` crashed inside LLVM codegen at default `-O2` (reproducible), and 1 of 7 runs crashed at `-O1`. Both backtraces were in the compiler, never in simulation code. | **Not reproduced** at `1b28a1d4`: 5 clean runs (3x `-O1`, 2x `-O2`). Small sample. | Do not treat as fixed. The worker must be supervised and resumable (§6), which is required for long batches anyway. Consider a PackageCompiler sysimage, which also removes the 30 s JIT. |

## 6. Design

### 6.1 One engine seam — `pipeline/s4_fvs/engine.py`

```python
class FvsEngineKind(Enum):
    FVSSN_FORTRAN = "fvssn_fortran"
    FVSJL = "fvsjl"

@dataclass(frozen=True)
class FvsJob:
    trajectory_id: str    # identity lives HERE, never in FVS output (B1)
    stand_cn: str         # exact string, via pipeline.ids.as_id_series
    keyfile: str          # rendered by regime_templates / regime_library, unchanged

class FvsEngine(Protocol):
    kind: FvsEngineKind
    version: str          # FVSsn build id | FVSjl commit + Julia version
    def run(self, jobs: Iterable[FvsJob], input_db: Path) -> Iterator[CycleRow]: ...
```

- `CycleRow`: `trajectory_id`, `year`, `cut` (an enum, not a bool), `tpa`, `ba`, `sdi`,
  `qmd`, `tcuft`, `mcuft`, `bdft`, removals. It populates `trajectory_cycles` directly.
- Config in `config/projection.yaml` under `fvs:` — `engine: fvssn_fortran | fvsjl`, plus
  `fvsjl: {julia, project, threads, sysimage}`. An enum, per AGENTS.md.
- The Fortran adapter is `run_shard`/`run_batch` from
  `experiments/2026-08-24_leto-ca-forest-viz/04_fvs_run.py`, promoted unchanged. It stays
  the reference engine, and the `rc in (0, 10)` / SIGFPE accounting becomes a reported
  count rather than a log line.
- **Cache keys gain engine kind and version.** Trajectories from two engines must never mix
  in one library; dedup is a cache, so its key must name the producer.

### 6.2 The FVSjl worker — `pipeline/s4_fvs/fvsjl_worker.jl`

- Python starts **one** long-lived `julia -O1 -t N --project=<FVSjl>` per machine. One
  process per stand pays 30 s of JIT per stand and is never acceptable.
- Jobs arrive as a table (`jobs(trajectory_id, stand_cn, keyfile_text)`) in a SQLite file
  beside the existing `fvs_inputs.db`; `@threads` runs them; each call is
  `run_keyfile(path; variant = Southern(), output = :csv)`.
- **No `DSNOut`** (§3). Each thread appends CSV summaries to its own file; Python
  bulk-loads them into DuckDB. Thread-private outputs also avoid SQLite write contention.
- A `done(trajectory_id)` table makes the worker resumable, so a crash (B5) or a killed
  overnight batch costs only the jobs in flight. Python restarts the worker and continues.
- `juliacall` in-process is rejected: it does not compose with the multiprocessing pool, and
  a Julia-level crash would take Python down with it.
- **Keyfile text stays the contract**, so artemis's verified renderers and keyword register
  keep their meaning and any job can be replayed through FVSsn byte for byte.

## 7. Phases, each gated by a test

| Phase | Work | Gate |
|---|---|---|
| **0. differential harness** | `research/fvsjl_engine_differential/` (landed on this branch): fixture, both engines, `compare.py`. | Unmanaged and thin: >=95% of rows within 1% on BA and TCuFt; landscape final-year total within 0.2%; zero `BA<=5 & TCuFt>500` rows; SIGFPE stands reported, not dropped silently. |
| **0b. close issue #17 with it** | Use the two-engine differential to verify the field layouts artemis still lacks — `ThinBBA`, shelterwood, `Natural` — by *behaviour* rather than by reading a table. Two independent implementations disagreeing on a card is a layout bug; agreeing is evidence. | One pinned layout test per new keyword in `config/fvs_keywords.yaml`, each citing the differential run. This is the cheapest route out of the `ThinDBH`-only regime library. |
| **1. adapter** | `engine.py`, `fvsjl_worker.jl`, sysimage build, Dockerfile additions (juliaup, pinned Julia, FVSjl pinned by commit). | Adapter `CycleRow`s equal the harness output; killing the worker mid-batch loses no completed job; `fvs.engine` switches engines with no other code change. |
| **2. default switch** | Flip `fvs.engine` to `fvsjl`. | Phase 0 gates pass on all three regimes at the pinned FVSjl commit, on ext4, through the CSV path; a pilot region reproduces its FVSsn library within the Phase 0 bar. |
| **3. shared-start branching** (research) | FVSjl holds a stand's whole state in one object (`StandState`, including `control.schedule`) and exposes `grow_cycle!`. Grow the common prefix once, copy the state at each treatment year, append activities, continue. With the 5–25 year timing offsets that ARTEMIS requires, trajectories within a stand share long prefixes. | A branched trajectory is **bit-identical** to the equivalent continuous run for every prescription in the library — the standard `research/restart_fidelity` already sets. Measure the saving; do not assume it. Needs a small public stepping API upstream (we cannot push to `bahaelaila7/FVSjl`). |
| **4. extensions** | Turn FFE carbon back on for library runs; evaluate ECON for the industrial rotations. | Carbon matches FVSsn within the Phase 0 bar on continuous runs. Note the original reason `carbon_extension: false` was a stop/restart artifact (`research/restart_fidelity/outputs/arm_c_vs_a.txt`); library runs have no barrier, and an in-process engine has no serialisation boundary to corrupt. |

## 8. Capabilities beyond a faster FVSsn

- **PPE MXHRVP landscape harvest scheduling** (`ppe_run_landscape_harvest!`) — a port of the
  multistand harvest allocator that was deleted from the FVS tree in 2014, validated against
  a rebuilt historical `FVSppe`. Per master cycle it ranks stands by a `PRIORITY`
  expression and selects until a `TARGET` landscape flow is met, with `CREDIT` per stand,
  an exact partial cut of the marginal stand (`hvpart`), and an optional
  max-contiguous-clearcut veto (`LHVMXC`/`hvcntg!`).
  - **Where it fits ARTEMIS:** as a *second, FVS-native even-flow baseline* reported beside
    the annealer and the oldest-first greedy allocator — one that reacts to each stand's
    realised state instead of a precomputed library. Its contiguity veto is a working
    reference for the annealer's adjacency and green-up penalties, and its policy algebra is
    the Event Monitor, so a prescription expressed there is expressible inside a trajectory.
  - **It does not replace simulated annealing.** It commits cycle by cycle — the lock-step
    shape ARTEMIS deliberately retired — and offers no optimality claim.
  - **Caveats:** Julia API only (no `MSPOLICY` keyfile parsing); validated on an
    EastCascades example, not SN; the coordinator-level clearcut wiring and `hvproj` are
    documented follow-ups. Treat as a research arm, not a pipeline stage.
- **In-memory lock-step coupling** (`ppe_run_landscape_live!`) — stands pause at shared
  boundaries while staying resident, using Julia tasks and channels. The FFE carbon
  corruption ARTEMIS measured came from stopping and restarting from disk, so this route has
  no such artifact. It makes the retired iterative-coupling design cheap again if
  state-dependent, cross-stand silviculture ever re-enters scope.
- **`ADDTREES` regeneration bridge** — at the establishment seam FVSjl writes a stand
  summary, invokes an external model, and schedules the `PLANT`/`NATURAL` cards it returns.
  That is the natural hook for ARTEMIS's nearest-neighbour reestablishment when it must
  depend on post-harvest state; in-process, the external call could become a Julia callback.
  Precomputed libraries are already served by static `Plant` records and `apportion_by_sdi`.
- **Stands that FVSsn cannot run** (§4) — a coverage gain, not a speed one, and the one
  most likely to change reported landscape totals.
- **`faithful = false`** — fixes documented FVS source bugs (its ledger lists D36–D38).
  Keep `faithful = true` in production for comparability; use the other mode for sensitivity
  studies and for probing suspected FVS behaviour.
- **Prior art inside FVSjl** — `apps/forest-explorer` already resolves TreeMap 2022 pixels
  to FIA plots, runs FVSjl threaded over an AOI under a management plan, and aggregates
  per-cycle species/DBH metrics. It is a miniature of ARTEMIS stages 1–4 and worth reading
  before writing the worker; its `simulate(plotInputs, scenario, cycles)` boundary is the
  same seam as §6.1.

## 9. Non-goals and open questions

- **Non-goal:** other variants. ARTEMIS is SN-only.
- **Non-goal:** replacing the annealer with any FVS-side scheduler (§8).
- **Open:** does the Windows `FVSsn.dll` SIGFPE on the same 33 stands? This decides whether
  §4 is a coverage *gain* or a coverage *bug already in our published libraries*.
- **Open:** which FVS build is the reference of record (B3).
- **Open:** will upstream take B1/B2 and a stepping API for phase 3? If not, ARTEMIS needs a
  pinned fork, and the pin belongs in `config/projection.yaml` either way.
- **Open:** the 8-to-16-thread scaling is weak (589 -> 774 runs/s). Per-stand setup and GC
  dominate; whether a sysimage and batched input reads improve it is untested.
