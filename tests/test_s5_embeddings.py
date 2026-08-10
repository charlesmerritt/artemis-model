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


# ---- clustering method registry ----


def test_every_method_declares_the_fields_the_panel_reads():
    for name, spec in emb.CLUSTER_METHODS.items():
        assert spec["label"], name
        assert spec["description"], name
        assert isinstance(spec["auto_k"], bool), name
        assert isinstance(spec["uses_k"], bool), name
        # A method cannot both take k and choose it.
        assert not (spec["auto_k"] and spec["uses_k"]), name


def test_default_method_is_registered():
    assert emb.DEFAULT_METHOD in emb.CLUSTER_METHODS


def test_resolve_methods_defaults_to_kmeans():
    assert emb.resolve_methods(None) == [emb.DEFAULT_METHOD]
    assert emb.resolve_methods("") == [emb.DEFAULT_METHOD]


def test_resolve_methods_preserves_order_and_dedupes():
    assert emb.resolve_methods("xmeans,kmeans,xmeans") == ["xmeans", "kmeans"]


def test_resolve_methods_tolerates_whitespace():
    assert emb.resolve_methods(" kmeans , lvq ") == ["kmeans", "lvq"]


def test_resolve_methods_rejects_unknown_name():
    # A typo must fail loudly rather than silently omit the requested method.
    with pytest.raises(ValueError, match="Unknown clustering method"):
        emb.resolve_methods("kmeans,kmeanz")


# ---- observed_cluster_count ----


def test_observed_cluster_count_from_labels():
    assert emb.observed_cluster_count([0, 1, 2, 1, 0]) == 3


def test_observed_cluster_count_counts_empty_trailing_cluster():
    # Fixed-k methods can leave a cluster unused; auto-k methods never report k.
    assert emb.observed_cluster_count([0, 0, 3]) == 4


def test_observed_cluster_count_rejects_empty():
    with pytest.raises(ValueError):
        emb.observed_cluster_count([])


def test_observed_cluster_count_rejects_negative():
    with pytest.raises(ValueError):
        emb.observed_cluster_count([-1, 0])


# ---- build_run ----


def test_build_run_shape():
    run = emb.build_run(
        method="kmeans",
        inside_flags=[True, True, False, False],
        cluster_ids=[0, 0, 1, 1],
        layer_info={"cluster_tile_url": None},
        k_requested=2,
    )

    assert run["method"] == "kmeans"
    assert run["label"] == emb.CLUSTER_METHODS["kmeans"]["label"]
    assert run["k_observed"] == 2
    assert run["k_requested"] == 2
    assert run["auto_k"] is False
    assert run["cluster_by_point"] == [0, 0, 1, 1]
    assert len(run["palette"]) == 2
    assert run["separability"]["jensen_shannon_divergence"] == pytest.approx(1.0)


def test_build_run_omits_k_requested_for_auto_k_methods():
    # X-means chooses its own k, so echoing --k back would be misleading.
    run = emb.build_run(
        method="xmeans",
        inside_flags=[True, False, True],
        cluster_ids=[0, 1, 2],
        layer_info={},
        k_requested=6,
    )
    assert run["auto_k"] is True
    assert run["k_requested"] is None
    assert run["k_observed"] == 3


def test_build_run_rejects_length_mismatch():
    with pytest.raises(ValueError, match="same length"):
        emb.build_run("kmeans", [True, False], [0], {}, 2)


def test_build_run_rejects_unknown_method():
    with pytest.raises(ValueError, match="Unknown clustering method"):
        emb.build_run("dbscan", [True], [0], {}, 2)


def test_build_run_labels_are_parallel_to_points():
    # cluster_by_point must stay index-aligned with the shared scatter points.
    inside = [True, False, True, False]
    ids = [2, 0, 1, 0]
    run = emb.build_run("kmeans", inside, ids, {}, 3)
    assert run["cluster_by_point"] == ids
    assert run["clusters"][0]["outside_count"] == 2


# ---- build_clusters_payload ----


def _run(method="kmeans", inside_flags=None, cluster_ids=None):
    inside_flags = inside_flags if inside_flags is not None else [True, True, False, False]
    cluster_ids = cluster_ids if cluster_ids is not None else [0, 0, 1, 1]
    return emb.build_run(method, inside_flags, cluster_ids, {}, 2)


def _payload(runs=None, inside_flags=None, coords=None, default_method=None):
    inside_flags = inside_flags if inside_flags is not None else [True, True, False, False]
    runs = runs if runs is not None else [_run()]
    matrix = coords if coords is not None else np.zeros((len(inside_flags), 2))
    return emb.build_clusters_payload(
        name="Test Stands",
        slug="test_stands",
        year=2024,
        scale_m=10,
        seed=42,
        inside_flags=inside_flags,
        coords=matrix,
        variance_ratios=[0.6, 0.2],
        extent_geom=EXTENT_BOX,
        aoi_geom=AOI_BOX,
        runs=runs,
        default_method=default_method,
    )


def test_payload_shape():
    payload = _payload()

    assert payload["schema"] == emb.CLUSTERS_SCHEMA
    assert payload["collection"] == emb.EMBEDDING_COLLECTION
    assert payload["sample"] == {"inside": 2, "outside": 2, "total": 4}
    assert payload["default_method"] == "kmeans"
    assert len(payload["runs"]) == 1
    assert payload["extent"]["area_ha"] > payload["aoi"]["area_ha"]


def test_payload_stores_scatter_geometry_once_without_cluster_ids():
    # Geometry is shared across methods; only cluster_by_point differs per run.
    coords = np.array([[1.5, -2.5], [3.0, 4.0], [0.0, 0.0], [1.0, 1.0]])
    payload = _payload(coords=coords)

    points = payload["scatter"]["points"]
    assert points[0] == {"x": 1.5, "y": -2.5, "inside": True}
    assert all("cluster" not in point for point in points)
    assert payload["scatter"]["explained_variance_ratio"] == [0.6, 0.2]


def test_payload_point_count_matches_every_run_label_count():
    runs = [_run("kmeans"), _run("lvq", cluster_ids=[0, 1, 1, 0])]
    payload = _payload(runs=runs)

    point_count = len(payload["scatter"]["points"])
    for run in payload["runs"]:
        assert len(run["cluster_by_point"]) == point_count


def test_payload_holds_multiple_methods_with_independent_divergence():
    perfect = _run("kmeans", cluster_ids=[0, 0, 1, 1])
    none = _run("lvq", cluster_ids=[0, 1, 0, 1])
    payload = _payload(runs=[perfect, none])

    by_method = {run["method"]: run for run in payload["runs"]}
    assert by_method["kmeans"]["separability"]["jensen_shannon_divergence"] == pytest.approx(1.0)
    assert by_method["lvq"]["separability"]["jensen_shannon_divergence"] == pytest.approx(0.0)


def test_payload_default_method_can_be_chosen():
    payload = _payload(runs=[_run("kmeans"), _run("xmeans")], default_method="xmeans")
    assert payload["default_method"] == "xmeans"


def test_payload_rejects_default_method_not_among_runs():
    with pytest.raises(ValueError, match="not among the runs"):
        _payload(runs=[_run("kmeans")], default_method="cobweb")


def test_payload_rejects_no_runs():
    with pytest.raises(ValueError, match="At least one"):
        _payload(runs=[])


def test_payload_without_coords_omits_scatter_points():
    payload = emb.build_clusters_payload(
        name="n",
        slug="s",
        year=2024,
        scale_m=10,
        seed=42,
        inside_flags=[True, False],
        coords=None,
        variance_ratios=[],
        extent_geom=EXTENT_BOX,
        aoi_geom=AOI_BOX,
        runs=[_run(inside_flags=[True, False], cluster_ids=[0, 1])],
    )
    assert payload["scatter"]["points"] == []
