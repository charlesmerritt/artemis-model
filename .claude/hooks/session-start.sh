#!/usr/bin/env bash
# SessionStart hook: prepare an ARTEMIS session in Claude Code on the web so
# `uv run pytest tests/` and `uv run ruff check .` work without network access
# to hosts outside the sandbox egress policy.
#
# Safe to re-run: every step is idempotent.
set -euo pipefail

# Local checkouts already have a working environment; only the ephemeral web
# container needs building from scratch.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

repo_root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$repo_root"

log() { printf 'session-start: %s\n' "$1" >&2; }

# 1. Python environment. `uv sync` also fetches the CPython 3.14 required by
# pyproject.toml, which the base image does not ship.
log "syncing python environment (uv sync)"
uv sync

# 2. DuckDB's sqlite_scanner extension.
#
# research/restart_fidelity/compare_arms.py runs `INSTALL sqlite; LOAD sqlite;`
# to ATTACH FVS SQLite outputs. DuckDB resolves that by downloading from
# extensions.duckdb.org, which the sandbox egress policy denies (403), so nine
# tests in tests/test_restart_fidelity.py fail with an HTTPException.
#
# DuckDB publishes the same extension binary on PyPI, which is reachable. Stage
# it into the local extension directory; `INSTALL` then finds it already present
# and skips the download entirely.
#
# The wheel is installed into a throwaway directory rather than the project venv
# so the synced environment keeps matching uv.lock exactly.
log "staging duckdb sqlite_scanner extension"
duckdb_version="$(uv run python -c 'import duckdb; print(duckdb.__version__)')"
duckdb_platform="$(uv run python -c 'import duckdb; print(duckdb.execute("PRAGMA platform").fetchone()[0])')"
extension_dir="${HOME}/.duckdb/extensions/v${duckdb_version}/${duckdb_platform}"

if [ -f "${extension_dir}/sqlite_scanner.duckdb_extension" ]; then
  log "sqlite_scanner already staged for duckdb ${duckdb_version}"
else
  staging="$(mktemp -d)"
  trap 'rm -rf "$staging"' EXIT
  # Version-matched: DuckDB refuses an extension built for another version.
  uv pip install --target "$staging" --quiet \
    "duckdb-extension-sqlite-scanner==${duckdb_version}"
  mkdir -p "$extension_dir"
  cp "${staging}/duckdb_extension_sqlite_scanner/extensions/v${duckdb_version}/sqlite_scanner.duckdb_extension" \
    "$extension_dir/"
  log "staged sqlite_scanner ${duckdb_version} for ${duckdb_platform}"
fi

# 3. Tracked git hook that rejects accidentally staged files over 99 MiB. Part
# of the README quickstart; a fresh clone does not pick it up on its own.
log "enabling tracked git hooks"
git config core.hooksPath .githooks

log "ready"
