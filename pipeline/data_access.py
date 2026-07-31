"""
Resolve declared data paths against the local drive or the R2 mirror.

``config/data_paths.yaml`` names files on the ``/mnt/d`` workstation drive. Off
that workstation the same data lives in the Cloudflare R2 bucket ``artemis-r2``,
so a declared path that is missing locally is not necessarily missing — it may
just be remote. This module performs that second lookup, letting callers ask
"is this data reachable?" and "give me a local copy" without knowing which side
answered.

Two mappings, both declared in the ``r2:`` block of ``data_paths.yaml``:

    /mnt/d/<rel>          ->  r2:artemis-r2/data/<rel>
    <repo>/data/<rel>     ->  r2:artemis-r2/data/Artemis_data/<rel>

The second exists because the bucket also mirrors the repository's gitignored
``data/`` tree (pipeline interim and processed outputs) under ``Artemis_data/``.
Directories whose bucket name differs from their drive name are listed under
``r2.renames`` rather than hardcoded here.

Credentials come from the preconfigured ``RCLONE_CONFIG_R2_*`` environment
variables; nothing here reads, stores, or logs a secret. Where rclone or those
variables are absent — a bare CI runner, a fresh clone — every lookup reports
"unavailable" and callers fall back to what they did before (tests skip).

Environment:
    ARTEMIS_R2_FALLBACK=0       disable remote lookups entirely
    ARTEMIS_R2_MAX_FETCH_MB=N   per-file download cap (default 512)

Usage:
    from pipeline import data_access

    data_access.exists("/mnt/d/RDS-2025-0045/Data/US_forest_ownership.tif")
    local = data_access.ensure_local("data/interim/clearcut_ag/feature_table.csv")
"""

import json
import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# rclone builds its remote from these; all three must be set for the fallback to work.
REQUIRED_ENV_VARS = (
    "RCLONE_CONFIG_R2_ACCESS_KEY_ID",
    "RCLONE_CONFIG_R2_SECRET_ACCESS_KEY",
    "RCLONE_CONFIG_R2_ENDPOINT",
)

DEFAULT_MAX_FETCH_MB = 512
CACHE_DIR = "r2_cache"

_STAT_TIMEOUT_S = 60
_FETCH_TIMEOUT_S = 900


class RemoteFetchTooLarge(RuntimeError):
    """A requested object exceeds the per-file download cap.

    Raised rather than returned so that pointing this module at, say, the 30 GB
    CONUS TreeMap raster fails loudly instead of looking like absent data.
    """


@lru_cache(maxsize=1)
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def data_paths() -> dict:
    with open(repo_root() / "config" / "data_paths.yaml") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def r2_available() -> bool:
    """Whether remote lookups can be attempted at all.

    Checks configuration only — no network call — so this stays cheap enough to
    guard every lookup. A configured-but-unreachable bucket surfaces later, as a
    failed rclone call that callers treat as "not found".
    """
    if os.environ.get("ARTEMIS_R2_FALLBACK", "1") == "0":
        return False
    if shutil.which("rclone") is None:
        logger.debug("R2 fallback off: rclone not on PATH")
        return False
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        logger.debug("R2 fallback off: unset %s", ", ".join(missing))
        return False
    return "r2" in data_paths()


def _as_path(path) -> Path:
    """Absolutize a declared path; repo-relative paths resolve against the root."""
    p = Path(path)
    return p if p.is_absolute() else repo_root() / p


def _relative_to(path: Path, base: Path) -> Path | None:
    try:
        return path.relative_to(base)
    except ValueError:
        return None


def remote_url(path) -> str | None:
    """The rclone URL for a declared path, or None when it maps nowhere.

    Only paths under the configured drive or under the repository's ``data/``
    tree have a counterpart in the bucket; anything else returns None.
    """
    cfg = data_paths().get("r2")
    if cfg is None:
        return None
    base = f"{cfg['remote']}:{cfg['bucket']}/{cfg['prefix']}"
    absolute = _as_path(path)

    rel = _relative_to(absolute, Path(data_paths()["drive"]))
    if rel is not None:
        head, *tail = rel.parts
        # A handful of directories were uploaded under a different name.
        head = cfg.get("renames", {}).get(head, head)
        return "/".join([base, head, *tail])

    rel = _relative_to(absolute, repo_root() / "data")
    if rel is not None:
        return "/".join([base, cfg["repo_data_prefix"], *rel.parts])

    return None


def _run(argv: list[str], timeout_s: int) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("rclone call failed (%s): %s", exc.__class__.__name__, argv[:2])
        return None


def stat(path) -> dict | None:
    """rclone metadata for the file behind a declared path, or None if absent.

    ``rclone lsjson --stat`` answers for a missing key with a synthetic directory
    entry (``IsDir`` true, empty ``Name``) because S3 has no real directories, so
    a genuine hit is identified by ``IsDir`` being false.
    """
    url = remote_url(path)
    if url is None or not r2_available():
        return None
    proc = _run(["rclone", "lsjson", "--stat", url], _STAT_TIMEOUT_S)
    if proc is None or proc.returncode != 0:
        return None
    try:
        meta = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return None if meta.get("IsDir", True) else meta


def _remote_dir_exists(path) -> bool:
    """Whether the remote counterpart is a non-empty prefix (a directory)."""
    url = remote_url(path)
    if url is None or not r2_available():
        return False
    proc = _run(["rclone", "lsf", "--max-depth", "1", url], _STAT_TIMEOUT_S)
    return proc is not None and proc.returncode == 0 and bool(proc.stdout.strip())


def exists(path) -> bool:
    """Whether the data is reachable at all — on the drive or in the bucket.

    Cheap: a hit in the bucket is confirmed from metadata, nothing is downloaded.
    Handles both files and directory-shaped datasets (``.gdb``, parcel folders).
    """
    if _as_path(path).exists():
        return True
    return stat(path) is not None or _remote_dir_exists(path)


def cache_path(path) -> Path:
    """Where a fetched copy of a drive path is staged, under gitignored data/."""
    absolute = _as_path(path)
    rel = _relative_to(absolute, Path(data_paths()["drive"])) or Path(absolute.name)
    return repo_root() / "data" / CACHE_DIR / rel


def ensure_local(path, dest: Path | None = None, max_fetch_mb: int | None = None) -> Path | None:
    """Return a local path holding this data, downloading from R2 if needed.

    Returns the declared path when it already exists, the destination when a
    download succeeds, and None when the data is reachable from neither source.
    Repository ``data/`` paths default to fetching in place (that is where the
    code that reads them looks); drive paths land under ``data/r2_cache/``.

    Raises RemoteFetchTooLarge when the object exceeds the cap — an oversize
    file is a caller mistake, not missing data, and should not look like one.
    """
    absolute = _as_path(path)
    if absolute.exists():
        return absolute

    if dest is None:
        in_repo_data = _relative_to(absolute, repo_root() / "data") is not None
        dest = absolute if in_repo_data else cache_path(absolute)
    dest = Path(dest)
    if dest.exists():
        return dest

    meta = stat(path)
    if meta is None:
        return None

    cap_mb = max_fetch_mb or int(os.environ.get("ARTEMIS_R2_MAX_FETCH_MB", DEFAULT_MAX_FETCH_MB))
    size_mb = meta["Size"] / 1_000_000
    if size_mb > cap_mb:
        raise RemoteFetchTooLarge(
            f"{remote_url(path)} is {size_mb:.0f} MB, over the {cap_mb} MB cap; "
            "raise ARTEMIS_R2_MAX_FETCH_MB or fetch it by hand with rclone"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("fetching %s (%.1f MB) -> %s", remote_url(path), size_mb, dest)
    proc = _run(["rclone", "copyto", remote_url(path), str(dest)], _FETCH_TIMEOUT_S)
    if proc is None or proc.returncode != 0:
        logger.warning("fetch failed: %s", (proc.stderr.strip() if proc else "no output"))
        return None
    return dest


def unavailable_reason(path) -> str:
    """A skip/error message explaining which lookups were tried and failed."""
    if not r2_available():
        return (
            f"{path} not present and the R2 fallback is unavailable "
            "(needs rclone plus RCLONE_CONFIG_R2_* credentials)"
        )
    url = remote_url(path)
    if url is None:
        return f"{path} not present, and it is under neither the data drive nor the repo's data/"
    return f"{path} not on the data drive and not in R2 ({url})"
