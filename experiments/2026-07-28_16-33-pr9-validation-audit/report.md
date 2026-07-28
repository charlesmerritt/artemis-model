# ML Experiment: PR #9 fold-local validation audit

- Date: 2026-07-28 16:33 EDT
- Prompt: Resolve the PR review finding that Stage A was fitted before spatial cross-validation.
- Repo commit before run: `3744026395e25e442abafd4d25265ef0b3a3ebc3`
- Status: completed
- Primary metric: balanced accuracy of the final two-stage decision over the held-out anchor proxy population
- Baseline: global Stage-A prefilter followed by conditional GroupKFold
- Device/runtime budget: CPU, under five minutes

## Hypothesis

The previously reported 0.8834 AUC is invalid as a held-out operational estimate
because the held-out blocks influenced Stage A's exemplars, threshold, and
survivor population. Fitting both stages within each spatial training fold will
separate the final two-stage decision metrics from the survivor-conditional
Stage-B diagnostic without changing the deployed all-anchor model.

## Data and method

The immutable input is the 4,500-point AlphaEarth table used by the production
run: 1,500 clearcut anchors, 1,500 non-forest anchors, and 1,500 apply points.
Its SHA-256 is recorded in `manifest.json`.

Three evaluations separate the review finding from the deployment mismatch:

1. Reproduce the prior global Stage-A prefilter and conditional Stage-B refit.
2. Refit Stage A inside each outer spatial fold while retaining the prior
   conditional-refit and two-anchor-year evaluation design.
3. Match deployment: fit Stage A and the all-anchor Stage B only on each training
   fold, then score each held-out anchor once at the operational 2022 feature
   year. Evaluate the final gated decision over all 3,000 anchors; separately
   compute Stage-B AUC only among fold-local Stage-A survivors.

## Commands

```bash
uv run python experiments/2026-07-28_16-33-pr9-validation-audit/experiment.py
```

## Results

| condition | metric | value | evaluation rows |
|---|---|---:|---:|
| prior global prefilter | conditional AUC | 0.883385 | 3,048 duplicated anchor-year rows |
| fold-local Stage A, conditional refit | conditional AUC | 0.888651 | 2,936 duplicated anchor-year rows |
| fold-local, deployment-matched Stage B | survivor-conditional AUC | 0.911780 | 1,468 unique 2022 survivors |
| **fold-local final two-stage decision (anchor proxy)** | **balanced accuracy** | **0.903333** | **3,000 unique 2022 anchors** |

The leakage finding is structurally valid, but its measured effect is not an AUC
drop: the closest leakage-only comparison rises by 0.0053. The more important
correction is deployment fidelity. Stage B is deployed after fitting all anchors
and scores only the 2022 surface; the old 0.8834 number instead cross-validated a
conditional refit over both 2018 and 2022 rows. AUC 0.9118 describes Stage B only
after the gate and is not an end-to-end pipeline metric.

The final gated decision has sensitivity 0.8573, specificity 0.9493, precision
0.9442, F1 0.8987, and balanced accuracy 0.9033 (1,286 true positives, 214 false
negatives, 76 false positives, and 1,424 true negatives).

Under the corrected pipeline, 1,299 positive and 169 negative held-out anchors
reach Stage B. Fold-fitted Stage A retains 86.6% of held-out clearcut anchors and
11.27% of held-out non-forest anchors. The former is a fold-local, fit-leak-free
but post-selection pass-rate estimate; 90% remains only the in-training quantile
target.

Per-fold survivor AUC ranges from 0.8730 to 0.9501. Thresholds fitted without the
held-out blocks range from 0.903868 to 0.908091.

## Artifacts

| path | description | tracked |
|---|---|---|
| `config.json` | split, years, thresholds, clusters, and seed | yes |
| `metrics/metrics.json` | aggregate comparison | yes |
| `metrics/fold_metrics.csv` | thresholds, counts, AUC, and accuracy by fold | yes |
| `logs/run.txt` | concise run output | yes |
| `manifest.json` | command, input hash, versions, runtime, and base commit | yes |

## Caveats

- This is one deterministic five-fold spatial partition, not a confidence
  interval over alternate block grids.
- These are post-selection estimates: k = 6 and the age-referenced design were
  chosen during the full-data exploratory sweep. No formal selection rule was
  nested inside the outer folds, so an independent test set or nested design is
  required for an unbiased generalisation estimate.
- The conditional population changes by fold because Stage A is part of the
  evaluated pipeline. Its 1,468 rows should not be compared as though they were
  the same sample as the prior 3,048 duplicated rows.
- Anchor labels still derive from LANDFIRE 2024. This audit addresses spatial
  leakage and deployment fidelity, not label-product independence.

## Next experiment

If metric uncertainty becomes decision-critical, repeat the fold-local
evaluation over several shifted 0.25-degree block grids. Hyperparameter selection
must also be nested inside each outer split if an unbiased generalisation
estimate is needed. That is not needed to correct this PR's point estimate or to
preserve the unchanged deployed model.

## Reproducibility notes

Configuration is captured in `config.json`; exact input hash and package
versions are in `manifest.json`. The input is a gitignored generated artifact;
the manifest records both its hash and the pipeline commands that regenerate it.
