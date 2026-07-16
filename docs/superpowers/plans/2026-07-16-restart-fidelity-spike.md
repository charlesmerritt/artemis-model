# Restart Fidelity Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine empirically whether FVS stop/restart preserves FFE carbon state, and whether in-process pause reproduces a continuous run exactly — the finding that selects the parallel-FVS orchestration architecture.

**Architecture:** Four arms run the same single-stand fixture over 1999–2019 (four 5-year cycles, no management) through different state-transfer mechanisms. Each arm writes its own SQLite `FVSOut.db`. DuckDB attaches all arms and diffs them. Python generates keyfiles and compares; R (`rFVS`) drives FVS on the Windows host.

**Tech Stack:** Python 3.14 + uv + pytest + DuckDB 1.5.4; R 4.5.0 + rFVS 2024.7.1 on Windows; FVS Southern variant (`FVSsn.dll`).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-16-parallel-fvs-runs-design.md`. Read it before starting.
- **Worktree:** `/home/chazm/projects/artemis-model/.claude/worktrees/parallel-fvs-runs`, branch `claude-code/parallel-fvs-runs`. Do all work here. Other agents are working in sibling worktrees — never touch `/home/chazm/projects/artemis-model` (on `main`) or `/tmp/artemis-model-codex-parallel-fvs-runs`.
- **Python:** `uv run python ...`, `uv run pytest ...`. Never call `python` directly.
- **DuckDB is the data layer.** All aggregation/comparison over FVS output goes through DuckDB, never pandas-in-memory merges or raw `sqlite3` queries.
- **FVS runs on Windows only.** WSL2 cannot run `FVSsn.dll`. Invoke via `/mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe`. FVS's working directory must be Windows-visible (under `/mnt/c/`).
- **Fixture (verified):** `Stand_CN = '43393151010478'`, `Stand_ID = 010006100083`, `Inv_Year 1999`, `Variant SN`, 39 tree records.
- **Input DB (verified):** `/mnt/c/FVS/Artemis_project/FVS_Data.db` (1.0 GB). Tables `FVS_StandInit_Plot`, `FVS_TreeInit_Plot`. Placeholder is `%Stand_CN%` (with underscores), NOT `%StandCN%`.
- **Carbon keywords (verified from Open-FVS source):** `FMIn` is base keyword option 104 and opens the FFE section, closed by `End`. `CarbRept` (fmin.f option 44) takes no parameters. `CarbCalc` (option 46) FLD1: `0`=FFE method, FLD2: `0`=imperial. `CarbReDB` (dbsin.f option 29) writes table `FVS_Carbon`; it calls `FMLNKD` and **errors if FFE is not active**, so `FMIn` is mandatory.
- **Never** report a carbon comparison as "no difference" when the `FVS_Carbon` table is absent. A missing table must fail loudly. This is the silent-corruption failure mode the whole spike exists to catch.

---

## File Structure

| File | Responsibility |
|---|---|
| `research/restart_fidelity/paths.py` | WSL↔Windows path constants + translation. The only place path translation lives. |
| `research/restart_fidelity/make_keyfiles.py` | Emit arm A/B/C/D keyfiles for the fixture stand. Pure string generation. |
| `research/restart_fidelity/compare_arms.py` | DuckDB attach + diff of `FVS_Summary2` and `FVS_Carbon` across arms. |
| `research/restart_fidelity/run_arms.R` | rFVS driver executed on Windows. Runs the four arms. |
| `research/restart_fidelity/BRIEF.md` | Context and how to run, matching `research/mgmt_units/BRIEF.md`. |
| `research/restart_fidelity/outputs/` | Per-arm `FVSOut.db`, restart files, result CSVs. Gitignored except small CSVs. |
| `tests/test_restart_fidelity.py` | Unit tests for keyfile generation and DuckDB comparison. No FVS required. |

---

### Task 1: Path translation and keyfile generation

**Files:**
- Create: `research/restart_fidelity/__init__.py` (empty)
- Create: `research/restart_fidelity/paths.py`
- Create: `research/restart_fidelity/make_keyfiles.py`
- Test: `tests/test_restart_fidelity.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `paths.SPIKE_DIR_WSL: Path` = `Path("/mnt/c/FVS/artemis_spike")`
  - `paths.SPIKE_DIR_WIN: str` = `"C:\\FVS\\artemis_spike"`
  - `paths.to_windows(p: Path) -> str`
  - `make_keyfiles.build_keyfile(arm: str, out_db: str, num_cycle: int = 4) -> str`
  - `make_keyfiles.ARMS: tuple[str, ...]` = `("a", "b", "c", "d")`

- [ ] **Step 1: Write the failing test**

Create `tests/test_restart_fidelity.py`:

```python
from __future__ import annotations

from pathlib import Path

from research.restart_fidelity import make_keyfiles, paths


def test_to_windows_translates_mnt_c():
    assert paths.to_windows(Path("/mnt/c/FVS/artemis_spike/a.key")) == r"C:\FVS\artemis_spike\a.key"


def test_keyfile_has_carbon_keywords():
    # CarbReDB errors unless FFE is active, so FMIn must be present.
    kf = make_keyfiles.build_keyfile("a", "arm_a.db")
    assert "FMIn" in kf
    assert "CarbRept" in kf
    assert "CarbReDB" in kf
    assert kf.index("FMIn") < kf.index("CarbRept")


def test_keyfile_uses_verified_fixture_and_schema():
    kf = make_keyfiles.build_keyfile("a", "arm_a.db")
    assert "43393151010478" in kf
    assert "FVS_StandInit_Plot" in kf
    assert "FVS_TreeInit_Plot" in kf
    # The input DB placeholder is %Stand_CN%, not %StandCN%.
    assert "%Stand_CN%" in kf
    assert "%StandCN%" not in kf


def test_keyfile_is_four_five_year_cycles():
    kf = make_keyfiles.build_keyfile("a", "arm_a.db")
    assert "InvYear       1999" in kf
    assert "TimeInt                 5" in kf
    assert "NumCycle      4" in kf


def test_keyfile_names_its_output_db():
    assert "arm_c.db" in make_keyfiles.build_keyfile("c", "arm_c.db")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_restart_fidelity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'research.restart_fidelity'`

- [ ] **Step 3: Write minimal implementation**

Create `research/restart_fidelity/__init__.py` as an empty file.

Create `research/restart_fidelity/paths.py`:

```python
"""WSL <-> Windows path translation for the restart-fidelity spike.

FVS runs on the Windows host and cannot see WSL paths, so every run directory
must live under /mnt/c (Windows-visible). This module is the only place that
translation happens.
"""

from __future__ import annotations

from pathlib import Path

SPIKE_DIR_WSL = Path("/mnt/c/FVS/artemis_spike")
SPIKE_DIR_WIN = r"C:\FVS\artemis_spike"

FVS_DATA_DB = "FVS_Data.db"
FVS_DATA_DB_SRC = Path("/mnt/c/FVS/Artemis_project/FVS_Data.db")

RSCRIPT_EXE = Path("/mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe")
FVS_BIN_WIN = r"C:\FVS\FVSSoftware\FVSbin"


def to_windows(p: Path) -> str:
    """Convert a /mnt/<drive>/... WSL path to a Windows path."""
    parts = p.parts
    if len(parts) < 3 or parts[0] != "/" or parts[1] != "mnt":
        raise ValueError(f"not a /mnt/<drive> path: {p}")
    drive = parts[2].upper()
    rest = "\\".join(parts[3:])
    return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
```

Create `research/restart_fidelity/make_keyfiles.py`:

```python
"""Generate FVS keyfiles for the four restart-fidelity spike arms.

All arms share one fixture stand and an identical 1999-2019 schedule (four
5-year cycles, no management), so any difference between arms is attributable
to the state-transfer mechanism alone.

Carbon is enabled deliberately: CarbReDB writes the FVS_Carbon table, and it
errors unless FFE is active, so the FMIn section is mandatory (verified in
Open-FVS dbsin.f option 29 / fmin.f options 44 and 46).
"""

from __future__ import annotations

ARMS: tuple[str, ...] = ("a", "b", "c", "d")

STAND_CN = "43393151010478"
STAND_ID = "010006100083"
INV_YEAR = 1999
CYCLE_YEARS = 5

_TEMPLATE = """\
!!title: restart_fidelity_arm_{arm}
StdIdent
{stand_id}               RestartFidelity_arm_{arm}
StandCN
{stand_cn}
MgmtId
A001
InvYear       {inv_year}
TimeInt                 {cycle_years}
NumCycle      {num_cycle}

FMIn
CarbRept
CarbCalc          0         0
End

DataBase
DSNOut
{out_db}
Summary        2
CarbReDB
End

Database
DSNIn
{in_db}
StandSQL
SELECT *
FROM FVS_StandInit_Plot
WHERE Stand_CN = '%Stand_CN%'
EndSQL
TreeSQL
SELECT *
FROM FVS_TreeInit_Plot
WHERE Stand_CN ='%Stand_CN%'
EndSQL
END
SPLabel
  All_FIA_Plots
Process
Stop
"""


def build_keyfile(arm: str, out_db: str, num_cycle: int = 4) -> str:
    """Return the keyfile text for one arm.

    `arm` is one of ARMS; `out_db` is the SQLite output filename FVS writes,
    relative to the run directory.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; expected one of {ARMS}")
    return _TEMPLATE.format(
        arm=arm,
        stand_id=STAND_ID,
        stand_cn=STAND_CN,
        inv_year=INV_YEAR,
        cycle_years=CYCLE_YEARS,
        num_cycle=num_cycle,
        out_db=out_db,
        in_db="FVS_Data.db",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_restart_fidelity.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add research/restart_fidelity/__init__.py research/restart_fidelity/paths.py research/restart_fidelity/make_keyfiles.py tests/test_restart_fidelity.py
git commit -m "Add keyfile generator and path translation for restart-fidelity spike"
```

---

### Task 2: DuckDB arm comparison

**Files:**
- Create: `research/restart_fidelity/compare_arms.py`
- Modify: `tests/test_restart_fidelity.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1 (independent module).
- Produces:
  - `compare_arms.attach_arms(con, arm_dbs: dict[str, Path]) -> None`
  - `compare_arms.diff_summary(con, left: str, right: str) -> pandas.DataFrame` — columns `Year, ba_delta, tpa_delta, sdi_delta`
  - `compare_arms.diff_carbon(con, left: str, right: str) -> pandas.DataFrame` — columns `Year, pool, left_value, right_value, delta`
  - `compare_arms.assert_carbon_present(con, alias: str) -> None` — raises `CarbonTableMissing`
  - `compare_arms.CarbonTableMissing` (exception)

**Context for the implementer:** FVS writes SQLite. DuckDB reads it via the `sqlite` extension — verified working against the real `FVSOut.db`:

```python
con.execute("INSTALL sqlite; LOAD sqlite;")
con.execute("ATTACH 'FVSOut.db' AS a (TYPE sqlite, READ_ONLY);")
```

Note `fvs.sqlite_master` does NOT work through the attach; use `information_schema.tables` filtered by `table_catalog`.

`FVS_Summary2` columns (verified): `CaseID, StandID, Year, RmvCode, Age, Tpa, TPrdTpa, BA, SDI, ZeideSDI, ReinekeSDI, SDIMax, RDSDI, CCF, ...`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_restart_fidelity.py`:

```python
import duckdb
import pytest

from research.restart_fidelity import compare_arms


def _make_arm(tmp_path: Path, name: str, ba: float, carbon: float | None) -> Path:
    """Build a tiny SQLite DB shaped like FVS output."""
    import sqlite3

    db = tmp_path / f"{name}.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE FVS_Summary2 (StandID TEXT, Year INT, Tpa REAL, BA REAL, SDI REAL)")
    conn.execute("INSERT INTO FVS_Summary2 VALUES ('S1', 2004, 100.0, ?, 50.0)", (ba,))
    if carbon is not None:
        conn.execute("CREATE TABLE FVS_Carbon (StandID TEXT, Year INT, Aboveground_Total REAL)")
        conn.execute("INSERT INTO FVS_Carbon VALUES ('S1', 2004, ?)", (carbon,))
    conn.commit()
    conn.close()
    return db


def _con(tmp_path: Path, arms: dict[str, Path]):
    con = duckdb.connect()
    compare_arms.attach_arms(con, arms)
    return con


def test_identical_arms_have_zero_summary_delta(tmp_path):
    arms = {"a": _make_arm(tmp_path, "a", 80.0, 10.0), "b": _make_arm(tmp_path, "b", 80.0, 10.0)}
    df = compare_arms.diff_summary(_con(tmp_path, arms), "a", "b")
    assert df["ba_delta"].abs().max() == 0.0


def test_summary_delta_detects_difference(tmp_path):
    arms = {"a": _make_arm(tmp_path, "a", 80.0, 10.0), "b": _make_arm(tmp_path, "b", 85.0, 10.0)}
    df = compare_arms.diff_summary(_con(tmp_path, arms), "a", "b")
    assert df["ba_delta"].abs().max() == pytest.approx(5.0)


def test_carbon_diff_is_reported_even_when_summary_matches(tmp_path):
    """The silent-corruption case: base metrics identical, carbon diverged."""
    arms = {"a": _make_arm(tmp_path, "a", 80.0, 10.0), "b": _make_arm(tmp_path, "b", 80.0, 7.5)}
    con = _con(tmp_path, arms)
    assert compare_arms.diff_summary(con, "a", "b")["ba_delta"].abs().max() == 0.0
    carbon = compare_arms.diff_carbon(con, "a", "b")
    assert carbon["delta"].abs().max() == pytest.approx(2.5)


def test_missing_carbon_table_fails_loudly(tmp_path):
    """A missing FVS_Carbon must raise, never read as 'no difference'."""
    arms = {"a": _make_arm(tmp_path, "a", 80.0, 10.0), "b": _make_arm(tmp_path, "b", 80.0, None)}
    con = _con(tmp_path, arms)
    with pytest.raises(compare_arms.CarbonTableMissing):
        compare_arms.assert_carbon_present(con, "b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_restart_fidelity.py -v -k "carbon or delta"`
Expected: FAIL — `ImportError: cannot import name 'compare_arms'`

- [ ] **Step 3: Write minimal implementation**

Create `research/restart_fidelity/compare_arms.py`:

```python
"""Compare restart-fidelity spike arms with DuckDB.

Each arm writes its own SQLite FVSOut.db. DuckDB attaches them all and diffs
in one query, so no arm is ever loaded into memory wholesale.

Carbon is diffed separately from base metrics on purpose: a summary-only
comparison would show a restart arm passing while FVS_Carbon is silently
corrupt, which is the exact failure this spike exists to catch.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

SUMMARY = "FVS_Summary2"
CARBON = "FVS_Carbon"


class CarbonTableMissing(RuntimeError):
    """Raised when an arm has no FVS_Carbon table.

    Never downgrade this to a warning: absent carbon must not be mistaken for
    unchanged carbon.
    """


def attach_arms(con: duckdb.DuckDBPyConnection, arm_dbs: dict[str, Path]) -> None:
    """Attach each arm's SQLite output under its own alias."""
    con.execute("INSTALL sqlite; LOAD sqlite;")
    for alias, db in arm_dbs.items():
        con.execute(f"ATTACH '{db}' AS {alias} (TYPE sqlite, READ_ONLY);")


def _has_table(con: duckdb.DuckDBPyConnection, alias: str, table: str) -> bool:
    rows = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_catalog = ? AND table_name = ?",
        [alias, table],
    ).fetchall()
    return len(rows) > 0


def assert_carbon_present(con: duckdb.DuckDBPyConnection, alias: str) -> None:
    if not _has_table(con, alias, CARBON):
        raise CarbonTableMissing(
            f"arm {alias!r} has no {CARBON} table - the run did not enable "
            f"FMIn/CarbRept + CarbReDB, so carbon cannot be compared"
        )


def diff_summary(con: duckdb.DuckDBPyConnection, left: str, right: str) -> pd.DataFrame:
    """Per-year base-metric deltas (left - right)."""
    return con.execute(
        f"""
        SELECT l.Year AS Year,
               l.BA  - r.BA  AS ba_delta,
               l.Tpa - r.Tpa AS tpa_delta,
               l.SDI - r.SDI AS sdi_delta
        FROM {left}.{SUMMARY} l
        JOIN {right}.{SUMMARY} r USING (StandID, Year)
        ORDER BY l.Year
        """
    ).df()


def diff_carbon(con: duckdb.DuckDBPyConnection, left: str, right: str) -> pd.DataFrame:
    """Per-year carbon-pool deltas (left - right), unpivoted to one row per pool."""
    assert_carbon_present(con, left)
    assert_carbon_present(con, right)

    pools = [
        c[0]
        for c in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_catalog = ? AND table_name = ? "
            "AND column_name NOT IN ('StandID', 'Year', 'CaseID')",
            [left, CARBON],
        ).fetchall()
    ]
    if not pools:
        raise CarbonTableMissing(f"arm {left!r} {CARBON} has no pool columns")

    unions = "\nUNION ALL\n".join(
        f"""SELECT l.Year AS Year, '{p}' AS pool,
                   l."{p}" AS left_value, r."{p}" AS right_value,
                   l."{p}" - r."{p}" AS delta
            FROM {left}.{CARBON} l
            JOIN {right}.{CARBON} r USING (StandID, Year)"""
        for p in pools
    )
    return con.execute(f"SELECT * FROM ({unions}) ORDER BY Year, pool").df()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_restart_fidelity.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add research/restart_fidelity/compare_arms.py tests/test_restart_fidelity.py
git commit -m "Add DuckDB arm comparison with carbon-missing guard"
```

---

### Task 3: Arm A — continuous 20-year run (first real FVS execution)

**Files:**
- Create: `research/restart_fidelity/run_arms.R`
- Create: `research/restart_fidelity/BRIEF.md`

**Interfaces:**
- Consumes: `make_keyfiles.build_keyfile("a", "arm_a.db")` output, staged into the run dir.
- Produces: `/mnt/c/FVS/artemis_spike/arm_a.db` containing `FVS_Summary2` and `FVS_Carbon`.

**This is the first real FVS growth run in this project from WSL.** Prior work only verified that `Rscript`, `rFVS`, and `FVSsn.dll` load. Expect setup friction; that is the point of doing arm A alone first.

- [ ] **Step 1: Stage the run directory**

```bash
mkdir -p /mnt/c/FVS/artemis_spike
cp /mnt/c/FVS/Artemis_project/FVS_Data.db /mnt/c/FVS/artemis_spike/FVS_Data.db
uv run python -c "
from pathlib import Path
from research.restart_fidelity import make_keyfiles, paths
p = paths.SPIKE_DIR_WSL / 'arm_a.key'
p.write_text(make_keyfiles.build_keyfile('a', 'arm_a.db'))
print('wrote', p)
"
```

Expected: `wrote /mnt/c/FVS/artemis_spike/arm_a.key`

- [ ] **Step 2: Write the arm A driver**

Create `research/restart_fidelity/run_arms.R`:

```r
# Restart-fidelity spike driver. Runs on the Windows host via Rscript.exe.
#
# Usage: Rscript.exe run_arms.R <arm>
# Arms: a = continuous; b = in-process pause; c = stop/restart; d = tree-list rebuild
#
# Stop point 2 = "just after the first call to the Event Monitor" (rFVS::fvsRun),
# which is where fvsCutNow() applies management. Arms B and C pause there.

library(rFVS)

FVSBIN <- "C:\\FVS\\FVSSoftware\\FVSbin"
SPIKE  <- "C:\\FVS\\artemis_spike"
PAUSE_YEARS <- c(2004, 2009, 2014)

setwd(SPIKE)

args <- commandArgs(trailingOnly = TRUE)
arm  <- if (length(args) > 0) args[1] else "a"

run_arm_a <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=arm_a.key")
  rtn <- fvsRun()
  cat("arm a return code:", rtn, "\n")
  invisible(rtn)
}

if (arm == "a") run_arm_a()
```

- [ ] **Step 3: Run arm A**

```bash
cd /mnt/c/FVS/artemis_spike && \
  /mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe \
  "$(wslpath -w /home/chazm/projects/artemis-model/.claude/worktrees/parallel-fvs-runs/research/restart_fidelity/run_arms.R)" a
```

Expected: `arm a return code: 2` (2 = FVS finished processing all stands).

If the return code is `1`, FVS errored — read `arm_a.db`'s `FVS_Error` table, or the `.out` file in the run dir, before changing anything.

- [ ] **Step 4: Verify carbon was actually produced**

This is the gate. If `FVS_Carbon` is absent, the keywords are wrong and every later arm is meaningless.

```bash
uv run python -c "
import duckdb
from research.restart_fidelity import compare_arms
con = duckdb.connect()
compare_arms.attach_arms(con, {'a': '/mnt/c/FVS/artemis_spike/arm_a.db'})
print(con.execute(\"SELECT table_name FROM information_schema.tables WHERE table_catalog='a' ORDER BY 1\").fetchall())
compare_arms.assert_carbon_present(con, 'a')
print(con.execute('SELECT Year, BA, Tpa FROM a.FVS_Summary2 ORDER BY Year').df().to_string(index=False))
"
```

Expected: table list includes `FVS_Carbon` and `FVS_Summary2`; `assert_carbon_present` does not raise; summary shows years 1999, 2004, 2009, 2014, 2019.

- [ ] **Step 5: Write BRIEF.md and commit**

Create `research/restart_fidelity/BRIEF.md` documenting: the spike's question, the four arms and their predictions (copy the arms table from the spec), the run directory (`/mnt/c/FVS/artemis_spike`), the exact Rscript invocation from Step 3, and the fixture stand. State that FVS runs on Windows only and that carbon must be diffed separately.

```bash
git add research/restart_fidelity/run_arms.R research/restart_fidelity/BRIEF.md
git commit -m "Add arm A continuous run; verify FVS_Carbon is produced"
```

---

### Task 4: Arm B — in-process pause (the primary assertion)

**Files:**
- Modify: `research/restart_fidelity/run_arms.R` (add `run_arm_b`)

**Interfaces:**
- Consumes: `paths`, `make_keyfiles.build_keyfile("b", "arm_b.db")`.
- Produces: `/mnt/c/FVS/artemis_spike/arm_b.db`.

**Why this matters most:** arm B pauses at stop point 2 in 2004/2009/2014 and resumes in the same process, so FVS state never leaves memory. It should equal arm A **exactly**. If B ≠ A, in-process pause is broken and *both* candidate architectures in the spec collapse — that outcome is more important than anything the restart arms show.

- [ ] **Step 1: Stage arm B's keyfile**

```bash
uv run python -c "
from research.restart_fidelity import make_keyfiles, paths
p = paths.SPIKE_DIR_WSL / 'arm_b.key'
p.write_text(make_keyfiles.build_keyfile('b', 'arm_b.db'))
print('wrote', p)
"
```

- [ ] **Step 2: Add the arm B driver**

Add to `research/restart_fidelity/run_arms.R`, before the dispatch line:

```r
run_arm_b <- function() {
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=arm_b.key")
  for (yr in PAUSE_YEARS) {
    rtn <- fvsRun(2, yr)                       # stop point 2, at year yr
    cat("arm b paused at", yr, "rtn:", rtn, "code:", fvsGetRestartcode(), "\n")
    if (rtn != 0) {
      cat("arm b: unexpected return", rtn, "at", yr, "\n")
      break
    }
    s <- fvsGetSummary()                        # read state at the barrier
    cat("  summary rows:", nrow(s), "\n")
  }
  rtn <- fvsRun()                               # run to completion
  cat("arm b final return code:", rtn, "\n")
  invisible(rtn)
}
```

Change the dispatch line to:

```r
if (arm == "a") run_arm_a() else if (arm == "b") run_arm_b()
```

- [ ] **Step 3: Run arm B**

```bash
cd /mnt/c/FVS/artemis_spike && \
  /mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe \
  "$(wslpath -w /home/chazm/projects/artemis-model/.claude/worktrees/parallel-fvs-runs/research/restart_fidelity/run_arms.R)" b
```

Expected: three pause lines (2004/2009/2014) then `arm b final return code: 2`.

- [ ] **Step 4: Assert B equals A exactly**

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
c = compare_arms.diff_carbon(con, 'a', 'b')
print('summary max |delta|:', s[['ba_delta','tpa_delta','sdi_delta']].abs().max().max())
print('carbon  max |delta|:', c['delta'].abs().max())
print(s.to_string(index=False))
"
```

Expected: both maxima are `0.0`.

**If they are not zero, stop and report before continuing.** A non-zero B-vs-A delta invalidates the spike's premise and must be understood (wrong stop point? keyfile drift between arms? `fvsRun()` resumption semantics?) rather than worked around.

- [ ] **Step 5: Commit**

```bash
git add research/restart_fidelity/run_arms.R
git commit -m "Add arm B in-process pause; assert exact equality with continuous arm A"
```

---

### Task 5: Arm C — stop/restart across processes (the carbon test)

**Files:**
- Modify: `research/restart_fidelity/run_arms.R` (add `run_arm_c`)

**Interfaces:**
- Consumes: `make_keyfiles.build_keyfile("c", "arm_c.db")`.
- Produces: `/mnt/c/FVS/artemis_spike/arm_c.db`, plus restart files `arm_c_<year>.rst`.

**What this tests:** `fvsSetCmdLine` parses `--stoppoint=<code>,<year>,<file>` and `--restart=<file>` (verified in `cmdline.f:125-140`). `--restart=` ignores the keyword file (`cmdline.f:148-152`). At a stop point FVS writes a one-time header then calls `putstd` per stand (`cmdline.f:443-451`).

**Prediction:** base metrics match arm A; **carbon diverges**, because `putstd.f`/`getstd.f` include an identical 36-common list that contains no FFE commons (`FMCOM`/`FMFCOM`/`FMPARM`/`FMPROP`/`FMSVCM`), so `CWD`, `CWD2B` ("debris-in-waiting … scheduled to become down debris at appropriate points in the future"), and `ALLDWN` are not preserved.

Whether rFVS and `--restart` interoperate cleanly is itself **untested** and is an outcome of this task.

- [ ] **Step 1: Stage arm C's keyfile**

```bash
uv run python -c "
from research.restart_fidelity import make_keyfiles, paths
p = paths.SPIKE_DIR_WSL / 'arm_c.key'
p.write_text(make_keyfiles.build_keyfile('c', 'arm_c.db'))
print('wrote', p)
"
```

- [ ] **Step 2: Add the arm C driver**

Add to `research/restart_fidelity/run_arms.R`, before the dispatch line:

```r
run_arm_c <- function() {
  # Segment 1: run from the keyword file, stop at 2004 and store all stands.
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=arm_c.key --stoppoint=2,2004,arm_c_2004.rst")
  rtn <- fvsRun()
  cat("arm c stored at 2004, rtn:", rtn, "\n")

  # Later segments: restart from the previous file, store at the next barrier.
  segs <- list(c("arm_c_2004.rst", "2009", "arm_c_2009.rst"),
               c("arm_c_2009.rst", "2014", "arm_c_2014.rst"))
  for (s in segs) {
    fvsSetCmdLine(paste0("--restart=", s[1], " --stoppoint=2,", s[2], ",", s[3]))
    rtn <- fvsRun()
    cat("arm c restarted from", s[1], "stored at", s[2], "rtn:", rtn, "\n")
  }

  # Final segment: restart from 2014 and run to completion (no stop point).
  fvsSetCmdLine("--restart=arm_c_2014.rst")
  rtn <- fvsRun()
  cat("arm c final return code:", rtn, "\n")
  invisible(rtn)
}
```

Change the dispatch line to:

```r
if (arm == "a") run_arm_a() else if (arm == "b") run_arm_b() else if (arm == "c") run_arm_c()
```

- [ ] **Step 3: Run arm C**

```bash
cd /mnt/c/FVS/artemis_spike && \
  /mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe \
  "$(wslpath -w /home/chazm/projects/artemis-model/.claude/worktrees/parallel-fvs-runs/research/restart_fidelity/run_arms.R)" c
```

Expected: a store line for 2004, two restart lines, then a final return code.

If `--restart` and rFVS conflict (for example the DLL must be reloaded between segments, or `--restart` cannot be combined with a fresh `fvsSetCmdLine`), **record the exact failure in BRIEF.md rather than forcing it** — "restart is not drivable from rFVS" is itself a finding that constrains the architecture.

- [ ] **Step 4: Measure the divergence**

```bash
uv run python -c "
import duckdb
from research.restart_fidelity import compare_arms
con = duckdb.connect()
compare_arms.attach_arms(con, {
    'a': '/mnt/c/FVS/artemis_spike/arm_a.db',
    'c': '/mnt/c/FVS/artemis_spike/arm_c.db',
})
s = compare_arms.diff_summary(con, 'a', 'c')
c = compare_arms.diff_carbon(con, 'a', 'c')
print('=== BASE METRICS (expect ~0) ===')
print(s.to_string(index=False))
print()
print('=== CARBON (predicted to diverge) ===')
print(c[c['delta'].abs() > 0].to_string(index=False))
print('carbon max |delta|:', c['delta'].abs().max())
" | tee /home/chazm/projects/artemis-model/.claude/worktrees/parallel-fvs-runs/research/restart_fidelity/outputs/arm_c_vs_a.txt
```

Record the result either way. **A clean carbon result is as important as a dirty one**: it would falsify the spec's central source-read finding and revive the global-barrier architecture.

- [ ] **Step 5: Commit**

```bash
mkdir -p research/restart_fidelity/outputs
git add research/restart_fidelity/run_arms.R research/restart_fidelity/outputs/arm_c_vs_a.txt
git commit -m "Add arm C stop/restart; measure carbon divergence against continuous arm A"
```

---

### Task 6: Arm D — tree-list rebuild

**Files:**
- Modify: `research/restart_fidelity/run_arms.R` (add `run_arm_d`)

**Interfaces:**
- Consumes: `make_keyfiles.build_keyfile("d", "arm_d.db")`.
- Produces: `/mnt/c/FVS/artemis_spike/arm_d.db`.

**Purpose:** quantify the cost of rebuilding FVS input from an exported tree list between segments, closing that option with evidence rather than assertion. Expected to diverge broadly — it loses calibration (`CALCOM`), RNG (`RANCOM`), establishment (`ESTREE`), and all FFE state, since only live-tree attributes are carried forward.

`fvsGetTreeAttrs()` returns the current tree list; `fvsSetTreeAttrs()` writes it back. Arm D restarts a fresh FVS at each barrier and injects the previous segment's trees.

- [ ] **Step 1: Stage arm D's keyfile**

```bash
uv run python -c "
from research.restart_fidelity import make_keyfiles, paths
p = paths.SPIKE_DIR_WSL / 'arm_d.key'
p.write_text(make_keyfiles.build_keyfile('d', 'arm_d.db'))
print('wrote', p)
"
```

- [ ] **Step 2: Add the arm D driver**

Add to `research/restart_fidelity/run_arms.R`, before the dispatch line:

```r
run_arm_d <- function() {
  # Segment 1: grow to the first barrier and capture the live tree list.
  fvsLoad("FVSsn", bin = FVSBIN)
  fvsSetCmdLine("--keywordfile=arm_d.key")
  rtn <- fvsRun(2, PAUSE_YEARS[1])
  cat("arm d segment 1 rtn:", rtn, "\n")
  trees <- fvsGetTreeAttrs(c("id", "species", "dbh", "ht", "cratio", "tpa"))
  cat("  captured", nrow(trees), "tree records\n")

  # Later segments: fresh FVS each time, tree list injected, everything else lost.
  for (yr in PAUSE_YEARS[-1]) {
    fvsSetCmdLine("--keywordfile=arm_d.key")
    rtn <- fvsRun(2, yr)
    fvsSetTreeAttrs(trees)
    trees <- fvsGetTreeAttrs(c("id", "species", "dbh", "ht", "cratio", "tpa"))
    cat("arm d rebuilt at", yr, "rtn:", rtn, "trees:", nrow(trees), "\n")
  }
  rtn <- fvsRun()
  cat("arm d final return code:", rtn, "\n")
  invisible(rtn)
}
```

Change the dispatch line to:

```r
if (arm == "a") run_arm_a() else
if (arm == "b") run_arm_b() else
if (arm == "c") run_arm_c() else
if (arm == "d") run_arm_d()
```

- [ ] **Step 3: Run arm D**

```bash
cd /mnt/c/FVS/artemis_spike && \
  /mnt/c/FVS/FVSSoftware/R/R-4.5.0/bin/x64/Rscript.exe \
  "$(wslpath -w /home/chazm/projects/artemis-model/.claude/worktrees/parallel-fvs-runs/research/restart_fidelity/run_arms.R)" d
```

Expected: per-segment tree counts, then a final return code. Arm D is the most likely arm to fail outright; if `fvsSetTreeAttrs` rejects the frame, record the exact error — the mechanism's fragility is part of the finding.

- [ ] **Step 4: Measure the divergence**

```bash
uv run python -c "
import duckdb
from research.restart_fidelity import compare_arms
con = duckdb.connect()
compare_arms.attach_arms(con, {
    'a': '/mnt/c/FVS/artemis_spike/arm_a.db',
    'd': '/mnt/c/FVS/artemis_spike/arm_d.db',
})
print('=== BASE METRICS ===')
print(compare_arms.diff_summary(con, 'a', 'd').to_string(index=False))
print('=== CARBON ===')
c = compare_arms.diff_carbon(con, 'a', 'd')
print('carbon max |delta|:', c['delta'].abs().max())
" | tee /home/chazm/projects/artemis-model/.claude/worktrees/parallel-fvs-runs/research/restart_fidelity/outputs/arm_d_vs_a.txt
```

- [ ] **Step 5: Commit**

```bash
git add research/restart_fidelity/run_arms.R research/restart_fidelity/outputs/arm_d_vs_a.txt
git commit -m "Add arm D tree-list rebuild; quantify state loss against continuous arm A"
```

---

### Task 7: Record the finding and the architecture decision

**Files:**
- Create: `notes/restart-fidelity-findings.md`
- Modify: `notes/README.md` (add index entry)
- Modify: `research/restart_fidelity/BRIEF.md` (append results)

**Interfaces:**
- Consumes: `outputs/arm_c_vs_a.txt`, `outputs/arm_d_vs_a.txt`, and the A-vs-B result from Task 4.
- Produces: the architecture decision that unblocks orchestrator design.

- [ ] **Step 1: Write the findings note**

Create `notes/restart-fidelity-findings.md` with these sections:

- **Result table:** one row per arm (A/B/C/D), columns: mechanism, base-metric max |delta|, carbon max |delta|, verdict.
- **What was executed vs. inferred:** state plainly which claims are now measured and which remain source-read only.
- **Architecture decision:** which candidate the evidence selects, per the spec's falsification rules —
  - B ≠ A → in-process pause broken; both candidates collapse; escalate.
  - C carbon ≡ A carbon → the `putstd` source reading was wrong; global barriers viable; Candidate 2 proceeds.
  - C carbon ≠ A carbon → finding confirmed; choose Candidate 1, outer-loop signalling, or patching `putstd`.
- **Caveat (required):** one deterministic stand with 39 tree records. A *dirty* carbon result is conclusive; a *clean* one is provisional and needs broader fixtures (mortality, establishment, FFE fuels) before any global-barrier orchestrator is built on it.

- [ ] **Step 2: Add the index entry**

Add one line to the `## Index` list in `notes/README.md`, matching the existing style (link + em-dash + hook):

```markdown
- [Restart fidelity findings](restart-fidelity-findings.md) — measured whether FVS stop/restart preserves FFE carbon state; four-arm spike (continuous / in-process pause / stop-restart / tree-list rebuild) and the architecture decision it selects.
```

- [ ] **Step 3: Verify the full test suite still passes**

Run: `uv run pytest tests/test_restart_fidelity.py -v`
Expected: PASS — 9 passed

- [ ] **Step 4: Commit**

```bash
git add notes/restart-fidelity-findings.md notes/README.md research/restart_fidelity/BRIEF.md
git commit -m "Record restart-fidelity spike results and architecture decision"
```

- [ ] **Step 5: Report to the user**

Report: the four-arm result table, which architecture the evidence selects, and whether the spec's central `putstd`/FFE finding was confirmed or falsified. If B ≠ A, lead with that — it outranks every other result.

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Arm A continuous | Task 3 |
| Arm B in-process pause, exact match | Task 4 |
| Arm C stop/restart, carbon diverges | Task 5 |
| Arm D tree-list rebuild | Task 6 |
| Carbon diffed separately from base metrics | Task 2 (`diff_carbon`), asserted in Task 4/5/6 |
| Missing `FVS_Carbon` fails loudly | Task 2 (`assert_carbon_present`), gated in Task 3 Step 4 |
| DuckDB as data layer | Task 2 |
| Comparison unit-tested without FVS | Task 2 |
| A-vs-B exact equality as primary check | Task 4 Step 4 |
| Path translation isolated | Task 1 (`paths.py`) |
| Results promoted to `notes/` | Task 7 |
| Single-fixture limitation recorded | Task 7 Step 1 |

Runtime and restart-file size (spec's operational-cost metric) are captured incidentally by the arm C/D output files; not broken out as a task, since they do not gate the architecture decision.

**Placeholder scan:** no TBD/TODO; every code step contains complete code; every command has an expected result.

**Type consistency:** `attach_arms`/`diff_summary`/`diff_carbon`/`assert_carbon_present`/`CarbonTableMissing` are defined in Task 2 and used with the same names in Tasks 3–6. `build_keyfile(arm, out_db)` and `paths.SPIKE_DIR_WSL` are defined in Task 1 and used identically in Tasks 3–6. R functions `run_arm_a/b/c/d` accumulate in one dispatch chain, rewritten in full at each task.
