# Claude Code on the web: session setup

How an ARTEMIS session is provisioned in the ephemeral web container, and the
egress constraints that silently break things there.

Two separate layers, often confused:

| Layer | File | Runs |
|---|---|---|
| Environment setup script | `.claude/environment-setup.sh` (copy of the web UI field for the `claude-code-artemis` environment) | Once, when the container image is built |
| Session hook | `.claude/hooks/session-start.sh` | Every session start |

Only the hook is executed by Claude Code. The setup script is version-controlled
here for review; the authoritative copy is pasted into the environment's settings
in the web UI, so editing this file alone changes nothing.

## The environment setup script failed at its first line

The original script began:

```bash
set -euo pipefail
curl -fsSL https://rclone.org/install.sh | sudo bash
```

`rclone.org` is denied by the sandbox egress policy (403 on CONNECT), so curl
exited 22 and `set -euo pipefail` aborted the entire script immediately. Nothing
after line 1 ran, and rclone was never installed. Four distinct problems:

1. **`rclone.org` is blocked.** Use the Ubuntu archive instead — it is reachable
   and carries rclone 1.60.1. Refresh only `sources.list.d/ubuntu.sources`: the
   image also configures the deadsnakes and ondrej/php PPAs, which are *also*
   denied (403 from `ppa.launchpadcontent.net`) and would fail `apt-get update`
   under `set -e`. On noble the Ubuntu archive lives in `sources.list.d/` and
   `/etc/apt/sources.list` is empty, so the `sourcelist`/`sourceparts` overrides
   are inverted from the pre-noble idiom.
2. **`| sudo bash` drops the proxy.** `/etc/sudoers` sets `Defaults env_reset`
   with the proxy `env_keep` line commented out, so `HTTPS_PROXY` and
   `NODE_EXTRA_CA_CERTS` do not survive `sudo`. Even with `rclone.org`
   allowlisted, the installer's own downloads would fail.
3. **The uv install was redundant *and* blocked.** uv already ships in the base
   image at `/root/.local/bin/uv` (v0.8.17, alongside black, mypy, poetry,
   pytest, pyright). `astral.sh` is denied by the egress policy too, so that line
   would have failed as well had it been reached. Verify uv; do not fetch it.
4. **System GDAL is unnecessary.** rasterio bundles GDAL 3.12.1 and pyogrio
   3.11.4 in their wheels; the suite passes with no system GDAL. Ubuntu noble's
   `gdal-bin`/`libgdal-dev` is GDAL 3.8 — installing it only adds a second,
   older GDAL.

### Blocker: R2 is not reachable

**`r2.cloudflarestorage.com` is denied by the egress policy** (`connect_rejected`
in the proxy's failure log). The setup script's whole purpose — pulling input
data from R2 — cannot work in this environment no matter how rclone is
installed. This needs an admin allowlist change, not a script change. The setup
script warns about it at the end rather than failing, so sessions that do not
need R2 data still start.

### Gotcha: rclone 1.60 cannot use `AWS_CA_BUNDLE`

The proxy sets `AWS_CA_BUNDLE` globally. The archive's rclone hands the AWS SDK
its own transport, which the SDK rejects:

```
LoadCustomCABundleError: unable to load custom CA bundle, HTTPClient's transport
unsupported type / caused by: unsupported transport, *fshttp.Transport
```

Every `rclone ... :s3:` call fails *before* reaching the network, which masks the
R2 denial behind a misleading TLS-shaped error. The setup script installs a
wrapper at `/usr/local/bin/rclone` (which precedes `/usr/bin` in `PATH`) that
unsets `AWS_CA_BUNDLE` and passes the bundle via rclone's own `--ca-cert`. With
the wrapper, rclone reaches the network and reports the honest `Forbidden`.

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

`uv run pytest tests/` should report **zero failures**, with every skip
attributable to a data-availability guard: `/mnt/d` is not mounted, and
`treemap_2022_fl.tif` / the `clearcut_ag` interim CSVs are not present.

Judge a session by that invariant rather than by a fixed count — the totals move
with the suite (they were 82 passed / 21 skipped when this note was written). A
*failure*, particularly anywhere in `tests/test_restart_fidelity.py`, means the
`sqlite_scanner` staging above did not happen.
