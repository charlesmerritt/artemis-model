# Claude Code on the web: session setup

How an ARTEMIS session is provisioned in the ephemeral web container, and the one
egress constraint that silently breaks the test suite there.

## What runs at session start

`.claude/hooks/session-start.sh`, registered as a `SessionStart` hook in
`.claude/settings.json`. It no-ops unless `CLAUDE_CODE_REMOTE=true`, so local
checkouts are unaffected. Three steps:

1. `uv sync` — also fetches CPython 3.14, which the base image does not ship
   (its system Python is 3.11).
2. Stage DuckDB's `sqlite_scanner` extension (see below).
3. `git config core.hooksPath .githooks` — a fresh clone does not pick up the
   tracked large-file guard on its own.

The hook is synchronous and idempotent; a warm container completes it in under a
second.

## Gotcha: `INSTALL sqlite` cannot reach extensions.duckdb.org

`research/restart_fidelity/compare_arms.py:attach_arms` runs
`INSTALL sqlite; LOAD sqlite;` before `ATTACH ... (TYPE sqlite)`. DuckDB resolves
that by downloading from `extensions.duckdb.org`, which the sandbox egress policy
denies with HTTP 403 — on both http and https, so it is a policy denial, not a
TLS or plain-HTTP problem. All nine tests in `tests/test_restart_fidelity.py`
fail with:

```
_duckdb.HTTPException: HTTP Error: Failed to download extension "sqlite_scanner"
at URL "http://extensions.duckdb.org/v1.5.4/linux_amd64/sqlite_scanner.duckdb_extension.gz" (HTTP 403)
```

This is environment-only. The extension resolves normally on a workstation with
open network access, so the failure does not reproduce locally.

**Fix:** DuckDB publishes the same binary on PyPI, which *is* reachable. The hook
installs `duckdb-extension-sqlite-scanner` at DuckDB's own version and copies the
`.duckdb_extension` file into `~/.duckdb/extensions/v<version>/<platform>/`.
`INSTALL sqlite` then finds it already present and skips the download.

Two details worth keeping:

- **Version must match exactly.** DuckDB rejects an extension built for another
  version, so the hook reads `duckdb.__version__` and `PRAGMA platform` rather
  than hardcoding `1.5.4`/`linux_amd64`. A DuckDB bump in `uv.lock` needs no hook
  change.
- **The wheel is installed to a temp dir, not the project venv.** `uv sync`
  prunes anything absent from `uv.lock`, so installing it into `.venv` would be
  undone by the next sync. Staging the binary under `~/.duckdb` survives that.

## Expected test result in the web container

`uv run pytest tests/` → **82 passed, 21 skipped**. The skips are the normal
data-availability guards: `/mnt/d` is not mounted, and `treemap_2022_fl.tif` /
the `clearcut_ag` interim CSVs are not present. Nothing should fail.
