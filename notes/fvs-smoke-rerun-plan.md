# FVS 5-county smoke rerun implementation plan

Goal: run a tiny, reproducible no-management FVS Southern (`SN`) smoke test for 5–10 stands from the existing 5-county Florida input database, using 10 five-year cycles and `Summary 2` database output.

## Scope

Input database:

- `/mnt/d/TreeMap_Chaz/output2020/FIA_5county_consolidated.db`

Use plot-level FVS-ready tables first:

- `FVS_STANDINIT_PLOT`
- `FVS_TREEINIT_PLOT`

Do not rebuild the AOI, TreeMap raster, or full statewide DB for this smoke test.

## Acceptance criteria

A run is demo-ready when:

1. 5–10 keyfiles are generated under `data/interim/fvs/smoke_no_management/`.
2. Each keyfile contains:
   - `NumCycle 10`
   - `TimeInt 0 5`
   - `Summary 2`
   - `DSNIn /mnt/d/TreeMap_Chaz/output2020/FIA_5county_consolidated.db`
   - plot-level `StandSQL` and `TreeSQL` queries.
3. FVS runs each keyfile with the Southern variant library (`FVSsn.so`).
4. Each stand produces an output SQLite DB with:
   - `FVS_Cases`
   - `FVS_Summary2`
   - optional `FVS_Error` warnings captured, not hidden.
5. A summary CSV reports stand ID, years, row counts, variant/version, and warning counts.

## Target files to add

```text
pipeline/s4_fvs/
  keyword_builder.py       # pure functions for selecting stands and rendering keyfile text
  generate_smoke_keyfiles.py
  run_smoke.py             # fvs2py runner; one FVS instance per stand
  summarize_smoke.py       # SQLite output QC summary

tests/
  test_s4_fvs_keyword_builder.py
```

Keep these scripts small and standalone; do not generalize to statewide batching yet.

## Step 1 — select smoke stands

Use a deterministic query against the input DB. Start with 10; fall back to 5 if runtime is slow.

```sql
SELECT
  s.STAND_CN,
  s.STAND_ID,
  CAST(s.INV_YEAR AS INTEGER) AS INV_YEAR,
  COUNT(t.STAND_CN) AS TREE_ROWS
FROM FVS_STANDINIT_PLOT s
JOIN FVS_TREEINIT_PLOT t
  ON t.STAND_CN = s.STAND_CN
WHERE s.VARIANT = 'SN'
GROUP BY s.STAND_CN, s.STAND_ID, s.INV_YEAR
ORDER BY s.STAND_CN
LIMIT :limit;
```

Verified on 2026-06-11: the DB has 688 `FVS_STANDINIT_PLOT` rows and all have `VARIANT = 'SN'`.

## Step 2 — render one keyfile per stand

Output layout:

```text
data/interim/fvs/smoke_no_management/
  manifest.csv
  134677819010854/
    134677819010854.key
    FVSOut.db
  ...
```

Minimal keyfile template:

```text
StdIdent
{stand_id:<26} smoke
StandCN
{stand_cn}
MgmtID
no_mgmt
InvYear       {inv_year}
NumCycle     10
TimeInt       0        5

DataBase
DSNOut
{output_db}
Summary       2
End

DataBase
DSNIn
{input_db}
StandSQL
SELECT *
FROM FVS_STANDINIT_PLOT
WHERE Stand_CN = '%Stand_CN%'
EndSQL
TreeSQL
SELECT *
FROM FVS_TREEINIT_PLOT
WHERE Stand_CN = '%Stand_CN%'
EndSQL
End

Process
Stop
```

Notes:

- Use absolute paths for `DSNIn` and `DSNOut`.
- Keep carbon/FIAVBC/fuels outputs out of this first smoke test. Add them only after `Summary 2` is reliable.
- Include `StdIdent` plus `StandCN`; prior working keyfiles used both.

## Step 3 — unit-test the keyfile builder

Test only pure behavior first:

- selected stand rows preserve `STAND_CN` and `STAND_ID` as strings.
- rendered keyfile contains `NumCycle 10`, `TimeInt 0 5`, `Summary 2`, `DSNIn`, `DSNOut`, `Process`, and `Stop`.
- SQL uses `FVS_STANDINIT_PLOT` and `FVS_TREEINIT_PLOT`.

Command:

```bash
uv run pytest tests/test_s4_fvs_keyword_builder.py -q
```

## Step 4 — generate keyfiles

Proposed command:

```bash
uv run python -m pipeline.s4_fvs.generate_smoke_keyfiles \
  --input-db /mnt/d/TreeMap_Chaz/output2020/FIA_5county_consolidated.db \
  --out-dir data/interim/fvs/smoke_no_management \
  --limit 10
```

Expected outputs:

- `manifest.csv` with `stand_cn,stand_id,inv_year,tree_rows,keyfile,output_db`.
- one keyfile directory per stand.

## Step 5 — run FVS

Preferred runtime: `fvs2py` in its FVS Docker/dev image, because it should provide compatible FVS shared libraries.

Inside the runtime, run:

```bash
uv run python -m pipeline.s4_fvs.run_smoke \
  --manifest data/interim/fvs/smoke_no_management/manifest.csv \
  --fvs-lib /usr/local/lib/FVSsn.so
```

Implementation shape for `run_smoke.py`:

```python
from fvs2py import FVS

fvs = FVS(fvs_lib)
fvs.load_keyfile(keyfile, check_single_stand=False)
fvs.run()
fvs._unload_fvs()
```

Use a fresh `FVS` instance per stand. It is slower but safer for a smoke demo.

Host fallback, only if library dependencies resolve:

```bash
uv pip install -e /home/chazm/projects/FVS/fvs2py
uv run python -m pipeline.s4_fvs.run_smoke \
  --manifest data/interim/fvs/smoke_no_management/manifest.csv \
  --fvs-lib /home/chazm/projects/FVS/fvs-modern/lib/FVSsn.so
```

## Step 6 — summarize outputs

Proposed command:

```bash
uv run python -m pipeline.s4_fvs.summarize_smoke \
  --manifest data/interim/fvs/smoke_no_management/manifest.csv \
  --out-csv data/interim/fvs/smoke_no_management/smoke_summary.csv
```

Summary columns:

- `stand_cn`
- `stand_id`
- `output_db`
- `case_rows`
- `variant`
- `fvs_version`
- `summary_rows`
- `first_year`
- `last_year`
- `warning_rows`
- `fatal_error_rows`

Useful QC SQL for each output DB:

```sql
SELECT COUNT(*) FROM FVS_Cases;
SELECT Variant, Version, COUNT(*) FROM FVS_Cases GROUP BY Variant, Version;
SELECT COUNT(*), MIN(Year), MAX(Year) FROM FVS_Summary2;
SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='FVS_Error';
```

Demo-ready expectation: `FVS_Cases` has 1 row per output DB, `Variant = 'SN'`, and `FVS_Summary2` spans roughly 50 projection years from the stand inventory year.

## Step 7 — presentation artifact

For tomorrow, create one compact table and one line plot from `smoke_summary.csv` + the per-stand `FVS_Summary2` tables:

- line plot: basal area (`BA`) by year, one line per stand.
- line plot or table: TPA by year.
- warning count table grouped by warning message.

Frame this as: “FVS is running locally for Florida TreeMap/FIA stands; next step is scaling the same reproducible runner to all 688 5-county stands, then statewide Florida.”

## Known risks

- FVS runtime may be the bottleneck. If host `FVSsn.so` fails, use the `fvs2py` Docker runtime.
- FVS may emit warnings. Capture them in `FVS_Error`; do not suppress them.
- FIA inventory year is not the ARTEMIS 2022 base year. For the smoke test, document that outputs start from each stand’s `INV_YEAR`; base-year alignment is a later production task.
- This is plot-level only. Condition-level runs are a later comparison after the smoke test works.
