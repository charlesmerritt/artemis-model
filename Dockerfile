# syntax=docker/dockerfile:1
#
# The ARTEMIS environment, portable off Claude Code's cloud sandbox: same distro,
# same rclone, pinned interpreter, locked dependencies, and — network permitting —
# the DuckDB sqlite extension already cached.
#
#   docker build -t artemis .
#   docker run --rm -it artemis scripts/setup-env.sh --check
#   docker run --rm -it artemis uv run pytest tests/ -q
#
# With data. R2 credentials stay outside the image — never bake them in:
#
#   docker run --rm -it \
#     -e RCLONE_CONFIG_R2_ENDPOINT -e RCLONE_CONFIG_R2_ACCESS_KEY_ID \
#     -e RCLONE_CONFIG_R2_SECRET_ACCESS_KEY \
#     -v "$PWD/data:/app/data" artemis uv run pytest tests/ -q
#
# On the workstation, mount the drive instead and skip the credentials entirely:
#
#   docker run --rm -it -v /mnt/d:/mnt/d:ro artemis uv run pytest tests/ -q
#
# Ubuntu noble to match the cloud environment: its archive rclone (1.60.1) is the
# version the R2 access path has been exercised against, including the CA shim below.

FROM ubuntu:24.04

# rclone from the distro archive rather than rclone.org or a GitHub release, both
# of which egress policies commonly deny — the same reason scripts/claude-code-env-setup.sh
# uses apt.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        rclone \
    && rm -rf /var/lib/apt/lists/*

# Same CA shim the cloud environment installs: rclone 1.60's S3 backend rejects
# AWS_CA_BUNDLE with "unsupported transport, *fshttp.Transport", so every S3 call
# fails before reaching the network. Inert unless the image is run behind a
# TLS-terminating proxy whose bundle is mounted at that path.
RUN printf '%s\n' \
        '#!/usr/bin/env bash' \
        'set -euo pipefail' \
        'unset AWS_CA_BUNDLE' \
        'if [ -r /root/.ccr/ca-bundle.crt ]; then' \
        '  export RCLONE_CA_CERT="${RCLONE_CA_CERT:-/root/.ccr/ca-bundle.crt}"' \
        'fi' \
        'exec /usr/bin/rclone "$@"' \
    > /usr/local/bin/rclone \
    && chmod +x /usr/local/bin/rclone

# Pinned to the uv this repository has been verified against; bump deliberately.
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# The non-secret half of the rclone remote, so a run only has to pass the endpoint
# and the two keys.
ENV RCLONE_CONFIG_R2_TYPE=s3 \
    RCLONE_CONFIG_R2_PROVIDER=Cloudflare

# Dependency layer: manifests only, so editing source neither re-resolves nor
# re-downloads. .python-version pins 3.14, which uv fetches here.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen

# Source, then the same bootstrap the cloud environment and a workstation run —
# one definition of "configured". It reports rather than fails on what an image
# cannot have (no git repo, no data credentials), and caches the DuckDB sqlite
# extension so the restart-fidelity tests need no network at run time. If
# extensions.duckdb.org is blocked during the build the image still builds; the
# extension is simply absent and `setup-env.sh --check` says so.
COPY . .
RUN scripts/setup-env.sh

CMD ["bash"]
