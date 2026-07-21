"""Canonical segmentation artifact and provenance regressions."""

from pathlib import Path
import sys

import geopandas as gpd
import pytest
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.segmentation.artifacts import (
    load_comparable_artifacts,
    manifest_path_for,
    write_segmentation_artifact,
)


def _units(strategy: str = "boundary_overlay") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "MU_ID": ["mu-1"],
            "Acres": [1.0],
            "SEGMENTATION_METHOD": [strategy],
            "PLT_CN": ["123"],
            "TM_VALUE": [7],
            "OWN_CODE": [3],
            "OWN_TYPE": ["Family Forest"],
            "SMZ_Pct": [12.5],
        },
        geometry=[box(0, 0, 10, 10)],
        crs="EPSG:5070",
    )


def _sources(tmp_path: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    shared = {}
    for name in ("treemap", "fiadb", "ownership", "species"):
        path = tmp_path / f"{name}.dat"
        if not path.exists():
            path.write_text(name)
        shared[name] = path
    parcels = tmp_path / "parcels.dat"
    if not parcels.exists():
        parcels.write_text("parcels")
    return shared, {"parcels": parcels}


def _write(
    tmp_path: Path,
    strategy: str,
    *,
    aoi_id: str = "12003",
    experiment_id: str = "exp-1",
    code_version: str = "abc123",
    seed: int = 7,
    shared_sources: dict[str, Path] | None = None,
) -> Path:
    shared, strategy_sources = _sources(tmp_path)
    if shared_sources is not None:
        shared = shared_sources
    artifact = tmp_path / strategy / "ManagementUnits.gpkg"
    write_segmentation_artifact(
        _units(strategy),
        artifact,
        strategy=strategy,
        aoi_id=aoi_id,
        experiment_id=experiment_id,
        seed=seed,
        strategy_parameters={"threshold": 200},
        code_version=code_version,
        shared_sources=shared,
        strategy_sources=strategy_sources,
    )
    return artifact


def test_boundary_artifact_contains_complete_attributed_contract_and_manifest(
    tmp_path: Path,
):
    artifact = _write(tmp_path, "boundary_overlay")

    units, manifest = load_comparable_artifacts(artifact, artifact, allow_same=True)[0]

    assert {
        "MU_ID",
        "Acres",
        "SEGMENTATION_METHOD",
        "PLT_CN",
        "TM_VALUE",
        "OWN_CODE",
        "OWN_TYPE",
        "SMZ_Pct",
        "geometry",
    } <= set(units.columns)
    assert manifest_path_for(artifact).exists()
    assert manifest == {
        "schema_version": 1,
        "strategy": "boundary_overlay",
        "aoi_id": "12003",
        "experiment_id": "exp-1",
        "seed": 7,
        "strategy_parameters": {"threshold": 200},
        "code_version": "abc123",
        "source_fingerprints": manifest["source_fingerprints"],
        "artifact_path": str(artifact.resolve()),
    }
    assert set(manifest["source_fingerprints"]) == {"shared", "strategy"}
    assert set(manifest["source_fingerprints"]["shared"]) == {
        "treemap",
        "fiadb",
        "ownership",
        "species",
    }
    assert set(manifest["source_fingerprints"]["shared"]["treemap"]) == {
        "resolved_path",
        "byte_size",
        "mtime_ns",
    }


def test_artifact_write_rejects_missing_post_attribution_column(tmp_path: Path):
    shared, strategy_sources = _sources(tmp_path)
    incomplete = _units().drop(columns="PLT_CN")

    with pytest.raises(ValueError, match="PLT_CN"):
        write_segmentation_artifact(
            incomplete,
            tmp_path / "ManagementUnits.gpkg",
            strategy="boundary_overlay",
            aoi_id="12003",
            experiment_id="exp-1",
            seed=7,
            strategy_parameters={},
            code_version="abc123",
            shared_sources=shared,
            strategy_sources=strategy_sources,
        )


def test_artifact_write_preflights_sources_before_writing_geopackage(tmp_path: Path):
    shared, strategy_sources = _sources(tmp_path)
    shared["treemap"] = tmp_path / "missing-treemap.tif"
    artifact = tmp_path / "ManagementUnits.gpkg"

    with pytest.raises(FileNotFoundError):
        write_segmentation_artifact(
            _units(),
            artifact,
            strategy="boundary_overlay",
            aoi_id="12003",
            experiment_id="exp-1",
            seed=7,
            strategy_parameters={},
            code_version="abc123",
            shared_sources=shared,
            strategy_sources=strategy_sources,
        )

    assert not artifact.exists()


def test_comparison_fails_closed_when_manifest_is_missing(tmp_path: Path):
    artifact = _write(tmp_path, "leto")
    manifest_path_for(artifact).unlink()

    with pytest.raises(FileNotFoundError, match="manifest"):
        load_comparable_artifacts(artifact, artifact, allow_same=True)


@pytest.mark.parametrize(
    ("field", "reference_value", "candidate_value"),
    [
        ("aoi_id", "12003", "12023"),
        ("experiment_id", "exp-1", "exp-2"),
        ("seed", 7, 8),
        ("code_version", "abc123", "def456"),
    ],
)
def test_comparison_rejects_mismatched_run_identity(
    tmp_path: Path,
    field: str,
    reference_value: str | int,
    candidate_value: str | int,
):
    kwargs = {field: reference_value}
    reference = _write(tmp_path / "reference", "leto", **kwargs)
    shared, _ = _sources(tmp_path / "reference")
    kwargs = {field: candidate_value, "shared_sources": shared}
    candidate = _write(tmp_path / "candidate", "boundary_overlay", **kwargs)

    with pytest.raises(ValueError, match=field):
        load_comparable_artifacts(reference, candidate)


def test_comparison_allows_strategy_specific_sources_to_differ(tmp_path: Path):
    reference = _write(tmp_path / "reference", "leto")
    shared, _ = _sources(tmp_path / "reference")
    candidate = _write(
        tmp_path / "candidate", "boundary_overlay", shared_sources=shared
    )

    (reference_units, reference_manifest), (candidate_units, candidate_manifest) = (
        load_comparable_artifacts(reference, candidate)
    )

    assert reference_units["SEGMENTATION_METHOD"].tolist() == ["leto"]
    assert candidate_units["SEGMENTATION_METHOD"].tolist() == ["boundary_overlay"]
    assert reference_manifest["strategy"] != candidate_manifest["strategy"]


def test_comparison_rejects_mismatched_shared_source_fingerprint(tmp_path: Path):
    reference = _write(tmp_path / "reference", "leto")
    shared, _ = _sources(tmp_path / "reference")
    changed_treemap = tmp_path / "changed-treemap.dat"
    changed_treemap.write_text("changed")
    shared["treemap"] = changed_treemap
    candidate = _write(
        tmp_path / "candidate", "boundary_overlay", shared_sources=shared
    )

    with pytest.raises(ValueError, match="shared-source fingerprints"):
        load_comparable_artifacts(reference, candidate)
