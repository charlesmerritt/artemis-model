"""Compare the leaked PR #9 metric with fold-local operational evaluation."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pipeline.s1_initial_state.classify_holes import (  # noqa: E402
    anchor_exemplars,
    band_columns,
    block_cv_scores,
    max_exemplar_similarity,
    pipeline_block_cv_predictions,
    pipeline_cv_metrics,
    stage_a_similarity,
    training_matrix,
)

HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fold_local_conditional_refit(table: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Old conditional-refit design, but with Stage A fitted inside each fold."""
    anchors = table[table.role.isin(["anchor_clearcut", "anchor_nonforest"])]
    groups = anchors.block.to_numpy()
    cv = GroupKFold(n_splits=5)
    frames = []
    for fold, (train_idx, test_idx) in enumerate(cv.split(anchors, groups=groups)):
        training, held_out = anchors.iloc[train_idx], anchors.iloc[test_idx]
        cols = band_columns(training, config["feature_year"])
        exemplars = anchor_exemplars(
            training,
            config["anchor_years"],
            config["stage_a_clusters"],
            config["seed"],
        )
        train_sim = max_exemplar_similarity(training, cols, exemplars)
        threshold = float(np.quantile(
            train_sim[training.role == "anchor_clearcut"],
            1.0 - config["stage_a_recall"],
        ))
        test_sim = max_exemplar_similarity(held_out, cols, exemplars)
        training = training[train_sim >= threshold]
        held_out = held_out[test_sim >= threshold]

        x_train, y_train, _ = training_matrix(training, config["anchor_years"])
        model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
        model.fit(x_train, y_train)
        truth = (held_out.role == "anchor_clearcut").astype(int).to_numpy()
        for year in config["anchor_years"]:
            probability = model.predict_proba(
                held_out[band_columns(held_out, year)].to_numpy(float)
            )[:, 1]
            frames.append(pd.DataFrame({
                "fold": fold,
                "year": year,
                "truth": truth,
                "probability": probability,
            }))
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    started = time.monotonic()
    config = json.loads((HERE / "config.json").read_text())
    input_path = ROOT / config["input"]
    table = pd.read_csv(input_path)
    cols = band_columns(table, config["feature_year"])

    # Baseline: the previous implementation fitted Stage A globally, selected
    # survivors globally, and then duplicated each survivor over both anchor years.
    exemplars = anchor_exemplars(
        table, config["anchor_years"], config["stage_a_clusters"], config["seed"]
    )
    similarity, threshold = stage_a_similarity(
        table, cols, config["stage_a_recall"], exemplars
    )
    roles = table.role.isin(["anchor_clearcut", "anchor_nonforest"])
    old_survivors = table[roles & (similarity >= threshold)].reset_index(drop=True)
    old_x, old_y, old_groups = training_matrix(old_survivors, config["anchor_years"])
    old_auc, old_accuracy, _ = block_cv_scores(
        old_x,
        old_y,
        old_groups,
        config["seed"],
        label_repeats=len(config["anchor_years"]),
    )

    fold_local_refit = fold_local_conditional_refit(table, config)
    refit_auc = roc_auc_score(fold_local_refit.truth, fold_local_refit.probability)
    refit_accuracy = float(
        ((fold_local_refit.probability >= config["decision_threshold"]).astype(int)
         == fold_local_refit.truth).mean()
    )

    # Corrected evaluation: fit both stages inside each outer training fold,
    # then score every held-out anchor once at the operational feature year.
    predictions = pipeline_block_cv_predictions(
        table,
        feature_year=config["feature_year"],
        anchor_years=config["anchor_years"],
        recall=config["stage_a_recall"],
        n_clusters=config["stage_a_clusters"],
        seed=config["seed"],
    )
    survivors = predictions[predictions.passed_stage_a]
    pipeline_metrics = pipeline_cv_metrics(
        predictions, decision=config["decision_threshold"]
    )
    new_auc = roc_auc_score(survivors.truth, survivors.probability)

    fold_rows = []
    for fold, frame in predictions.groupby("fold"):
        selected = frame[frame.passed_stage_a]
        fold_rows.append({
            "fold": int(fold),
            "held_out_blocks": ",".join(sorted(frame.block.astype(str).unique())),
            "stage_a_threshold": float(frame.stage_a_threshold.iloc[0]),
            "held_out_n": int(len(frame)),
            "survivor_n": int(len(selected)),
            "survivor_positive": int(selected.truth.sum()),
            "survivor_negative": int((1 - selected.truth).sum()),
            "survivor_auc": (
                float(roc_auc_score(selected.truth, selected.probability))
                if selected.truth.nunique() == 2 else None
            ),
            "survivor_accuracy": float(
                ((selected.probability >= config["decision_threshold"]).astype(int)
                 == selected.truth).mean()
            ),
        })

    metrics = {
        "old_global_prefilter": {
            "auc": float(old_auc),
            "accuracy": float(old_accuracy),
            "n_duplicated_anchor_year_rows": int(len(old_y)),
            "positive": int(old_y.sum()),
            "negative": int((1 - old_y).sum()),
        },
        "fold_local_operational_pipeline": {
            **pipeline_metrics,
        },
        "fold_local_conditional_refit_both_years": {
            "auc": float(refit_auc),
            "accuracy": refit_accuracy,
            "n_duplicated_anchor_year_rows": int(len(fold_local_refit)),
            "positive": int(fold_local_refit.truth.sum()),
            "negative": int((1 - fold_local_refit.truth).sum()),
        },
        "delta_survivor_auc_new_minus_old": float(new_auc - old_auc),
    }

    metrics_dir = HERE / "metrics"
    logs_dir = HERE / "logs"
    metrics_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    (metrics_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    pd.DataFrame(fold_rows).to_csv(metrics_dir / "fold_metrics.csv", index=False)

    summary = (
        f"old global-prefilter AUC={old_auc:.6f}, n={len(old_y)}\n"
        f"fold-local conditional-refit AUC={refit_auc:.6f}, n={len(fold_local_refit)}\n"
        f"fold-local Stage-B survivor AUC={new_auc:.6f}, n={len(survivors)}\n"
        f"end-to-end balanced accuracy={pipeline_metrics['pipeline_balanced_accuracy']:.6f}\n"
    )
    (logs_dir / "run.txt").write_text(summary)
    print(summary, end="")

    manifest = {
        "command": "uv run python experiments/2026-07-28_16-33-pr9-validation-audit/experiment.py",
        "repo_commit_before_run": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip(),
        "input": str(input_path.relative_to(ROOT)),
        "input_sha256": sha256(input_path),
        "input_regeneration_commands": [
            "uv run python -m pipeline.s1_initial_state.sample_hole_points",
            "uv run python -m pipeline.s1_initial_state.embed_holes sample",
        ],
        "outputs": [
            "metrics/metrics.json",
            "metrics/fold_metrics.csv",
            "logs/run.txt",
        ],
        "runtime_seconds": time.monotonic() - started,
        "versions": {
            name: importlib.metadata.version(name)
            for name in ["numpy", "pandas", "scikit-learn"]
        },
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
