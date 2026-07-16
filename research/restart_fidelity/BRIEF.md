# Research Brief — FVS Restart Fidelity Spike

**Read this fully before acting.** Spec: `docs/superpowers/specs/2026-07-16-parallel-fvs-runs-design.md`.
Plan: `docs/superpowers/plans/2026-07-16-restart-fidelity-spike.md`.

## 0. The question

Does FVS stop/restart preserve FFE carbon/fuels state, and does in-process pause reproduce a
continuous run exactly? The answer selects the parallel-FVS orchestration architecture, so
nothing else is built until this reports.

**Why it matters:** `putstd.f` and `getstd.f` include an identical 36-common list that contains
**no FFE commons** (`FMCOM`/`FMFCOM`/`FMPARM`/`FMPROP`/`FMSVCM`). `FMCOM` holds `CWD` (coarse
woody debris), `CWD2B` ("debris-in-waiting … scheduled to become down debris at appropriate
points in the future"), and `ALLDWN` (snag falldown timing). If that reading is right,
restart-based global barriers silently corrupt carbon — and `config/projection.yaml` requires
`carbon_extension: true` with all five IPCC pools.

This is **source-read evidence**. The spike tests it.

## 1. Operating environment (verified)

- **Working dir:** `/home/chazm/projects/artemis-model/.claude/worktrees/parallel-fvs-runs`,
  branch `claude-code/parallel-fvs-runs`. Other agents work in sibling worktrees — never touch
  `/home/chazm/projects/artemis-model` (on `main`) or `/tmp/artemis-model-codex-parallel-fvs-runs`.
- **FVS runs on Windows only.** WSL2 cannot run `FVSsn.dll`.
- **Run dir:** `/mnt/c/FVS/artemis_spike` (Windows-visible; `C:\FVS\artemis_spike`).
  Contains `FVS_Data.db` (copied from `/mnt/c/FVS/Artemis_project/`), per-arm `.key`, `.db`, `.out`.
- **Python:** `uv run python ...` / `uv run pytest ...`, from the worktree root.
- **DuckDB is the data layer.** FVS writes SQLite; DuckDB attaches it via the `sqlite` extension.

## 2. How to run

```bash
# Stage (once)
mkdir -p /mnt/c/FVS/artemis_spike
cp /mnt/c/FVS/Artemis_project/FVS_Data.db /mnt/c/FVS/artemis_spike/FVS_Data.db

# Generate a keyfile
uv run python -c "
from research.restart_fidelity import make_keyfiles, paths
(paths.SPIKE_DIR_WSL / 'arm_a.key').write_text(make_keyfiles.build_keyfile('a', 'arm_a.db'))
"

# Run an arm (a | b | c | d)
cd /mnt/c/FVS/artemis_spike && \
  /mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe \
  "$(wslpath -w /home/chazm/projects/artemis-model/.claude/worktrees/parallel-fvs-runs/research/restart_fidelity/run_arms.R)" a

# Compare
uv run pytest tests/test_restart_fidelity.py -v
```

## 3. Fixture (verified)

`Stand_CN = '43393151010478'` → `Stand_ID = 010006100083`, `Inv_Year 1999`, `Variant SN`,
**39 tree records**. Schedule: `InvYear 1999`, `TimeInt 5`, `NumCycle 4` → 1999–2019, exactly
four 5-year cycles. **No management**, so any arm-to-arm difference is state transfer alone.

Input DB tables are `FVS_StandInit_Plot` / `FVS_TreeInit_Plot`; the placeholder is
`%Stand_CN%` (underscores), **not** `%StandCN%`.

## 4. Arms and predictions

| Arm | Mechanism | Prediction |
|---|---|---|
| **A** Continuous | `fvsRun()`, 1999→2019, no stops | reference truth |
| **B** In-process pause | `fvsRun(2, yr)` at 2004/09/14, resume same process; never serialized | **exact match to A** |
| **C** Stop/restart | `--stoppoint=2,yr,file` → `--restart=file`, across processes | base matches; **carbon diverges** |
| **D** Tree-list rebuild | export/re-inject tree list each segment | diverges broadly |

**B ≠ A is the most important possible outcome** — it would collapse *both* candidate
architectures, outranking anything the restart arms show.

## 5. Carbon keywords (verified against Open-FVS source)

- `FMIn` — base keyword option 104 (`initre.f:3726`), opens the FFE section, closed by `End`.
- `CarbRept` — `fmin.f` option 44, no parameters.
- `CarbCalc` — `fmin.f` option 46. FLD1 `0`=FFE method, FLD2 `0`=imperial (US tons/acre).
- `CarbReDB` — `dbsin.f` option 29, writes `FVS_Carbon`. Calls `FMLNKD` and **errors unless FFE
  is active**, so `FMIn` is mandatory, not optional.

**Never** report carbon as "no difference" when `FVS_Carbon` is absent — `compare_arms.assert_carbon_present`
raises `CarbonTableMissing` for exactly this reason.

## 6. Results

### Arm A — continuous (2026-07-16): PASS

Return code `0` (rFVS: "FVS is in good running state"). Note the plan predicted `2` ("finished
processing all stands"); `0` is returned after a completed single-stand run and is not an error.

`arm_a.db` tables: `FVS_Carbon`, `FVS_Cases`, `FVS_Hrv_Carbon`, `FVS_InvReference`, `FVS_Summary2`.
(`CarbReDB` sets both `ICMRPT` and `ICHRPT`, hence `FVS_Hrv_Carbon` too.)

| Year | BA | Tpa | SDI |
|---|---|---|---|
| 1999 | 90.53 | 1212.0 | 170 |
| 2004 | 106.46 | 1179.3 | 204 |
| 2009 | 128.15 | 1087.0 | 256 |
| 2014 | 148.73 | 1003.3 | 296 |
| 2019 | 158.92 | 878.5 | 310 |

`FVS_Carbon` pools: `Aboveground_Total_Live, Aboveground_Merch_Live, Belowground_Live,
Belowground_Dead, Standing_Dead, Forest_Down_Dead_Wood, Forest_Floor, Forest_Shrub_Herb,
Total_Stand_Carbon, Total_Removed_Carbon, Carbon_Released_From_Fire`.

**Watch `Forest_Down_Dead_Wood` and `Standing_Dead`** — these are the `FMCOM` `CWD`/`ALLDWN`
pools that `putstd` does not serialize. If the finding holds, arm C diverges here first.

### Arm B — in-process pause (2026-07-16): EXACT MATCH

Paused at stop point 2 in 2004/2009/2014 (`restartcode: 2` each), resumed in-process.
**Summary max |delta| = 0.0, carbon max |delta| = 0.0** across all pools and all five cycles.
In-process pause is transparent; segmentation itself introduces no error.

### Arm C — stop/restart (2026-07-16): BASE EXACT, CARBON CORRUPTED

Chain: `c1` store@2004 → `c2` restart+store@2009 → `c3` restart+store@2014 → `c4` restart→end.

Base metrics exact (max |delta| = 0.0). `Forest_Shrub_Herb` collapses to a constant **0.02** at
every restart; `Total_Stand_Carbon` understated by 3.93–5.38 tons/acre (**−8.3%** at 2014).
`Forest_Down_Dead_Wood` and `Standing_Dead` **survive exactly** — contradicting the prediction.

Likely mechanism: `COVTYP` (in `FMCOM`, absent from `putstd`) is lost; `fmcba.f:63-69` estimates
herb/shrub from forest type, falling back to the hardwood default `(0.01+0.03) × 0.5 = 0.02`.

**Operational gotcha (cost an hour):** a negative restart code is a *signal*, not a result.
`cmdline.f:299` sets `restartcode = -originalRestartCode` after `getstd`; `fvsRun()` restores the
stand and returns **without running**. Call `fvsRun()` again. Calling it once silently produces a
**0-byte store file** that fails at the *next* segment with "Premature end of data". Use
`run_until_settled()`.

### Arm D — tree-list rebuild (2026-07-16): MECHANISM DOES NOT COMPOSE

`fvsSetTreeAttrs` rejected the frame ("Length of 'id' must be 324") — FVS triples records during
growth (108 captured vs 324 live). The rejection is a **warning, not an error**, so the injection
silently no-op'd and segments ran as independent fresh runs (duplicate DB rows: 1999 ×3, 2004 ×2).

**Arm D's numbers are confounded — not a measurement of rebuild divergence.** The rFVS in-memory
tree-list path is closed on mechanism grounds; a faithful test needs `.tre` regeneration.

### Decision

See `notes/restart-fidelity-findings.md`. Restart-based global barriers are incompatible with
valid carbon. Note the narrow variant: only one pool breaks, via one variable (`COVTYP`) —
carrying it across the barrier may suffice, but that is **untested**.
