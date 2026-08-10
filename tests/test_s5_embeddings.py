"""Tests for the pure statistics in pipeline/s5_imagery/embeddings.py."""

import sys
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s5_imagery import embeddings as emb

AOI_BOX = box(-82.60, 30.10, -82.59, 30.11)
EXTENT_BOX = box(-82.62, 30.08, -82.57, 30.13)


def _records(pairs):
    """pairs: list of (cluster, inside) tuples."""
    return [{"cluster": cluster, "inside": inside} for cluster, inside in pairs]


# ---- palette ----


def test_cluster_palette_length_and_cycling():
    assert len(emb.cluster_palette(3)) == 3
    long_palette = emb.cluster_palette(len(emb.CLUSTER_PALETTE) + 2)
    assert long_palette[0] == long_palette[len(emb.CLUSTER_PALETTE)]


def test_cluster_palette_rejects_zero():
    with pytest.raises(ValueError):
        emb.cluster_palette(0)


# ---- cluster_distribution ----


def test_cluster_distribution_counts_and_shares():
    records = _records([(0, True), (0, True), (1, True), (0, False), (1, False), (1, False)])
    distribution = emb.cluster_distribution(records, k=2)

    cluster0, cluster1 = distribution
    assert cluster0["inside_count"] == 2
    assert cluster0["outside_count"] == 1
    # Shares normalize within each side: 2 of 3 inside, 1 of 3 outside.
    assert cluster0["inside_share"] == pytest.approx(2 / 3)
    assert cluster0["outside_share"] == pytest.approx(1 / 3)
    assert cluster1["inside_share"] == pytest.approx(1 / 3)


def test_cluster_distribution_inside_fraction_is_the_other_direction():
    # Cluster 0 holds 3 inside and 1 outside: 75% of the cluster is inside.
    records = _records([(0, True), (0, True), (0, True), (0, False), (1, False)])
    distribution = emb.cluster_distribution(records, k=2)
    assert distribution[0]["inside_fraction"] == pytest.approx(0.75)


def test_cluster_distribution_empty_cluster_has_null_fraction():
    records = _records([(0, True), (0, False)])
    distribution = emb.cluster_distribution(records, k=3)
    assert distribution[2]["total_count"] == 0
    assert distribution[2]["inside_fraction"] is None


def test_cluster_distribution_share_delta_signs_the_side():
    records = _records([(0, True), (0, True), (1, False), (1, False)])
    distribution = emb.cluster_distribution(records, k=2)
    assert distribution[0]["share_delta"] == pytest.approx(1.0)
    assert distribution[1]["share_delta"] == pytest.approx(-1.0)


def test_cluster_distribution_rejects_out_of_range_cluster():
    with pytest.raises(ValueError, match="outside range"):
        emb.cluster_distribution(_records([(5, True)]), k=2)


def test_cluster_distribution_handles_one_sided_sample():
    distribution = emb.cluster_distribution(_records([(0, True), (1, True)]), k=2)
    assert distribution[0]["inside_share"] == pytest.approx(0.5)
    assert distribution[0]["outside_share"] == 0.0


# ---- jensen_shannon_divergence ----


def test_jsd_identical_distributions_is_zero():
    assert emb.jensen_shannon_divergence([3.0, 5.0, 2.0], [3.0, 5.0, 2.0]) == pytest.approx(0.0)


def test_jsd_is_scale_invariant():
    # Only the shape matters; the two sides rarely have equal sample counts.
    assert emb.jensen_shannon_divergence([1.0, 1.0], [50.0, 50.0]) == pytest.approx(0.0)


def test_jsd_disjoint_distributions_is_one():
    assert emb.jensen_shannon_divergence([10.0, 0.0], [0.0, 10.0]) == pytest.approx(1.0)


def test_jsd_is_symmetric():
    p, q = [4.0, 1.0, 1.0], [1.0, 3.0, 2.0]
    assert emb.jensen_shannon_divergence(p, q) == pytest.approx(
        emb.jensen_shannon_divergence(q, p)
    )


def test_jsd_partial_overlap_is_between():
    value = emb.jensen_shannon_divergence([8.0, 2.0], [2.0, 8.0])
    assert 0.0 < value < 1.0


def test_jsd_empty_side_returns_zero():
    assert emb.jensen_shannon_divergence([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_jsd_rejects_length_mismatch():
    with pytest.raises(ValueError, match="equal length"):
        emb.jensen_shannon_divergence([1.0], [1.0, 2.0])


def test_jsd_rejects_empty():
    with pytest.raises(ValueError, match="non-empty"):
        emb.jensen_shannon_divergence([], [])


def test_interpret_divergence_moves_through_the_scale():
    assert "same population" in emb.interpret_divergence(0.0)
    assert "Weak" in emb.interpret_divergence(0.10)
    assert "Moderate" in emb.interpret_divergence(0.25)
    assert "Strong" in emb.interpret_divergence(0.45)
    assert "Near-complete" in emb.interpret_divergence(0.95)


# ---- pca_2d ----


def test_pca_2d_recovers_dominant_axis():
    rng = np.random.default_rng(0)
    # Variance almost entirely along one direction, with a little noise elsewhere.
    t = rng.normal(0, 10, 200)
    matrix = np.column_stack([t, 0.01 * rng.normal(0, 1, 200), 0.01 * rng.normal(0, 1, 200)])

    coords, ratios = pca = emb.pca_2d(matrix)
    assert coords.shape == (200, 2)
    assert ratios[0] > 0.99
    assert ratios[0] >= ratios[1]
    assert sum(pca[1]) <= 1.0 + 1e-9


def test_pca_2d_is_centered():
    rng = np.random.default_rng(1)
    matrix = rng.normal(50, 5, (100, 8))
    coords, _ = emb.pca_2d(matrix)
    assert coords.mean(axis=0) == pytest.approx([0.0, 0.0], abs=1e-9)


def test_pca_2d_rejects_degenerate_input():
    with pytest.raises(ValueError):
        emb.pca_2d(np.array([[1.0, 2.0]]))
    with pytest.raises(ValueError):
        emb.pca_2d(np.array([1.0, 2.0, 3.0]))


# ---- build_chart_payload ----


def _payload(records, k=2, coords=None):
    matrix = coords if coords is not None else np.zeros((len(records), 2))
    return emb.build_chart_payload(
        name="Test Stands",
        slug="test_stands",
        year=2024,
        k=k,
        scale_m=10,
        seed=42,
        records=records,
        coords=matrix,
        variance_ratios=[0.6, 0.2],
        extent_geom=EXTENT_BOX,
        aoi_geom=AOI_BOX,
        layer_info={"cluster_tile_url": None, "export": None},
    )


def test_build_chart_payload_shape():
    records = [
        {"cluster": 0, "inside": True},
        {"cluster": 1, "inside": False},
    ]
    payload = _payload(records)

    assert payload["schema"] == emb.CLUSTERS_SCHEMA
    assert payload["collection"] == emb.EMBEDDING_COLLECTION
    assert payload["k"] == 2
    assert payload["sample"] == {"inside": 1, "outside": 1, "total": 2}
    assert len(payload["clusters"]) == 2
    assert len(payload["palette"]) == 2
    assert payload["extent"]["area_ha"] > payload["aoi"]["area_ha"]


def test_build_chart_payload_scatter_points_pair_with_records():
    records = [
        {"cluster": 0, "inside": True},
        {"cluster": 1, "inside": False},
    ]
    coords = np.array([[1.5, -2.5], [3.0, 4.0]])
    payload = _payload(records, coords=coords)

    points = payload["scatter"]["points"]
    assert points[0] == {"x": 1.5, "y": -2.5, "cluster": 0, "inside": True}
    assert points[1]["inside"] is False
    assert payload["scatter"]["explained_variance_ratio"] == [0.6, 0.2]


def test_build_chart_payload_separability_for_perfect_split():
    records = [
        {"cluster": 0, "inside": True},
        {"cluster": 0, "inside": True},
        {"cluster": 1, "inside": False},
        {"cluster": 1, "inside": False},
    ]
    payload = _payload(records)
    assert payload["separability"]["jensen_shannon_divergence"] == pytest.approx(1.0)
    assert "Near-complete" in payload["separability"]["interpretation"]


def test_build_chart_payload_separability_for_no_split():
    records = [
        {"cluster": 0, "inside": True},
        {"cluster": 1, "inside": True},
        {"cluster": 0, "inside": False},
        {"cluster": 1, "inside": False},
    ]
    payload = _payload(records)
    assert payload["separability"]["jensen_shannon_divergence"] == pytest.approx(0.0)
    assert "same population" in payload["separability"]["interpretation"]


def test_build_chart_payload_without_coords_omits_scatter_points():
    records = [{"cluster": 0, "inside": True}, {"cluster": 1, "inside": False}]
    payload = emb.build_chart_payload(
        name="n",
        slug="s",
        year=2024,
        k=2,
        scale_m=10,
        seed=42,
        records=records,
        coords=None,
        variance_ratios=[],
        extent_geom=EXTENT_BOX,
        aoi_geom=AOI_BOX,
        layer_info={},
    )
    assert payload["scatter"]["points"] == []
