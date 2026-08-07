# Claude Code cloud environment (`claude-code-artemis`)

Why the environment setup script does what it does. Ubuntu noble, running as root,
with all outbound HTTPS through a TLS-terminating policy proxy. Findings verified
2026-08-07 unless dated otherwise.

Three files, three scopes, one definition of "configured":

| File | Scope | Run by |
|---|---|---|
| [`scripts/claude-code-env-setup.sh`](../scripts/claude-code-env-setup.sh) | Machine: tools, shims, reachability | Pasted into the web UI's environment setup field |
| [`scripts/setup-env.sh`](../scripts/setup-env.sh) | Repo: dependencies, hooks, DuckDB extension, data report | The above, the Dockerfile, and by hand |
| [`Dockerfile`](../Dockerfile) | Everything, off this sandbox | `docker build` |

## Egress denials that shaped the script

The proxy answers a denied host with **403 on CONNECT**, which surfaces as
`curl: (56)`, an rclone timeout, or a plain 403 — never as DNS failure.

| Host | Consequence | Answer |
|---|---|---|
| `rclone.org` | `install.sh` cannot be fetched. Under `set -euo pipefail` this killed the script at its first line, so nothing after it ran. | Install rclone from the Ubuntu archive |
| `astral.sh` | The uv installer is unreachable | uv already ships in the base image at `/root/.local/bin/uv` |
| `ppa.launchpadcontent.net` | The image's deadsnakes and ondrej/php PPAs 403, failing a plain `apt-get update` under `set -e` | Refresh only `sources.list.d/ubuntu.sources` |
| `extensions.duckdb.org` | `INSTALL sqlite` fails; **9 restart-fidelity tests fail** | None available — needs an admin allowlist |
| `github.com` release downloads | Was why an earlier session used `boto3` instead of rclone | apt, as above |

`extensions.duckdb.org` is the only one still open, and the only one no workaround
reaches: DuckDB downloads `sqlite_scanner` at first use, and
`research/restart_fidelity/compare_arms.py` calls `INSTALL sqlite`. Once the host is
allowed even once, `scripts/setup-env.sh` caches the extension into
`~/.duckdb/extensions/` and later runs need no network. The Docker image does this
at build time.

## rclone cannot use `AWS_CA_BUNDLE` (the shim)

The environment sets `AWS_CA_BUNDLE=/root/.ccr/ca-bundle.crt` globally so tools trust
the proxy CA. rclone 1.60's S3 backend hands the AWS SDK its own transport, and the
SDK refuses:

```
Failed to create file system for "r2:artemis-r2/data": LoadCustomCABundleError:
unable to load custom CA bundle, HTTPClient's transport unsupported type
caused by: unsupported transport, *fshttp.Transport
```

Every S3 call fails before reaching the network. The fix is a wrapper at
`/usr/local/bin/rclone` (which precedes `/usr/bin`) that unsets `AWS_CA_BUNDLE` and
passes the same bundle through rclone's own `RCLONE_CA_CERT`. Confirmed both ways:
the real binary fails with the error above, and succeeds with the bundle supplied by
flag. The Dockerfile installs the same shim, inert when no bundle is mounted.

Also relevant if the script is ever changed to use `sudo bash`: `/etc/sudoers` sets
`Defaults env_reset` with the proxy `env_keep` line commented out, so `HTTPS_PROXY`
and `NODE_EXTRA_CA_CERTS` do not survive `sudo`.

## What the environment does not need

- **System GDAL.** rasterio bundles GDAL 3.12.1 and pyogrio 3.11.4 in their wheels;
  the suite passes without it. Noble's `gdal-bin` (3.8) would add a second, older GDAL.
- **A Python build.** `.python-version` pins 3.14 and uv provisions it — 3.14.0rc2
  with uv 0.8.17, which is the newest uv the base image carries. CI's newer uv gets
  3.14 final; both pass.

## Data

`/mnt/d` is a workstation mount and never exists here. The same data is in the
Cloudflare R2 bucket `artemis-r2`; [`data/index.md`](../data/index.md) catalogs it and
`pipeline/data_access.py` resolves declared paths against whichever source answers.
The token is bucket-scoped, so a bare `rclone lsd r2:` returns 403 — always name the
bucket. Credentials come from `RCLONE_CONFIG_R2_*` set on the environment; the
account-scoped endpoint is what the egress policy must allow, not the generic
`r2.cloudflarestorage.com`.

## Open inconsistency

The env-setup script points per-session provisioning at `.claude/hooks/session-start.sh`,
but `.gitignore` ignores `.claude/`, so that hook cannot be version-controlled as
things stand and is absent from fresh containers. Either unignore that one path or
fold what it does into `scripts/setup-env.sh`, which every environment already runs.
