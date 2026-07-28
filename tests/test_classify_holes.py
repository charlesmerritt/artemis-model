"""Tests for the hole classifier, focused on the two silent-failure risks.

1. The scaler is folded into a single linear form so Earth Engine can evaluate
   the model as one dot product. If that algebra is wrong, the exported raster
   disagrees with the local model and nothing surfaces the discrepancy.
2. Post-2022 embeddings would leak the LF2024-derived anchor label, so the year
   guard must actually raise.
"""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / f"pipeline/s1_initial_state/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


classify = _load("classify_holes")
embed = _load("embed_holes")


def _synthetic_table(n=400, bands=8, seed=0, years=(2018, 2022)) -> pd.DataFrame:
    """Two separable unit-norm clusters plus an ambiguous apply set, for each year."""
    rng = np.random.default_rng(seed)
    centre_pos = np.zeros(bands)
    centre_pos[0] = 1.0
    centre_neg = np.zeros(bands)
    centre_neg[1] = 1.0
    roles, strata, frames = [], [], []
    for year in years:
        rows = []
        for centre, role, stratum in [(centre_pos, "anchor_clearcut", 1),
                                      (centre_neg, "anchor_nonforest", 5),
                                      ((centre_pos + centre_neg) / 2, "apply", 3)]:
            vecs = centre + rng.normal(0, 0.25, size=(n, bands))
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
            rows.append(vecs)
            if year == years[0]:
                roles += [role] * n
                strata += [stratum] * n
        frames.append(pd.DataFrame(np.vstack(rows),
                                   columns=[f"A{i:02d}_{year}" for i in range(bands)]))
    table = pd.concat(frames, axis=1)
    table["role"] = roles
    table["stratum"] = strata
    table["block"] = rng.integers(0, 6, size=len(table)).astype(str)
    return table


def test_folded_coefficients_reproduce_the_pipeline_exactly():
    """The EE-side dot product must match sklearn's scaled pipeline to float precision."""
    table = _synthetic_table()
    cols = classify.band_columns(table, 2022)
    model, _ = classify.fit_and_evaluate(table, cols, seed=1)

    scaler = model.named_steps["standardscaler"]
    logreg = model.named_steps["logisticregression"]
    coef = logreg.coef_[0] / scaler.scale_
    intercept = logreg.intercept_[0] - (logreg.coef_[0] * scaler.mean_ / scaler.scale_).sum()

    x = table[cols].to_numpy(float)
    folded = 1.0 / (1.0 + np.exp(-(x @ coef + intercept)))
    np.testing.assert_allclose(folded, model.predict_proba(x)[:, 1], rtol=1e-9, atol=1e-9)


def test_similarity_threshold_retains_the_requested_anchor_recall():
    table = _synthetic_table()
    cols = classify.band_columns(table, 2022)
    exemplars = classify.anchor_exemplars(table, [2022], n_clusters=4, seed=0)
    sim, threshold = classify.stage_a_similarity(table, cols, 0.90, exemplars)

    anchors = table.role == "anchor_clearcut"
    assert (sim[anchors] >= threshold).mean() == pytest.approx(0.90, abs=0.02)
    # Clearcut anchors must sit closer to their own exemplars than non-forest does.
    assert sim[anchors].median() > sim[table.role == "anchor_nonforest"].median()


def test_anchor_exemplars_are_unit_norm_and_multi_modal():
    table = _synthetic_table()
    exemplars = classify.anchor_exemplars(table, [2022], n_clusters=5, seed=0)
    assert exemplars.shape == (5, 8)
    np.testing.assert_allclose(np.linalg.norm(exemplars, axis=1), 1.0, rtol=1e-9)


def test_exemplars_rescue_a_bimodal_anchor_population_that_one_centroid_averages_away():
    """The real Stage-A failure: fresh cuts and established stands are separate modes.

    Averaging them yields a vector between the two that represents neither, so
    the minority mode falls below threshold. Exemplars must keep both modes.
    """
    rng = np.random.default_rng(0)
    bands = 8
    young, old = np.zeros(bands), np.zeros(bands)
    young[0], old[1] = 1.0, 1.0  # orthogonal modes, as unlike as two anchors get

    def blob(centre, n):
        v = centre + rng.normal(0, 0.05, size=(n, bands))
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    # Deliberately lopsided: only 15% of anchors are the "fresh cut" mode.
    anchors = np.vstack([blob(old, 340), blob(young, 60)])
    table = pd.DataFrame(anchors, columns=[f"A{i:02d}_2022" for i in range(bands)])
    table["role"] = "anchor_clearcut"
    table["stratum"] = 1
    table["block"] = rng.integers(0, 6, size=len(table)).astype(str)
    cols = classify.band_columns(table, 2022)
    is_young = np.zeros(len(table), dtype=bool)
    is_young[340:] = True

    one = classify.anchor_exemplars(table, [2022], n_clusters=1, seed=0)
    many = classify.anchor_exemplars(table, [2022], n_clusters=2, seed=0)
    sim_one, thr_one = classify.stage_a_similarity(table, cols, 0.90, one)
    sim_many, thr_many = classify.stage_a_similarity(table, cols, 0.90, many)

    # One centroid: the minority mode is far from the mean and gets excluded.
    assert (sim_one[is_young] >= thr_one).mean() < 0.5
    # Two exemplars: both modes are retained at roughly the global 90% recall.
    assert (sim_many[is_young] >= thr_many).mean() > 0.8
    assert (sim_many[~is_young] >= thr_many).mean() > 0.8


def test_age_referenced_training_stacks_one_row_per_anchor_year():
    table = _synthetic_table()
    x1, y1, g1 = classify.training_matrix(table, [2022])
    x2, y2, g2 = classify.training_matrix(table, [2018, 2022])
    assert len(x2) == 2 * len(x1)
    assert y2.sum() == 2 * y1.sum()
    # Both rows of a pixel keep its block, so GroupKFold cannot split them apart.
    assert sorted(set(g2)) == sorted(set(g1))


def test_pipeline_fold_fits_stage_a_without_held_out_rows():
    """Changing held-out embeddings must not change the fold's Stage-A threshold."""
    table = _synthetic_table(n=50)
    table["block"] = np.resize(np.array([str(i) for i in range(5)]), len(table))
    anchors = table[table.role.isin(["anchor_clearcut", "anchor_nonforest"])]
    train = anchors[anchors.block != "0"]
    held_out = anchors[anchors.block == "0"]

    first = classify.pipeline_fold_predictions(
        train,
        held_out,
        feature_year=2022,
        anchor_years=[2018, 2022],
        recall=0.90,
        n_clusters=4,
        seed=1,
    )
    changed = held_out.copy()
    changed.loc[:, classify.band_columns(changed, 2022)] *= -1
    second = classify.pipeline_fold_predictions(
        train,
        changed,
        feature_year=2022,
        anchor_years=[2018, 2022],
        recall=0.90,
        n_clusters=4,
        seed=1,
    )

    assert first.stage_a_threshold.nunique() == 1
    assert second.stage_a_threshold.nunique() == 1
    assert first.stage_a_threshold.iloc[0] == pytest.approx(second.stage_a_threshold.iloc[0])


def test_pipeline_block_cv_scores_each_held_out_anchor_once_at_feature_year():
    """Operational CV validates unique 2022 anchors, not duplicated anchor-year rows."""
    table = _synthetic_table(n=50)
    table["block"] = np.resize(np.array([str(i) for i in range(5)]), len(table))
    anchors = table[table.role.isin(["anchor_clearcut", "anchor_nonforest"])]

    predictions = classify.pipeline_block_cv_predictions(
        table,
        feature_year=2022,
        anchor_years=[2018, 2022],
        recall=0.90,
        n_clusters=4,
        seed=1,
    )

    assert len(predictions) == len(anchors)
    assert predictions.source_index.is_unique
    assert set(predictions.role) == {"anchor_clearcut", "anchor_nonforest"}
    assert predictions.passed_stage_a.dtype == bool


def test_pipeline_metrics_count_stage_a_rejections_in_final_decisions():
    predictions = pd.DataFrame({
        "truth": [1, 1, 0, 0],
        "probability": [0.9, 0.9, 0.8, 0.1],
        "passed_stage_a": [True, False, True, False],
    })

    metrics = classify.pipeline_cv_metrics(predictions, decision=0.5)

    assert metrics["pipeline_true_positive"] == 1
    assert metrics["pipeline_false_negative"] == 1
    assert metrics["pipeline_false_positive"] == 1
    assert metrics["pipeline_true_negative"] == 1
    assert metrics["pipeline_balanced_accuracy"] == pytest.approx(0.5)
    assert metrics["stage_b_survivor_auc"] == pytest.approx(1.0)


def test_pipeline_metrics_report_undefined_values_without_aborting():
    predictions = pd.DataFrame({
        "truth": [1, 1, 0, 0],
        "probability": [0.9, 0.8, 0.7, 0.1],
        "passed_stage_a": [True, True, False, False],
    })

    metrics = classify.pipeline_cv_metrics(predictions, decision=1.0)

    assert metrics["pipeline_true_positive"] == 0
    assert metrics["pipeline_false_negative"] == 2
    assert metrics["pipeline_false_positive"] == 0
    assert metrics["pipeline_true_negative"] == 2
    assert np.isnan(metrics["pipeline_precision"])
    assert np.isnan(metrics["pipeline_f1"])
    assert np.isnan(metrics["stage_b_survivor_auc"])
    assert metrics["stage_b_survivor_accuracy"] == pytest.approx(0.0)


def test_label_shuffle_keeps_anchor_year_copies_paired():
    labels = np.array([1, 0, 1, 1, 0, 1])

    shuffled = classify.shuffle_anchor_labels(labels, seed=7, repeats=2)

    np.testing.assert_array_equal(shuffled[:3], shuffled[3:])
    assert sorted(shuffled[:3]) == [0, 1, 1]


def test_band_columns_rejects_a_year_with_no_embeddings():
    with pytest.raises(ValueError, match="no embedding columns for year 2019"):
        classify.band_columns(_synthetic_table(), 2019)


@pytest.mark.parametrize("years", [[2023], [2024], [2020, 2024]])
def test_post_2022_feature_years_are_refused(years):
    with pytest.raises(ValueError, match="leak"):
        embed.check_feature_years(years)


def test_feature_years_through_2022_are_allowed():
    embed.check_feature_years([2017, 2020, 2022])


def test_unit_rows_normalises_and_survives_a_zero_vector():
    x = np.array([[3.0, 4.0], [0.0, 0.0]])
    out = classify.unit_rows(x)
    np.testing.assert_allclose(out[0], [0.6, 0.8])
    np.testing.assert_allclose(out[1], [0.0, 0.0])


def test_model_json_round_trips_through_the_ee_contract():
    """Fields embed_holes.probability_image reads must all be present and typed."""
    table = _synthetic_table()
    cols = classify.band_columns(table, 2022)
    exemplars = classify.anchor_exemplars(table, [2022], n_clusters=3, seed=0)
    sim, threshold = classify.stage_a_similarity(table, cols, 0.9, exemplars)
    model, metrics = classify.fit_and_evaluate(table, cols, seed=1)
    scaler = model.named_steps["standardscaler"]
    logreg = model.named_steps["logisticregression"]
    payload = json.loads(json.dumps({
        "feature_year": 2022,
        "bands": [c.rsplit("_", 1)[0] for c in cols],
        "coef": (logreg.coef_[0] / scaler.scale_).tolist(),
        "intercept": float(logreg.intercept_[0] - (logreg.coef_[0] * scaler.mean_ / scaler.scale_).sum()),
        "anchor_exemplars": exemplars.tolist(),
        "similarity_threshold": threshold,
        "decision_threshold": 0.5,
        "metrics": metrics,
    }))
    assert len(payload["coef"]) == len(payload["bands"]) == len(payload["anchor_exemplars"][0])
    assert all(b.startswith("A") and "_" not in b for b in payload["bands"])
    assert payload["feature_year"] <= embed.MAX_FEATURE_YEAR
