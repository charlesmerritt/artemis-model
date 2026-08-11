#!/usr/bin/env bash
# Setup script for the "claude-code-artemis" Claude Code cloud environment.
#
# This file is NOT executed by Claude Code. It is the version-controlled copy of
# the setup script configured in the web UI for the environment; paste its
# contents there. Per-session provisioning lives in .claude/hooks/session-start.sh.
#
# Machine-level only: tools, shims, and reachability. Everything repo-level —
# dependencies, git hooks, the DuckDB extension — lives in scripts/setup-env.sh,
# which this script hands off to at the end and which the Dockerfile runs too, so
# there is one definition of "configured" across all three environments.
#
# See notes/claude-code-web-environment.md for the diagnosis behind each step.
set -euo pipefail

log() { printf 'env-setup: %s\n' "$1" >&2; }

# ---------------------------------------------------------------------------
# rclone
#
# NOT via https://rclone.org/install.sh: the sandbox egress policy denies
# rclone.org with 403 on CONNECT. Under `set -euo pipefail` that curl failure
# aborted the whole script at its first line, which is why nothing after it ran.
#
# Also not piped into `sudo bash`: /etc/sudoers sets `Defaults env_reset` with
# the proxy env_keep line commented out, so HTTPS_PROXY and NODE_EXTRA_CA_CERTS
# do not survive sudo and the installer's own downloads would fail regardless.
#
# The Ubuntu archive is reachable and carries rclone, so use apt.
# ---------------------------------------------------------------------------
if command -v rclone >/dev/null 2>&1; then
  log "rclone already present ($(rclone version 2>/dev/null | head -1))"
else
  log "installing rclone from the Ubuntu archive"
  # Refresh only ubuntu.sources. The image also configures the deadsnakes and
  # ondrej/php PPAs, which the egress policy denies (403 from
  # ppa.launchpadcontent.net); their errors would fail `apt-get update` under
  # `set -e`. Note that on noble the Ubuntu archive lives in
  # sources.list.d/ubuntu.sources and /etc/apt/sources.list is empty, so the
  # sourcelist/sourceparts values below are deliberately inverted from the
  # pre-noble idiom.
  sudo apt-get update -qq \
    -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/ubuntu.sources \
    -o Dir::Etc::sourceparts=/dev/null
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends rclone
  log "installed $(rclone version 2>/dev/null | head -1)"
fi

# ---------------------------------------------------------------------------
# rclone + proxy CA shim
#
# The archive's rclone (1.60.1) cannot consume the globally-set AWS_CA_BUNDLE:
# its S3 backend hands the AWS SDK a custom transport and the SDK rejects it
# with "LoadCustomCABundleError: unsupported transport, *fshttp.Transport".
# Every `rclone ... :s3:` call fails before reaching the network.
#
# Shadow it with a wrapper that drops AWS_CA_BUNDLE and passes the bundle
# through rclone's own --ca-cert instead. /usr/local/bin precedes /usr/bin.
# ---------------------------------------------------------------------------
real_rclone="$(command -v rclone)"
if [ "$real_rclone" != "/usr/local/bin/rclone" ]; then
  log "installing rclone CA shim at /usr/local/bin/rclone"
  sudo tee /usr/local/bin/rclone >/dev/null <<SHIM
#!/usr/bin/env bash
# Wrapper: rclone 1.60's S3 backend cannot use AWS_CA_BUNDLE (unsupported
# transport). Drop it and supply the proxy CA via rclone's own flag.
set -euo pipefail
unset AWS_CA_BUNDLE
if [ -r /root/.ccr/ca-bundle.crt ]; then
  export RCLONE_CA_CERT="\${RCLONE_CA_CERT:-/root/.ccr/ca-bundle.crt}"
fi
exec "$real_rclone" "\$@"
SHIM
  sudo chmod +x /usr/local/bin/rclone
fi

# ---------------------------------------------------------------------------
# uv
#
# Nothing to install: uv ships in the base image at /root/.local/bin/uv,
# alongside black, mypy, poetry, pytest and pyright. The astral.sh installer was
# both redundant and unreachable (astral.sh is denied by the egress policy too),
# so verify instead of fetching.
# ---------------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  log "uv present: $(uv --version)"
else
  log "ERROR: uv missing from the base image and astral.sh is blocked by the egress policy"
  exit 1
fi

# ---------------------------------------------------------------------------
# System GDAL is deliberately NOT installed.
#
# rasterio bundles GDAL 3.12.1 and pyogrio bundles 3.11.4 in their wheels; the
# full test suite passes with no system GDAL. Installing Ubuntu noble's
# gdal-bin/libgdal-dev (GDAL 3.8) would only add a second, older GDAL.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Preflight: report reachability rather than letting it surprise a later run.
#
# Non-fatal on purpose — tooling installed fine, and failing here would block
# sessions that do not need remote data.
# ---------------------------------------------------------------------------

# Guard every probe with `if`: curl emits its %{http_code} ("000" on a failed
# CONNECT) *and* exits non-zero, so a trailing `|| echo 000` would concatenate
# into "000000" and read as reachable, while a bare assignment would trip set -e.
http_code() {
  local code
  if code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$1" 2>/dev/null)"; then
    printf '%s' "$code"
  else
    printf '000'
  fi
}

# The account-scoped endpoint, not the generic r2.cloudflarestorage.com: egress
# policies allow hosts by name, and only the configured one is actually used.
r2_endpoint="${RCLONE_CONFIG_R2_ENDPOINT:-https://r2.cloudflarestorage.com}"
r2_host="${ARTEMIS_R2_ENDPOINT_HOST:-${r2_endpoint#https://}}"
r2_host="${r2_host%%/*}"
if [ "$(http_code "https://${r2_host}/")" = "000" ]; then
  log "WARNING: cannot reach ${r2_host} — the egress policy denies it (403 on CONNECT)."
  log "WARNING: rclone is installed but R2 pulls will fail until an admin allowlists that host."
else
  # A host that answers is not the same as a working remote. This exercises the
  # whole path — CA shim, credentials, bucket scope — the way the pipeline will.
  # Name the bucket: the token is bucket-scoped, so a bare `rclone lsd r2:` is 403.
  if rclone lsf --max-depth 1 r2:artemis-r2/data >/dev/null 2>&1; then
    log "R2 reachable and the r2: remote lists artemis-r2/data"
  else
    log "WARNING: ${r2_host} answers but 'rclone lsf r2:artemis-r2/data' failed."
    log "WARNING: check RCLONE_CONFIG_R2_{TYPE,PROVIDER,ENDPOINT,ACCESS_KEY_ID,SECRET_ACCESS_KEY}."
  fi
fi

# DuckDB downloads sqlite_scanner on first `INSTALL sqlite`, which
# research/restart_fidelity/compare_arms.py does. While this host is denied, 9
# restart-fidelity tests fail with HTTP 403 — the single largest gap in the
# environment, and the one thing here that needs an admin, not a workaround.
if [ "$(http_code "https://extensions.duckdb.org/")" = "000" ]; then
  log "WARNING: extensions.duckdb.org is denied by the egress policy."
  log "WARNING: 9 restart-fidelity tests will fail until an admin allowlists it;"
  log "WARNING: scripts/setup-env.sh caches the extension once it is reachable."
else
  log "extensions.duckdb.org reachable — sqlite_scanner can be cached"
fi

# ---------------------------------------------------------------------------
# Repo-level bootstrap
#
# Dependencies, git hooks, and the DuckDB extension cache. Doing it here rather
# than per session means the environment snapshot already carries a synced .venv.
# Skipped without complaint when the checkout is not where we expect: this script
# also runs before a repo exists in some environments.
# ---------------------------------------------------------------------------
repo=""
for candidate in "${ARTEMIS_REPO_DIR:-}" "$PWD" /home/user/artemis-model; do
  if [ -n "$candidate" ] && [ -x "$candidate/scripts/setup-env.sh" ]; then
    repo="$candidate"
    break
  fi
done

if [ -n "$repo" ]; then
  log "running repo bootstrap in $repo"
  "$repo/scripts/setup-env.sh" || log "WARNING: repo bootstrap reported a problem (see above)"
else
  log "no checkout found — skipping repo bootstrap (run scripts/setup-env.sh in-session)"
fi

log "done"
