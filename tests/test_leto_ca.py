"""Tests for the LETO cellular-automata segmentation library (pipeline/leto_ca.py)."""

import numpy as np
import pytest

from pipeline import leto_ca


CELL_ACRES = 900.0 / 4046.8564224


def _synthetic(seed=42, shape=(60, 80)):
    rng = np.random.default_rng(seed)
    valid = rng.random(shape) > 0.15
    features = {
        "STDAGE": rng.random(shape) * 120.0,
        "BALIVE": rng.random(shape) * 180.0,
        "QMD": rng.random(shape) * 14.0,
        "TPA": rng.random(shape) * 600.0,
    }
    forest_type = np.where(valid, rng.choice([121, 161, 401], size=shape), 0).astype(np.int32)
    ownership = np.full(shape, 3, dtype=np.int16)
    ownership[:, shape[1] // 2:] = 4
    ownership[~valid] = -1
    return valid, features, forest_type, ownership


def _run_segment(**cfg_overrides):
    valid, features, forest_type, ownership = _synthetic()
    cfg = {"maximum_iterations": 5, "maximum_stand_acres": 15.0, **cfg_overrides}
    labels = leto_ca.segment(features, forest_type, ownership, valid, CELL_ACRES,
                             cfg=cfg, log=lambda *a, **k: None)
    return labels, valid, ownership


def test_robust_standardize_median_iqr():
    rng = np.random.default_rng(0)
    array = rng.normal(50.0, 10.0, size=(40, 40))
    valid = np.ones_like(array, dtype=bool)
    std, median, scale = leto_ca.robust_standardize(array, valid, z_clip=4.0)
    assert median == pytest.approx(np.median(array), rel=1e-6)
    q1, q3 = np.percentile(array, [25, 75])
    assert scale == pytest.approx((q3 - q1) / 1.349, rel=1e-6)
    assert float(np.abs(std).max()) <= 4.0


def test_robust_standardize_zero_iqr_falls_back():
    array = np.full((10, 10), 7.0)
    std, median, scale = leto_ca.robust_standardize(array, np.ones((10, 10), bool), z_clip=4.0)
    assert scale == 1.0
    assert np.all(std == 0.0)


def test_initial_segments_cover_valid_and_respect_ownership():
    valid, _features, _ft, ownership = _synthetic()
    labels = leto_ca.create_initial_segments(valid, 100.0 / CELL_ACRES, ownership)
    assert np.array_equal(labels > 0, valid)
    for seg_id in np.unique(labels[labels > 0]):
        owners = np.unique(ownership[labels == seg_id])
        assert owners.size == 1


def test_segment_labels_exactly_cover_valid():
    labels, valid, _ownership = _run_segment()
    assert labels.dtype == np.int32
    assert np.array_equal(labels > 0, valid)
    # renumbered: ids are contiguous 1..max
    ids = np.unique(labels[labels > 0])
    assert ids[0] == 1 and ids[-1] == ids.size


def test_segment_never_crosses_ownership():
    labels, _valid, ownership = _run_segment()
    for seg_id in np.unique(labels[labels > 0]):
        assert np.unique(ownership[labels == seg_id]).size == 1


def test_segment_respects_maximum_stand_size():
    labels, _valid, _ownership = _run_segment()
    max_cells = max(1, int(15.0 / CELL_ACRES))
    counts = np.bincount(labels.ravel())[1:]
    assert counts.max() <= max_cells


def test_segment_pieces_are_connected():
    labels, _valid, _ownership = _run_segment()
    resplit = leto_ca.split_disconnected_segments(labels)
    assert int(resplit.max()) == int(labels.max())


def test_eight_neighbor_mode_runs():
    labels, valid, _ownership = _run_segment(use_eight_neighbors=True)
    assert np.array_equal(labels > 0, valid)


def test_segment_reports_homogeneity_scales():
    valid, features, forest_type, ownership = _synthetic()
    info = {}
    leto_ca.segment(features, forest_type, ownership, valid, CELL_ACRES,
                    cfg={"maximum_iterations": 2}, info=info, log=lambda *a, **k: None)
    assert set(info["homogeneity_scales"]) == set(features)
    assert all(np.isfinite(v) and v > 0 for v in info["homogeneity_scales"].values())


def test_split_management_units_uniform_parent_and_class():
    labels, valid, _ownership = _run_segment()
    rng = np.random.default_rng(1)
    riparian = (rng.random(labels.shape) < 0.3) & valid
    mu = leto_ca.split_management_units(labels, riparian)
    assert np.array_equal(mu > 0, valid)
    parent = leto_ca.uniform_label_lookup(mu, labels)
    rip_class = leto_ca.uniform_label_lookup(mu, riparian.astype(np.int32))
    for mu_id in np.unique(mu[mu > 0]):
        cells = mu == mu_id
        assert np.unique(labels[cells]).size == 1, "MU spans multiple parent stands"
        assert np.unique(riparian[cells]).size == 1, "MU mixes riparian classes"
        assert parent[mu_id] == labels[cells][0]
        assert rip_class[mu_id] == int(riparian[cells][0])
    # MU pieces are connected
    assert int(leto_ca.split_disconnected_segments(mu).max()) == int(mu.max())


def test_uniform_label_lookup_matches_categorical_mode_when_uniform():
    labels, valid, _ownership = _run_segment()
    rng = np.random.default_rng(2)
    riparian = (rng.random(labels.shape) < 0.25) & valid
    mu = leto_ca.split_management_units(labels, riparian)
    direct = leto_ca.uniform_label_lookup(mu, riparian.astype(np.int32))
    mode = leto_ca.segment_categorical_mode(mu, riparian.astype(np.int32))
    assert np.array_equal(direct, mode)


def test_merge_small_segments_enforces_minimum():
    labels, _valid, _ownership = _run_segment()
    counts = np.bincount(labels.ravel())[1:]
    min_cells = max(1, int(np.ceil(5.0 / CELL_ACRES)))
    small = counts[(counts > 0) & (counts < min_cells)]
    # small stands may remain only when no same-owner neighbour under the size
    # cap exists; on this synthetic raster nearly everything should be merged
    assert small.size < 0.1 * counts.size


def test_default_cfg_is_the_validated_experiment_configuration():
    cfg = leto_ca.DEFAULT_CFG
    assert cfg["variable_weights"] == {"FORTYPCD": 0.30, "STDAGE": 0.25, "BALIVE": 0.20,
                                       "QMD": 0.15, "TPA": 0.10}
    assert cfg["maximum_iterations"] == 100
    assert cfg["convergence_threshold"] == 0.001
    assert cfg["shared_edge_bonus"] == 0.1
    assert cfg["similar_merge_similarity_name"] == "STDAGE"
    assert cfg["similar_merge_max_similarity_difference"] == 10.0
    assert cfg["similar_merge_min_shared_edges"] == 1
