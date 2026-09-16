#!/usr/bin/env bash
# Bring an ARTEMIS environment up to a known state, and report what it can reach.
#
# Every step is idempotent, so this is safe to re-run on a workstation, in a fresh
# clone, inside the Docker image (which runs it at build time), or from a
# SessionStart hook. It configures what it can and reports what it cannot: a
# missing data drive, absent R2 credentials, or a blocked DuckDB extension host
# are environmental facts, not failures, and none of them stop the script.
#
# Only a broken toolchain is fatal — no uv, or dependencies that will not install.
#
# Usage:
#   scripts/setup-env.sh            configure and report
#   scripts/setup-env.sh --check    report only, change nothing

set -euo pipefail

CHECK_ONLY=0
case "${1:-}" in
  --check) CHECK_ONLY=1 ;;
  "") ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

warnings=0

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; warnings=$((warnings + 1)); }
fail() { printf '  \033[31mfail\033[0m  %s\n' "$1" >&2; exit 1; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# Prefer the interpreter already in .venv. `uv run` would create one on demand,
# which is a side effect --check promises not to have.
have_python() { [[ -x .venv/bin/python ]]; }
py() { .venv/bin/python "$@"; }

# ---------------------------------------------------------------------------
step "Toolchain"

command -v uv >/dev/null 2>&1 || fail "uv not found — install from https://docs.astral.sh/uv/"
ok "uv $(uv --version | awk '{print $2}')"

# .python-version pins the interpreter; uv fetches it if the host lacks it.
if (( CHECK_ONLY )); then
  if [[ -x .venv/bin/python ]]; then
    ok "environment present ($(.venv/bin/python --version))"
  else
    warn "no .venv yet — run without --check to create it"
  fi
else
  uv sync --frozen --quiet
  ok "dependencies synced ($(.venv/bin/python --version), pinned by .python-version)"
fi

# ---------------------------------------------------------------------------
step "Repository hooks"

# The hook rejects staged blobs over 99 MiB. core.hooksPath is per-clone local
# config, so every fresh checkout starts without it — including this one.
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  warn "not a git repository — skipping hooks (expected inside the Docker image)"
elif (( CHECK_ONLY )); then
  if [[ "$(git config --get core.hooksPath || true)" == ".githooks" ]]; then
    ok "core.hooksPath = .githooks"
  else
    warn "core.hooksPath unset — the large-file pre-commit hook is not active"
  fi
else
  git config core.hooksPath .githooks
  ok "core.hooksPath = .githooks (large-file pre-commit hook active)"
fi

# ---------------------------------------------------------------------------
step "DuckDB sqlite extension"

# research/restart_fidelity/compare_arms.py runs INSTALL sqlite, which downloads
# from extensions.duckdb.org on first use. Pre-installing it here means the
# restart-fidelity tests work behind an egress policy that blocks that host —
# provided the host is reachable at least once, which is what the image build is for.
if ! have_python; then
  warn "no environment yet — cannot check sqlite_scanner"
else
  duckdb_status="$(
    py - <<'PY' 2>/dev/null || true
import duckdb
con = duckdb.connect()
installed = con.execute(
    "select installed from duckdb_extensions() where extension_name = 'sqlite_scanner'"
).fetchone()
print("present" if installed and installed[0] else "missing")
PY
  )"

  if [[ "$duckdb_status" == "present" ]]; then
    ok "sqlite_scanner already installed"
  elif (( CHECK_ONLY )); then
    warn "sqlite_scanner not installed — 9 restart-fidelity tests will fail"
  elif py -c "import duckdb; duckdb.connect().execute('INSTALL sqlite')" >/dev/null 2>&1; then
    ok "sqlite_scanner installed"
  else
    warn "could not install sqlite_scanner — extensions.duckdb.org unreachable or blocked;
        9 restart-fidelity tests will fail until it is allowed once"
  fi
fi

# ---------------------------------------------------------------------------
step "Data sources"

# Read the declared locations rather than assuming them; fall back to the
# committed defaults when there is no interpreter yet to parse the YAML.
drive=/mnt/d
bucket="r2:artemis-r2/data"
if have_python; then
  drive="$(py -c "
import yaml
print(yaml.safe_load(open('config/data_paths.yaml'))['drive'])
" 2>/dev/null || echo /mnt/d)"
  # Names the bucket explicitly: the token is bucket-scoped, so a bare `rclone lsd r2:` is 403.
  bucket="$(py -c "
import yaml
cfg = yaml.safe_load(open('config/data_paths.yaml'))['r2']
print(f\"{cfg['remote']}:{cfg['bucket']}/{cfg['prefix']}\")
" 2>/dev/null || echo "r2:artemis-r2/data")"
fi

if [[ -d "$drive" ]]; then
  ok "data drive mounted at $drive"
else
  warn "data drive $drive not mounted — falling back to R2"
fi

r2_ready=1
remote="${bucket%%:*}"
if ! command -v rclone >/dev/null 2>&1; then
  warn "rclone not found — no R2 fallback"
  r2_ready=0
# A remote can come from RCLONE_CONFIG_R2_* environment variables or from an
# rclone.conf section; listremotes is what sees both.
elif ! rclone listremotes 2>/dev/null | grep -qx "${remote}:"; then
  warn "no '${remote}' rclone remote — set RCLONE_CONFIG_R2_{TYPE,PROVIDER,ENDPOINT,ACCESS_KEY_ID,SECRET_ACCESS_KEY}
        or define [${remote}] in rclone.conf"
  r2_ready=0
fi

if (( r2_ready )); then
  if rclone lsf --max-depth 1 "$bucket" >/dev/null 2>&1; then
    ok "R2 reachable ($bucket)"
  else
    warn "R2 credentials set but $bucket did not list — check the endpoint and token scope"
  fi
fi

if [[ ! -d "$drive" ]] && (( ! r2_ready )); then
  warn "neither data source available — data-dependent tests will skip"
fi

# ---------------------------------------------------------------------------
step "Summary"

if (( warnings == 0 )); then
  ok "environment fully configured"
else
  printf '  %d warning(s) above. The toolchain is usable; the flagged capabilities are not.\n' "$warnings"
fi

printf '\n  Verify with:  uv run pytest tests/ -q\n\n'
