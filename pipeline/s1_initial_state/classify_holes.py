"""Decide which TreeMap holes are really forest, from AlphaEarth embeddings.

Two stages, run in order.

**Stage A — similarity mask.** Take the clearcut anchors (S1+S2: LANDFIRE says
forest in 2016, hole in 2022, forest again in 2024, so the regrowth *proves*
they were never converted), average their embeddings into a centroid, and score
every hole pixel by cosine similarity to it. AlphaEarth vectors are unit-length,
so cosine is a plain dot product. The threshold is chosen from the anchor
distribution rather than picked by hand: ``--recall`` sets the fraction of
anchors the mask must retain, and the threshold is that quantile of anchor
similarity. This is a recall-oriented funnel — it narrows the universe, it does
not make the final call.

**Stage B — binary classifier.** Inside the masked universe, fit
``anchor_clearcut`` (1) against ``anchor_nonforest`` (0, restricted to
herb/agriculture/shrub — the plausible confusers, not water and asphalt) and
score the ambiguous S3/S4 holes. Pixels above ``--decision`` are proposed for
add-back to TreeMap 2022; the rest stay holes.

Honest evaluation, because the failure mode here is silent
------------------------------------------------------------
- **GroupKFold on 0.25-degree blocks**, not random CV. Random folds let a model
  memorise geography and still look excellent. For the operational metric,
  Stage A and Stage B are both fitted inside each training fold before the
  held-out block is filtered and scored.
- **A label-shuffle baseline** is reported alongside. If real and shuffled scores
  are close, the features carry no signal and the headline number is an artifact.
- **Feature years are capped at 2022** (enforced in ``embed_holes``). The anchor
  label comes from LANDFIRE 2024, so a 2024 embedding would leak it.
- **Per-stratum apply rates** are reported separately. S1 anchors are stands cut
  around 2014-2016, so by 2022 they are ~6-8 year old pine. A *fresh* cut looks
  different, and much of S3 is fresh. Watch the S3 rate for evidence of that
  generalisation gap rather than assuming it away.
- **The reported spatial-CV metrics are post-selection.** The exploratory
  full-data sweep chose six exemplars and the age-referenced design. Fold-local
  fitting removes direct validation-fold fit leakage, but an independent test
  set or nested selection rule is still needed for an unbiased estimate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

from pipeline.s1_initial_state.embed_holes import check_feature_years

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "data/interim/treemap_holes"
ACRES_PER_PIXEL = 0.2224

STRATUM_NAMES = {
    1: "S1_cut_pre2016_regrown",
    2: "S2_cut_2016_2022_regrown",
    3: "S3_cut_2016_2022_open",
    4: "S4_regrown_only",
    5: "S5_no_evidence",
}


def band_columns(table: pd.DataFrame, year: int) -> list[str]:
    cols = sorted(c for c in table.columns if c.startswith("A") and c.endswith(f"_{year}"))
    if not cols:
        raise ValueError(f"no embedding columns for year {year}; have {table.columns.tolist()[:8]}...")
    return cols


def unit_rows(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.where(norms == 0, 1.0, norms)


def anchor_exemplars(table: pd.DataFrame, anchor_years: list[int], n_clusters: int,
                     seed: int) -> np.ndarray:
    """K unit-norm exemplar vectors describing what a cut-and-managed stand looks like.

    A *single* mean vector is a bad summary here: the anchors span roughly age 2
    to age 8 post-harvest (S1 was cut ~2014-2016, sampled in both 2018 and 2022),
    and averaging a bare fresh cut with an established stand yields a vector that
    represents neither. k-means keeps the modes apart, and similarity is then the
    max over exemplars — the same ``agg="max"`` pattern as
    ``notebooks/clearcut_ag_common.similarity_image``.
    """
    from sklearn.cluster import KMeans

    rows = table[table.role == "anchor_clearcut"]
    pooled = np.vstack([rows[band_columns(rows, y)].to_numpy(float) for y in anchor_years])
    k = min(n_clusters, len(pooled))
    centres = KMeans(n_clusters=k, random_state=seed, n_init=10).fit(pooled).cluster_centers_
    return unit_rows(centres)


def stage_a_similarity(table: pd.DataFrame, cols: list[str], recall: float,
                       exemplars: np.ndarray) -> tuple[pd.Series, float]:
    """Max cosine similarity to any anchor exemplar + the recall-based threshold."""
    sim = max_exemplar_similarity(table, cols, exemplars)
    threshold = float(np.quantile(sim[table.role == "anchor_clearcut"], 1.0 - recall))
    return sim, threshold


def max_exemplar_similarity(table: pd.DataFrame, cols: list[str],
                            exemplars: np.ndarray) -> pd.Series:
    """Max cosine similarity to any fitted Stage-A exemplar."""
    sim_all = unit_rows(table[cols].to_numpy(float)) @ exemplars.T
    return pd.Series(sim_all.max(axis=1), index=table.index)


def training_matrix(table: pd.DataFrame, anchor_years: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Stack the anchor rows once per `anchor_years` entry, features from that year.

    With a single year this is the plain design. With several it becomes
    **age-referenced**: the S1 anchors were cut around 2014-2016, so their 2018
    embedding shows them at roughly age 2-4 and their 2022 embedding at age 6-8.
    Stacking both teaches "post-harvest managed forest at *any* stand age"
    instead of "6-8 year old pine", which is what the apply set actually needs —
    much of S3 is a fresh cut, and a fresh cut looks nothing like established
    pine. Stable non-forest contributes a row per year too; it looks much the
    same in both, which is precisely the contrast being taught.

    Both rows of a pixel share its spatial block, so GroupKFold still keeps them
    in the same fold and the duplication cannot leak across the CV split.
    """
    anchors = table[table.role.isin(["anchor_clearcut", "anchor_nonforest"])]
    xs, ys, groups = [], [], []
    for year in anchor_years:
        cols = band_columns(anchors, year)
        xs.append(anchors[cols].to_numpy(float))
        ys.append((anchors.role == "anchor_clearcut").astype(int).to_numpy())
        groups.append(anchors.block.to_numpy())
    return np.vstack(xs), np.concatenate(ys), np.concatenate(groups)


def shuffle_anchor_labels(y: np.ndarray, seed: int, repeats: int = 1) -> np.ndarray:
    """Permute unique-anchor labels while keeping repeated anchor-year rows paired."""
    if repeats < 1 or len(y) % repeats:
        raise ValueError("label rows must divide evenly across anchor-year repeats")
    rows = len(y) // repeats
    base = y[:rows]
    for repeat in range(1, repeats):
        if not np.array_equal(y[repeat * rows:(repeat + 1) * rows], base):
            raise ValueError("anchor-year label copies are not paired")
    return np.tile(np.random.default_rng(seed).permutation(base), repeats)


def block_cv_scores(x: np.ndarray, y: np.ndarray, groups: np.ndarray, seed: int,
                    label_repeats: int = 1):
    """Block-CV AUC and accuracy, plus the label-shuffle baseline for the same split."""
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
    cv = GroupKFold(n_splits=min(5, len(np.unique(groups))))
    prob = cross_val_predict(model, x, y, groups=groups, cv=cv, method="predict_proba")[:, 1]
    shuffled = shuffle_anchor_labels(y, seed, label_repeats)
    prob_shuffled = cross_val_predict(model, x, shuffled, groups=groups, cv=cv,
                                      method="predict_proba")[:, 1]
    return (roc_auc_score(y, prob), ((prob >= 0.5).astype(int) == y).mean(),
            roc_auc_score(shuffled, prob_shuffled))


def pipeline_fold_predictions(
    training: pd.DataFrame,
    held_out: pd.DataFrame,
    *,
    feature_year: int,
    anchor_years: list[int],
    recall: float,
    n_clusters: int,
    seed: int,
) -> pd.DataFrame:
    """Fit both stages on one training fold and score its held-out anchors once.

    Stage A's exemplars and recall threshold are learned only from ``training``.
    Stage B matches the deployed fit: all training anchors, stacked over
    ``anchor_years``. Held-out anchors are scored only at ``feature_year`` because
    that is the surface the operational pipeline applies.
    """
    training = training[training.role.isin(["anchor_clearcut", "anchor_nonforest"])]
    held_out = held_out[held_out.role.isin(["anchor_clearcut", "anchor_nonforest"])]
    cols = band_columns(training, feature_year)

    exemplars = anchor_exemplars(training, anchor_years, n_clusters, seed)
    train_sim = max_exemplar_similarity(training, cols, exemplars)
    threshold = float(
        np.quantile(train_sim[training.role == "anchor_clearcut"], 1.0 - recall)
    )
    held_out_sim = max_exemplar_similarity(held_out, cols, exemplars)

    x_train, y_train, _ = training_matrix(training, anchor_years)
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
    model.fit(x_train, y_train)
    probability = model.predict_proba(held_out[cols].to_numpy(float))[:, 1]

    return pd.DataFrame({
        "source_index": held_out.index.to_numpy(),
        "role": held_out.role.to_numpy(),
        "block": held_out.block.to_numpy(),
        "truth": (held_out.role == "anchor_clearcut").astype(int).to_numpy(),
        "probability": probability,
        "similarity": held_out_sim.to_numpy(),
        "stage_a_threshold": threshold,
        "passed_stage_a": (held_out_sim >= threshold).to_numpy(),
    })


def pipeline_block_cv_predictions(
    table: pd.DataFrame,
    *,
    feature_year: int,
    anchor_years: list[int],
    recall: float,
    n_clusters: int,
    seed: int,
) -> pd.DataFrame:
    """Out-of-fold predictions for the complete two-stage operational pipeline."""
    anchors = table[table.role.isin(["anchor_clearcut", "anchor_nonforest"])]
    groups = anchors.block.to_numpy()
    cv = GroupKFold(n_splits=min(5, len(np.unique(groups))))
    folds = []
    for fold, (train_idx, test_idx) in enumerate(cv.split(anchors, groups=groups)):
        predictions = pipeline_fold_predictions(
            anchors.iloc[train_idx],
            anchors.iloc[test_idx],
            feature_year=feature_year,
            anchor_years=anchor_years,
            recall=recall,
            n_clusters=n_clusters,
            seed=seed,
        )
        folds.append(predictions.assign(fold=fold))
    return pd.concat(folds, ignore_index=True)


def pipeline_cv_metrics(predictions: pd.DataFrame, decision: float) -> dict[str, float | int]:
    """Conditional Stage-B diagnostics and end-to-end two-stage decision metrics."""
    truth = predictions.truth.to_numpy(dtype=int)
    passed = predictions.passed_stage_a.to_numpy(dtype=bool)
    probability = predictions.probability.to_numpy(float)
    survivors = predictions[passed]

    predicted = passed & (probability >= decision)
    positive = truth == 1
    negative = ~positive
    tp = int((predicted & positive).sum())
    fn = int((~predicted & positive).sum())
    fp = int((predicted & negative).sum())
    tn = int((~predicted & negative).sum())
    sensitivity = tp / (tp + fn) if tp + fn else float("nan")
    specificity = tn / (tn + fp) if tn + fp else float("nan")
    precision = tp / (tp + fp) if tp + fp else float("nan")
    f1 = (
        2 * precision * sensitivity / (precision + sensitivity)
        if np.isfinite(precision) and np.isfinite(sensitivity) and precision + sensitivity
        else float("nan")
    )

    survivor_truth = survivors.truth.to_numpy(dtype=int)
    survivor_probability = survivors.probability.to_numpy(float)
    survivor_auc = (
        roc_auc_score(survivor_truth, survivor_probability)
        if len(np.unique(survivor_truth)) == 2
        else float("nan")
    )
    survivor_accuracy = (
        ((survivor_probability >= decision).astype(int) == survivor_truth).mean()
        if len(survivors)
        else float("nan")
    )
    return {
        "pipeline_accuracy": float((predicted == positive).mean()),
        "pipeline_balanced_accuracy": float((sensitivity + specificity) / 2),
        "pipeline_sensitivity": float(sensitivity),
        "pipeline_specificity": float(specificity),
        "pipeline_precision": float(precision),
        "pipeline_f1": float(f1),
        "pipeline_true_positive": tp,
        "pipeline_false_negative": fn,
        "pipeline_false_positive": fp,
        "pipeline_true_negative": tn,
        "stage_b_survivor_auc": float(survivor_auc),
        "stage_b_survivor_accuracy": float(survivor_accuracy),
        "n_stage_b_survivors": int(len(survivors)),
        "n_stage_b_survivor_positive": int(survivor_truth.sum()),
        "n_stage_b_survivor_negative": int((1 - survivor_truth).sum()),
        "stage_a_clearcut_pass_rate": float(predictions.loc[positive, "passed_stage_a"].mean()),
        "stage_a_nonforest_pass_rate": float(predictions.loc[negative, "passed_stage_a"].mean()),
    }


def fit_and_evaluate(table: pd.DataFrame, cols: list[str], seed: int,
                     anchor_years: list[int] | None = None,
                     stage_a_recall: float | None = None,
                     stage_a_clusters: int = 6,
                     decision_threshold: float = 0.5):
    """Fit the anchor classifier and report block-CV scores against a shuffle baseline.

    Final gated-decision metrics over every held-out anchor evaluate the complete
    rule used on ambiguous strata, using anchors as the labelled proxy population.
    Stage-B AUC among Stage-A survivors is reported separately as a conditional
    diagnostic; the all-anchor AUC is kept for comparability with the exploratory
    sweep. Both stages are refit inside each spatial training fold, and each
    held-out anchor is scored once at the operational feature year.
    """
    if anchor_years:
        x, y, groups = training_matrix(table, anchor_years)
    else:
        train = table[table.role.isin(["anchor_clearcut", "anchor_nonforest"])]
        x = train[cols].to_numpy(float)
        y = (train.role == "anchor_clearcut").astype(int).to_numpy()
        groups = train.block.to_numpy()

    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
    n_splits = min(5, len(np.unique(groups)))
    label_repeats = len(anchor_years) if anchor_years else 1
    auc, acc, auc_shuffled = block_cv_scores(x, y, groups, seed, label_repeats)

    print(f"\nStage B — GroupKFold({n_splits}) on 0.25 deg blocks, n={len(y):,} "
          f"({y.sum():,} clearcut / {(1 - y).sum():,} non-forest)"
          + (f", age-referenced over {anchor_years}" if anchor_years else ""))
    print(f"  block-CV AUC       {auc:.4f}   accuracy {acc:.4f}   (all anchors)")
    print(f"  label-shuffle AUC  {auc_shuffled:.4f}   <- must be near 0.5, else the signal is spurious")
    if auc - auc_shuffled < 0.15:
        print("  WARNING: real and shuffled scores are close; do not trust the apply-set rates.")

    metrics = {"auc_block_cv": auc, "accuracy_block_cv": acc, "auc_label_shuffle": auc_shuffled}

    if stage_a_recall is not None:
        feature_year = int(cols[0].rsplit("_", 1)[1])
        predictions = pipeline_block_cv_predictions(
            table,
            feature_year=feature_year,
            anchor_years=anchor_years or [feature_year],
            recall=stage_a_recall,
            n_clusters=stage_a_clusters,
            seed=seed,
        )
        pipeline_metrics = pipeline_cv_metrics(predictions, decision=decision_threshold)
        print(f"  end-to-end two-stage anchor proxy: balanced accuracy "
              f"{pipeline_metrics['pipeline_balanced_accuracy']:.4f}   "
              f"sensitivity {pipeline_metrics['pipeline_sensitivity']:.4f}   "
              f"specificity {pipeline_metrics['pipeline_specificity']:.4f}   "
              f"precision {pipeline_metrics['pipeline_precision']:.4f}  <- FINAL RULE")
        print(f"  Stage B among fold-local Stage-A survivors: AUC "
              f"{pipeline_metrics['stage_b_survivor_auc']:.4f}   accuracy "
              f"{pipeline_metrics['stage_b_survivor_accuracy']:.4f}   "
              f"n={pipeline_metrics['n_stage_b_survivors']:,} "
              f"({pipeline_metrics['n_stage_b_survivor_positive']:,} pos / "
              f"{pipeline_metrics['n_stage_b_survivor_negative']:,} neg)  <- CONDITIONAL")
        metrics |= pipeline_metrics

    model.fit(x, y)
    return model, metrics


def report_apply(table: pd.DataFrame, prob: pd.Series, in_mask: pd.Series, decision: float) -> pd.DataFrame:
    apply_rows = table.role == "apply"
    frame = pd.DataFrame({
        "stratum": table.loc[apply_rows, "stratum"].map(STRATUM_NAMES),
        "in_similarity_mask": in_mask[apply_rows],
        "prob": prob[apply_rows],
    })
    frame["add_back"] = frame.in_similarity_mask & (frame.prob >= decision)
    summary = frame.groupby("stratum").agg(
        points=("prob", "size"),
        passed_stage_a=("in_similarity_mask", "mean"),
        mean_prob=("prob", "mean"),
        frac_add_back=("add_back", "mean"),
    )
    print("\nApply set (S3/S4), share proposed for add-back:")
    print(summary.to_string(float_format=lambda v: f"{v:,.3f}"))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embeddings", type=Path, default=DATA_DIR / "hole_embeddings.csv")
    parser.add_argument("--feature-year", type=int, default=2022)
    parser.add_argument("--recall", type=float, default=0.90,
                        help="fraction of clearcut anchors the Stage-A mask must retain")
    parser.add_argument("--decision", type=float, default=0.5,
                        help="Stage-B probability above which a hole is proposed for add-back")
    parser.add_argument("--anchor-clusters", type=int, default=6,
                        help="k-means exemplars representing post-harvest stand ages in Stage A")
    parser.add_argument("--anchor-years", type=int, nargs="*", default=[2018, 2022],
                        help="years to stack anchor rows over (age-referenced training); "
                             "pass a single year for the plain design")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    check_feature_years([args.feature_year, *args.anchor_years])
    table = pd.read_csv(args.embeddings)
    cols = band_columns(table, args.feature_year)
    print(f"{len(table):,} points, {len(cols)} embedding bands for {args.feature_year}")
    print(table.role.value_counts().to_string())

    exemplars = anchor_exemplars(table, args.anchor_years, args.anchor_clusters, args.seed)
    sim, threshold = stage_a_similarity(table, cols, args.recall, exemplars)
    in_mask = sim >= threshold
    print(f"\nStage A — max cosine similarity to {len(exemplars)} clearcut exemplars "
          f"(k-means over anchor years {args.anchor_years})")
    print(f"  threshold {threshold:.4f} (retains {args.recall:.0%} of anchors)")
    for role in ["anchor_clearcut", "anchor_nonforest", "apply"]:
        rows = table.role == role
        print(f"  {role:<17} median sim {sim[rows].median():.4f}   "
              f"passes mask {in_mask[rows].mean():.1%}")

    model, metrics = fit_and_evaluate(
        table,
        cols,
        args.seed,
        args.anchor_years,
        stage_a_recall=args.recall,
        stage_a_clusters=args.anchor_clusters,
        decision_threshold=args.decision,
    )
    prob = pd.Series(model.predict_proba(table[cols].to_numpy(float))[:, 1], index=table.index)
    summary = report_apply(table, prob, in_mask, args.decision)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    scaler, logreg = model.named_steps["standardscaler"], model.named_steps["logisticregression"]
    # Fold the scaler into the linear model so Earth Engine can evaluate one dot product.
    coef = (logreg.coef_[0] / scaler.scale_).tolist()
    intercept = float(logreg.intercept_[0] - (logreg.coef_[0] * scaler.mean_ / scaler.scale_).sum())
    (args.out_dir / "hole_model.json").write_text(json.dumps({
        "feature_year": args.feature_year,
        "bands": [c.rsplit("_", 1)[0] for c in cols],
        "coef": coef,
        "intercept": intercept,
        "anchor_exemplars": exemplars.tolist(),
        "anchor_years": args.anchor_years,
        "similarity_threshold": threshold,
        "decision_threshold": args.decision,
        "metrics": metrics,
    }, indent=2))
    scored = pd.concat(
        [table, pd.DataFrame({"similarity": sim, "prob": prob, "in_similarity_mask": in_mask})],
        axis=1,
    )
    scored.to_csv(args.out_dir / "hole_scored_points.csv", index=False)
    summary.to_csv(args.out_dir / "hole_apply_summary.csv")
    print(f"\nwrote hole_model.json, hole_scored_points.csv, hole_apply_summary.csv to {args.out_dir}")


if __name__ == "__main__":
    main()
