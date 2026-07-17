# FVS Restart Fidelity — how to run

Operational guide. For **what was found and why it matters**, read
[`BRIEF.md`](BRIEF.md) and [`notes/restart-fidelity-findings.md`](../../notes/restart-fidelity-findings.md).

**TL;DR of the finding:** stop/restart and in-process pause both reproduce a continuous FVS run
**exactly** on stand values (BA / Tpa / SDI, max |Δ| = 0.0), including across 5 stands. Only
**carbon** diverges, which is why `config/projection.yaml` now sets `carbon_extension: false`.

---

## 1. Prerequisites

| Requirement | Value | Notes |
|---|---|---|
| FVS | Windows host only | WSL2 **cannot** run `FVSsn.dll` |
| Rscript | `/mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe` | R 4.5.0, `rFVS` 2024.7.1 |
| FVS binaries | `C:\FVS\FVSSoftware\FVSbin` | `FVSsn.dll` (Southern variant) |
| Input DB | `/mnt/c/FVS/Artemis_project/FVS_Data.db` | ~1.0 GB |
| Python | `uv run python` / `uv run pytest` | 3.14, needs `duckdb` |

The run directory **must be Windows-visible** (under `/mnt/c/`), because FVS resolves paths on the
Windows side. Path translation lives in exactly one place: `paths.py`.

Check the bridge works before anything else:

```bash
/mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe -e 'library(rFVS); cat("rFVS ok\n")'
```

## 2. Stage the run directory (once)

```bash
mkdir -p /mnt/c/FVS/artemis_spike
cp /mnt/c/FVS/Artemis_project/FVS_Data.db /mnt/c/FVS/artemis_spike/FVS_Data.db   # ~10 s
```

## 3. Generate keyfiles

```bash
# single-stand arms (a/b/c/d) -- fixture 43393151010478, 1999-2019, four 5-yr cycles
uv run python -c "
from research.restart_fidelity import make_keyfiles, paths
for a in ('a','b','c','d'):
    (paths.SPIKE_DIR_WSL / f'arm_{a}.key').write_text(make_keyfiles.build_keyfile(a, f'arm_{a}.db'))
"

# multi-stand arms (m/n) -- 5 SN stands, 2019-2039
uv run python -c "
from research.restart_fidelity import make_keyfiles, paths
for t, db in (('m','arm_m.db'), ('n','arm_n.db')):
    (paths.SPIKE_DIR_WSL / f'arm_{t}.key').write_text(make_keyfiles.build_multistand_keyfile('a', db))
"
```

## 4. Run an arm

All arms run through one driver. Define a shortcut first:

```bash
R="/mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe"
S="$(wslpath -w "$PWD/research/restart_fidelity/run_arms.R")"
cd /mnt/c/FVS/artemis_spike     # FVS must run from the Windows-visible dir
```

| Arm | Command | What it does |
|---|---|---|
| **A** | `"$R" "$S" a` | continuous 20-yr reference (1 stand) |
| **B** | `"$R" "$S" b` | in-process pause at 2004/09/14, resume same process |
| **C** | `for s in c1 c2 c3 c4; do "$R" "$S" $s 2; done` | stop/restart chain, **stop point 2**, one process per segment |
| **E** | `for s in c1 c2 c3 c4; do "$R" "$S" $s 6 e; done` | same as C at **stop point 6** (writes `arm_e.*`) |
| **D** | `"$R" "$S" d` | tree-list rebuild — **known broken**, see BRIEF.md |
| **M** | `"$R" "$S" m` | multi-stand continuous reference (5 stands) |
| **N** | `for s in n1 n2 n3 n4; do "$R" "$S" $s 6; done` | multi-stand stop/restart chain (5 stands) |

Segment args: `run_arms.R <seg> [stop_code] [output_tag]`.

Arms C/E/N run **one process per segment** on purpose — FVS keeps stand state in global commons,
so a fresh process per segment is both the cleaner test and the shape production would use.

Re-running an arm: delete its artifacts first, or rows **append** and you get duplicates.

```bash
rm -f arm_c.db arm_c.out arm_c_*.rst      # before re-running arm C
```

## 5. Compare arms

All comparison is DuckDB. FVS writes SQLite; DuckDB attaches it directly, so every arm is diffed
in one query with nothing loaded into memory.

```bash
uv run python -c "
import duckdb
from research.restart_fidelity import compare_arms
con = duckdb.connect()
compare_arms.attach_arms(con, {
    'a': '/mnt/c/FVS/artemis_spike/arm_a.db',
    'b': '/mnt/c/FVS/artemis_spike/arm_b.db',
})
s = compare_arms.diff_summary(con, 'a', 'b')
print('base metric max |delta|:', s[['ba_delta','tpa_delta','sdi_delta']].abs().max().max())
print(s.to_string(index=False))
"
```

`diff_summary` returns per-year `ba_delta` / `tpa_delta` / `sdi_delta` (left − right).
`diff_carbon` returns per-year, per-pool deltas — **kept separate on purpose**: a summary-only
comparison shows a restart arm passing while carbon is silently corrupt. `assert_carbon_present`
raises `CarbonTableMissing` so an absent table can never read as "no difference".

## 6. Run the tests

```bash
uv run pytest tests/test_restart_fidelity.py -v   # 11 passed, no FVS needed
uv run pytest tests/ -q                           # full suite
```

The tests exercise keyfile generation and the DuckDB comparison against small in-memory fixtures.
**They do not run FVS**, so they pass on any machine — the FVS arms are run by hand per above.

## 7. Files

| File | Responsibility |
|---|---|
| `paths.py` | WSL↔Windows path constants + `to_windows()`. The only place translation lives. |
| `make_keyfiles.py` | Keyfile generation. `build_keyfile` (1 stand), `build_multistand_keyfile` (5 stands). |
| `compare_arms.py` | DuckDB attach + diff. `diff_summary`, `diff_carbon`, `assert_carbon_present`. |
| `run_arms.R` | rFVS driver for every arm. Runs on Windows. |
| `outputs/` | Committed result `.txt` files — the measurements, independent of the run dir. |
| `BRIEF.md` | Research context, per-arm results, decisions. |

## 8. Gotchas that will cost you an hour

These are measured, not guesses. Each one cost real debugging time.

1. **A negative restart code is a signal, not a result.** After `getstd` restores a stand,
   `cmdline.f:299` sets `restartcode = -originalRestartCode`. `fvsRun()` reloads the stand and
   **returns without running it**. You must call `fvsRun()` again. Calling it once silently writes
   a **0-byte store file**, and the failure surfaces one segment later as
   `"Premature end of data"`. Use `run_until_settled()`.

2. **`fvsRun()` returns `0` per stand, `2` when all stands are done.** For multi-stand runs, loop
   until `2` — `0` means another stand is pending, not that the run finished.

3. **`CarbReDB` errors unless FFE is active.** It calls `FMLNKD`, so the `FMIn` section is
   mandatory whenever carbon output is requested (`dbsin.f` option 29).

4. **The input DB placeholder is `%Stand_CN%`** (underscores), not `%StandCN%`, and the tables are
   `FVS_StandInit_Plot` / `FVS_TreeInit_Plot`.

5. **`fvsSetTreeAttrs` cannot resize a stand.** It rejects any tree list whose record count differs
   from the live stand (FVS triples records during growth). The rejection is a **warning, not an
   error**, so a bad injection silently no-ops. This is why arm D does not work.

6. **Stop point choice does not fix carbon.** Arm E (stop point 6) diverges identically to arm C;
   it only shifts which rows are written pre-store vs post-restart.
