# FVS five-county growth smoke notebook

## ⚠️ Current repo state (verified 2026-07-14) — notebook does not run as committed

**Renamed `notebooks/FVS_5county_growth_smoke.ipynb` → `.ipynb.old` on 2026-07-14** to mark it
retired/broken. Two independent blockers:

1. **Missing pipeline modules (hard blocker, import-time).** The notebook's first code cell
   imports `select_smoke_stands`, `write_smoke_keyfiles` from `pipeline.s4_fvs.keyword_builder`,
   plus `pipeline.s4_fvs.probe_libraries`, `pipeline.s4_fvs.run_smoke`, and
   `pipeline.s4_fvs.summarize_smoke`. **None of these four modules exist in the repo** — only
   `pipeline/s4_fvs/paint_fvs_to_raster.py` is present. They were never committed to git history
   (`git log --all -- '*keyword_builder*'` is empty) and are not gitignored. The
   `tests/test_s4_fvs_keyword_builder.py` file this note references below is likewise absent.
   The notebook therefore raises `ModuleNotFoundError` at cell 1, before touching any data. The
   "Added pipeline helpers" and "Verification run" sections below describe code that must have
   existed in an uncommitted/lost worktree — treat them as a spec for what to re-create, not as
   the current repo contents.
2. **External drive unmounted.** `INPUT_DB = /mnt/d/TreeMap_Chaz/output2020/FIA_5county_consolidated.db`
   lives on the external PERSEUS_DAT drive, which is currently a stale mount (`/mnt/d` reads fail
   with "No such device"). Even with the modules restored, the notebook needs that drive remounted.

To restore the notebook: recover/rewrite the four `pipeline/s4_fvs/*` helper modules (and their
test), then remount the D: drive.

## Purpose

Sprint artifact for growing TreeMap/FIA stands in the five-county Florida AOI with FVS Southern (`SN`). The implementation uses the existing consolidated input DB rather than rebuilding TreeMap or FIA inputs.

Primary input:

- `/mnt/d/TreeMap_Chaz/output2020/FIA_5county_consolidated.db`

Primary notebook:

- `notebooks/FVS_5county_growth_smoke.ipynb.old` (renamed from `.ipynb`, 2026-07-14 — broken)

## Added pipeline helpers (⚠️ currently MISSING from the repo — see warning above)

- `pipeline/s4_fvs/keyword_builder.py` — deterministic stand selection and no-management keyfile rendering for plot- or condition-level FIA2FVS tables.
- `pipeline/s4_fvs/generate_smoke_keyfiles.py` — CLI for writing per-stand `.key` files plus `manifest.csv`.
- `pipeline/s4_fvs/probe_libraries.py` — checks local `FVS/fvs-modern/lib/FVS*.so` libraries for ctypes/fvs2py loadability.
- `pipeline/s4_fvs/run_smoke.py` — fvs2py runner with optional subprocess isolation so FVS segfaults do not kill the whole batch.
- `pipeline/s4_fvs/summarize_smoke.py` — summarizes `FVS_Cases`, `FVS_Summary2`, and `FVS_Error` from output SQLite DBs.
- `tests/test_s4_fvs_keyword_builder.py` — unit tests for pure keyfile-builder behavior.

## Generated artifacts

Linux/WSL keyfiles:

- `data/interim/fvs/smoke_no_management/manifest.csv`
- one keyfile directory per selected stand, e.g. `data/interim/fvs/smoke_no_management/134677819010854/134677819010854.key`

Windows FVS GUI bundle:

- `/mnt/d/TreeMap_Chaz/artemis_fvs_smoke_no_management/manifest.csv`
- keyfiles render DB paths as `D:/...`, e.g. `D:/TreeMap_Chaz/output2020/FIA_5county_consolidated.db`

Library probe:

- `data/interim/fvs/library_probe.csv`

## Current FVS runtime finding

`FVS/fvs-modern/lib/FVSsn.so` loads successfully via both `ctypes` and the local `FVS/fvs2py` wrapper. However, actual DB-backed runs fail in this environment.

Reproduction command:

```bash
uv run python -m pipeline.s4_fvs.run_smoke \
  --manifest data/interim/fvs/smoke_no_management/manifest.csv \
  --fvs-lib FVS/fvs-modern/lib/FVSsn.so \
  --limit 3 \
  --isolate-subprocess
```

Observed result:

- `run_status.csv` records `returncode=-11` for the first three stands.
- FVS stdout includes `OPEN FAILED FOR   17` and `ErrMsg = unrecognized token: "'"`.
- Partial output DBs may contain `FVS_Error`, but no `FVS_Cases` or `FVS_Summary2` rows.

An exact successful-looking historical keyfile shape pointed at `/mnt/d/TreeMap_Chaz/Proc_TreeMap2000_fvs/FVS_Data.db` failed the same way with the local Linux `FVSsn.so`, so this appears to be a local library/DB-extension/runtime issue rather than only the new keyfile template.

## DuckDB viewing step

The notebook now includes a DuckDB export/viewer section after `FVS_Summary2` collection. It normalizes `FVS_Summary2` into lower snake-case columns, preserves FIA/FVS IDs as strings, casts integer-like and continuous fields for DuckDB, writes `fvs_summary2_duckdb.csv`, loads table `fvs_summary2` into `fvs_summary2.duckdb`, creates `fvs_summary2_preview`, and starts DuckDB's web UI when `START_DUCKDB_UI = True`.

After Windows FVS writes output, rerun the notebook cells from “Summarize FVS output” through “DuckDB export and viewer.” By default, with `RUN_LOCAL_FVS = False`, the notebook reads the Windows output SQLite database at `C:\FVS\Artemis_project\FVSOut.db` (`/mnt/c/FVS/Artemis_project/FVSOut.db` from WSL). `C:\FVS\Artemis_project\FVS_Data.db` is the project/input database; `FVSOut.db` contains `FVS_Cases`, `FVS_Summary2`, and `FVS_Error`. The manifest-based SQLite output path remains the fallback for local Linux/fvs2py runs.

## Windows GUI handoff

Use the Windows bundle if local Linux FVS remains blocked:

1. Open `C:\FVS\FVSSoftware\FVS.lnk` or run `C:\FVS\FVSSoftware\FVS_Icon.bat`.
2. Start with keyfile `D:\TreeMap_Chaz\artemis_fvs_smoke_no_management\134677819010854\134677819010854.key` if the GUI exposes a keyword/keyfile run option.
3. If keyfile import is unavailable, open/use the project under `C:\FVS\Artemis_project\` or upload/open the source input DB `D:\TreeMap_Chaz\output2020\FIA_5county_consolidated.db` in FVS OnLocal, choose variant `SN`, and select plot-level tables `FVS_STANDINIT_PLOT` and `FVS_TREEINIT_PLOT`.
4. Select the first 10 stands from the generated `manifest.csv`, request Summary version 2 database output, and run.
5. After Windows FVS writes output to `C:\FVS\Artemis_project\FVSOut.db`, rerun the notebook cells from “Summarize FVS output” through “DuckDB export and viewer.” The current WSL-visible path is `/mnt/c/FVS/Artemis_project/FVSOut.db`; it is a SQLite database with `FVS_Cases`, `FVS_Summary2`, and `FVS_Error`.

## Verification run

```bash
uv run pytest tests/test_s4_fvs_keyword_builder.py -q
uv run python -m pipeline.s4_fvs.probe_libraries --variant SN --check-fvs2py
uv run jupyter nbconvert --to notebook --execute notebooks/FVS_5county_growth_smoke.ipynb --output FVS_5county_growth_smoke.executed.ipynb --ExecutePreprocessor.timeout=120
```

The executed notebook completed with local FVS disabled (`RUN_LOCAL_FVS = False`).
