"""Canonical S1 segmentation artifacts and fail-closed run provenance."""

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import geopandas as gpd

SCHEMA_VERSION = 1
CANONICAL_UNIT_COLUMNS = (
    "MU_ID",
    "Acres",
    "SEGMENTATION_METHOD",
    "PLT_CN",
    "TM_VALUE",
    "OWN_CODE",
    "OWN_TYPE",
    "SMZ_Pct",
    "geometry",
)
RUN_IDENTITY_FIELDS = ("aoi_id", "experiment_id", "seed", "code_version")
FINGERPRINT_FIELDS = {
    "resolved_path",
    "byte_size",
    "mtime_ns",
    "metadata_sha256",
}


def manifest_path_for(artifact_path: Path | str) -> Path:
    """Return the sidecar manifest path for a canonical artifact."""
    artifact = Path(artifact_path)
    return artifact.with_suffix(".manifest.json")


def source_fingerprint(path: Path | str) -> dict[str, str | int]:
    """Fingerprint file metadata or directory metadata plus contents."""
    resolved = Path(path).resolve(strict=True)
    entries = [resolved]
    if resolved.is_dir():
        entries.extend(sorted(resolved.rglob("*")))
    metadata = hashlib.sha256()
    byte_size = 0
    mtime_ns = 0
    for entry in entries:
        stat = entry.lstat()
        relative_path = "." if entry == resolved else entry.relative_to(resolved)
        metadata.update(os.fsencode(str(relative_path)))
        metadata.update(b"\0")
        metadata.update(str(stat.st_mode).encode())
        metadata.update(b"\0")
        metadata.update(str(stat.st_size).encode())
        metadata.update(b"\0")
        metadata.update(str(stat.st_mtime_ns).encode())
        metadata.update(b"\0")
        metadata.update(str(stat.st_ctime_ns).encode())
        metadata.update(b"\0")
        if entry.is_file():
            byte_size += stat.st_size
            if resolved.is_dir():
                with entry.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        metadata.update(chunk)
        mtime_ns = max(mtime_ns, stat.st_mtime_ns)
    return {
        "resolved_path": str(resolved),
        "byte_size": byte_size,
        "mtime_ns": mtime_ns,
        "metadata_sha256": metadata.hexdigest(),
    }


def _fingerprint_sources(
    sources: Mapping[str, Path | str],
) -> dict[str, dict[str, str | int]]:
    if not sources:
        raise ValueError("At least one source fingerprint is required")
    return {name: source_fingerprint(path) for name, path in sorted(sources.items())}


def resolve_code_version(project_root: Path | str) -> str:
    """Return the commit plus a content-addressed dirty-worktree marker."""
    root = Path(project_root).resolve()
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    if not status:
        return commit

    digest = hashlib.sha256(status)
    tracked_diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--binary", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout
    digest.update(tracked_diff)
    untracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    for relative_path_bytes in sorted(path for path in untracked if path):
        relative_path = os.fsdecode(relative_path_bytes)
        untracked_path = root / relative_path
        digest.update(relative_path_bytes)
        digest.update(b"\0")
        if untracked_path.is_symlink():
            digest.update(os.fsencode(os.readlink(untracked_path)))
        else:
            with untracked_path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(b"\0")
    return f"{commit}+dirty.{digest.hexdigest()[:16]}"


def _validate_canonical_units(
    units: gpd.GeoDataFrame,
    *,
    strategy: str | None = None,
) -> None:
    missing = set(CANONICAL_UNIT_COLUMNS).difference(units.columns)
    if missing:
        raise ValueError(
            f"Canonical segmentation artifact missing columns: {sorted(missing)}"
        )
    if units.empty:
        raise ValueError(
            "Canonical segmentation artifact must contain at least one unit"
        )
    if units.crs is None:
        raise ValueError("Canonical segmentation artifact must define a CRS")
    if units["MU_ID"].isna().any() or units["MU_ID"].duplicated().any():
        raise ValueError(
            "Canonical segmentation MU_ID values must be non-null and unique"
        )
    methods = set(units["SEGMENTATION_METHOD"].dropna().astype(str))
    if strategy is not None and methods != {strategy}:
        raise ValueError(
            "Canonical segmentation SEGMENTATION_METHOD must match manifest strategy"
        )


def write_segmentation_artifact(
    units: gpd.GeoDataFrame,
    artifact_path: Path | str,
    *,
    strategy: str,
    aoi_id: str,
    experiment_id: str,
    seed: int,
    strategy_parameters: Mapping[str, Any],
    code_version: str,
    shared_sources: Mapping[str, Path | str],
    strategy_sources: Mapping[str, Path | str],
) -> Path:
    """Write a complete attributed GeoPackage and its run manifest."""
    _validate_canonical_units(units, strategy=strategy)
    if not strategy or not aoi_id or not experiment_id or not code_version:
        raise ValueError("Strategy, AOI, experiment ID, and code version are required")
    artifact = Path(artifact_path).resolve()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "strategy": strategy,
        "aoi_id": str(aoi_id),
        "experiment_id": str(experiment_id),
        "seed": int(seed),
        "strategy_parameters": dict(strategy_parameters),
        "code_version": str(code_version),
        "source_fingerprints": {
            "shared": _fingerprint_sources(shared_sources),
            "strategy": _fingerprint_sources(strategy_sources),
        },
        "artifact_path": str(artifact),
    }
    sidecar = manifest_path_for(artifact)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    units.to_file(artifact, layer="management_units", driver="GPKG")
    sidecar.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return sidecar


def _load_manifest(artifact_path: Path) -> dict[str, Any]:
    sidecar = manifest_path_for(artifact_path)
    if not sidecar.is_file():
        raise FileNotFoundError(f"Segmentation manifest is missing: {sidecar}")
    manifest = json.loads(sidecar.read_text())
    required = {
        "schema_version",
        "strategy",
        *RUN_IDENTITY_FIELDS,
        "strategy_parameters",
        "source_fingerprints",
        "artifact_path",
    }
    missing = required.difference(manifest)
    if missing:
        raise ValueError(f"Segmentation manifest missing fields: {sorted(missing)}")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported segmentation manifest schema_version: "
            f"{manifest['schema_version']!r}"
        )
    if manifest["artifact_path"] != str(artifact_path.resolve()):
        raise ValueError("Segmentation manifest artifact_path does not match artifact")
    fingerprints = manifest["source_fingerprints"]
    if not isinstance(fingerprints, dict) or set(fingerprints) != {
        "shared",
        "strategy",
    }:
        raise ValueError("Segmentation manifest source_fingerprints are invalid")
    for group in fingerprints.values():
        if not isinstance(group, dict) or not group:
            raise ValueError("Segmentation manifest source fingerprint group is empty")
        for fingerprint in group.values():
            if (
                not isinstance(fingerprint, dict)
                or set(fingerprint) != FINGERPRINT_FIELDS
            ):
                raise ValueError("Segmentation manifest source fingerprint is invalid")
    return manifest


def load_segmentation_artifact(
    artifact_path: Path | str,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Load one canonical artifact only when its manifest and contract are valid."""
    artifact = Path(artifact_path).resolve()
    if not artifact.is_file():
        raise FileNotFoundError(f"Segmentation artifact is missing: {artifact}")
    manifest = _load_manifest(artifact)
    units = gpd.read_file(artifact, layer="management_units")
    _validate_canonical_units(units, strategy=manifest["strategy"])
    return units, manifest


def load_comparable_artifacts(
    reference_path: Path | str,
    candidate_path: Path | str,
    *,
    allow_same: bool = False,
) -> tuple[
    tuple[gpd.GeoDataFrame, dict[str, Any]],
    tuple[gpd.GeoDataFrame, dict[str, Any]],
]:
    """Load a pair only when run identity and shared provenance are comparable."""
    reference = load_segmentation_artifact(reference_path)
    candidate = load_segmentation_artifact(candidate_path)
    reference_manifest = reference[1]
    candidate_manifest = candidate[1]

    if (
        not allow_same
        and reference_manifest["strategy"] == candidate_manifest["strategy"]
    ):
        raise ValueError("Comparison strategies must differ")
    for field in RUN_IDENTITY_FIELDS:
        if reference_manifest[field] != candidate_manifest[field]:
            raise ValueError(f"Comparison manifest {field} values do not match")
    reference_shared = reference_manifest["source_fingerprints"]["shared"]
    candidate_shared = candidate_manifest["source_fingerprints"]["shared"]
    if reference_shared != candidate_shared:
        raise ValueError("Comparison shared-source fingerprints do not match")
    return reference, candidate
