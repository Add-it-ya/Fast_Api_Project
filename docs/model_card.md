# Model card: used car price prediction

Generated facts in this card come from `app/models/model_metadata.json`, which
`python -m training.train_model` writes alongside the artifact. `GET /model/info`
serves the same content at runtime.

## Overview

| | |
|---|---|
| Task | Regression — predict the selling price of a used car |
| Algorithm | `RandomForestRegressor` (50 trees, max depth 12) inside a scikit-learn `Pipeline` |
| Framework | scikit-learn 1.3.2 |
| Inputs | 12 features (7 numeric, 5 categorical) |
| Output | A single price, in Indian rupees |
| Version | 4 |

## Intended use

Indicative pricing for used cars in the Indian market, for exploration and as a
starting point for a human valuation.

**Not intended for** automated pricing decisions, lending or insurance
decisions, or any use where a wrong number causes financial harm to a person
without a human in the loop. It has not been evaluated for fairness, and the
training data carries no protected attributes to evaluate against.

## Training data

`data/car-details.csv`, sha256 recorded in the metadata. 6,926 rows after
de-duplication, split 80/20 with `random_state=42` — 5,540 training rows and
1,386 held out.

Columns `name`, `model` and `edition` are dropped before training: they are
high-cardinality free text that would let the model memorise individual
listings rather than learn from the features.

## Metrics

On the 1,386 held-out rows, scored once after the configuration was chosen,
against a baseline that predicts the training mean for every car. The
cross-validated estimates from model selection are in the last column:

| | model | mean baseline | 5-fold CV on training rows |
|---|---:|---:|---:|
| MAE | ₹71,407 | ₹275,313 | ₹81,127 ± ₹1,934 |
| RMSE | ₹122,920 | ₹468,791 | |
| R² | 0.93 | -0.00 | 0.89 |
| MAPE | 17.80% | 106.58% | 18.02% |

An R² of 0.93 means little on its own; the baseline shows what predicting the
average would have scored.

MAPE of about 18% is the figure to quote: a typical prediction is off by
roughly a fifth of the true price, and the test split and cross-validation
agree on it. That is useful as a starting point, not as a final number.

## Choosing the forest

The number of trees and the depth were chosen by `training/sweep.py`, which
never sees the 1,386 test rows. Each configuration is scored with 5-fold
cross-validation on the 5,540 training rows, and its compressed artifact size
is measured by fitting it once on all of them. The rule was fixed before
looking at the results: take the smallest artifact whose mean CV MAE is within
one standard error of the best. Differences smaller than that are inside the
noise between folds.

Selected rows of the 20 tried (all of them, with R² and MAPE, are in
[model_sweep.txt](model_sweep.txt)):

| trees | depth | CV MAE | ± 1 SE | artifact |
|---:|---:|---:|---:|---:|
| 200 | 18 | ₹80,273 | ₹1,553 | 15.7 MB |
| 200 | 14 | ₹80,324 | ₹1,678 | 10.3 MB |
| 100 | 12 | ₹80,787 | ₹1,784 | 3.3 MB |
| **50** | **12** | **₹81,127** | ₹1,934 | **1.7 MB** |
| 50 | 10 | ₹83,584 | ₹1,909 | 0.9 MB |
| 50 | 8 | ₹88,631 | ₹1,859 | 0.4 MB |

The best CV MAE was 200 trees at depth 18. Anything at or under ₹81,826 is
within one standard error of it. The smallest such artifact is 50 trees at
depth 12, nine times smaller. Depth matters much more than the number of trees
here: going from depth 12 to 8 costs ₹7,500, while going from 200 trees to 50
at the same depth costs ₹400.

The test split was then scored once, for the chosen configuration only. Its
MAE (₹71,407) is better than the CV estimate (₹81,127). That says this test
split is easier than the average fold, not that the model got better, so the
CV figure is the more conservative one to quote.

The previous version (v3, 100 trees at depth 12) was sized by scoring
configurations on the test rows themselves, which makes its reported error
optimistic. v4 replaces it, at half the size and within ₹41 on the same test
rows.

v4 also drops a `StandardScaler` that sat in front of the forest. Trees split
on thresholds, so rescaling a feature does not change what they learn.
Refitting the old 100/12 forest without it moved the test MAE by ₹28 (0.04%).

Artifacts are written with `joblib` compression level 3, which costs a fraction
of a second at startup and makes the file about 5x smaller.

## Serving

Training fits with `n_jobs=-1`, and the saved artifact keeps that setting.
`load_bundle` switches it to `n_jobs=1` at startup, because each request
predicts a single row. Median time per prediction on 12 cores:

| | n_jobs=-1 | n_jobs=1 | 8 at once, n_jobs=-1 | 8 at once, n_jobs=1 |
|---|---:|---:|---:|---:|
| v3, 100 trees | 30 ms | 4.3 ms | 38 /s | 73 /s |
| v4, 50 trees | 18 ms | 3.1 ms | 55 /s | 112 /s |

Raw output from three runs of each, made with `scripts/inference_benchmark.py`, is in
[benchmarks/inference/n_jobs.txt](../benchmarks/inference/n_jobs.txt).

## Limitations

- **Small dataset.** Under 7,000 rows for a 32-brand market. Rare brands have
  very few examples — 19 of 30 are under 1% of the training rows, some at 0.02%.
  Predictions for those are weakly supported.
- **Unknown categories return a confident-looking number.** The pipeline uses
  `OneHotEncoder(handle_unknown='ignore')`, so an unseen brand becomes an
  all-zero vector and the model still returns a price. The API rejects
  out-of-vocabulary categories with a 422 rather than letting that through, but
  the underlying model has no such guard.
- **No temporal validation.** The split is random, not chronological, so the
  metrics do not tell you how the model holds up as the market moves.
- **Prices are historical.** Nothing accounts for inflation or shifts in demand
  since the data was collected.
- **Point estimates only.** No prediction interval, so the API cannot say how
  confident it is about any individual car.

## Monitoring

- **Input drift** — `GET /model/drift` reports Population Stability Index per
  feature against the training distribution recorded at training time. Under 0.1
  is stable, 0.1–0.25 a moderate shift, above 0.25 significant. Exposed to
  Prometheus as `model_feature_drift_psi{feature=...}`.
- **Live accuracy** — `POST /predictions/{prediction_id}/actual` records a real sale price
  against a prediction; `GET /model/performance` reports error over recent
  scored predictions next to the training metrics. Exposed as `model_live_mae`.

Drift is the earlier signal of the two: input distributions shift immediately,
whereas accuracy cannot be measured until outcomes come back.

Drift is computed over the newest 2,000 predictions in the prediction log, so
every worker reports the same figure and it survives restarts. A background
task recomputes it every minute.

## Reproducing

```bash
python -m training.sweep         # model selection, about 3 minutes
python -m training.train_model   # fit the chosen configuration, score the test split
```

Deterministic given the same input file — the split, the forest and the
train/test boundary are all seeded. The metadata records the data hash, the
scikit-learn version and the Python version, so a mismatch is visible rather
than mysterious.

## Known operational hazard

The artifact is a pickle and is tied to the scikit-learn version that wrote it.
Loading this model under scikit-learn 1.9.0 raises
`AttributeError: 'SimpleImputer' object has no attribute '_fill_dtype'` — it does
not degrade quietly. The version is recorded in the metadata and checked at
startup, which logs a warning on mismatch, and `GET /model/info` exposes
`version_match` so the discrepancy is visible before it becomes an incident.
