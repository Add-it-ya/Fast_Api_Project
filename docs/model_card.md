# Model card: used car price prediction

Generated facts in this card come from `app/models/model_metadata.json`, which
`python -m training.train_model` writes alongside the artifact. `GET /model/info`
serves the same content at runtime.

## Overview

| | |
|---|---|
| Task | Regression — predict the selling price of a used car |
| Algorithm | `RandomForestRegressor` (200 trees, max depth 18) inside a scikit-learn `Pipeline` |
| Framework | scikit-learn 1.3.2 |
| Inputs | 12 features (7 numeric, 5 categorical) |
| Output | A single price, in Indian rupees |
| Version | 2 |

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

On the 1,386 held-out rows, against a baseline that predicts the training mean
for every car:

| | model | mean baseline |
|---|---:|---:|
| MAE | ₹71,466 | ₹275,313 |
| RMSE | ₹122,607 | ₹468,791 |
| R² | 0.93 | -0.00 |
| MAPE | 17.96% | 106.58% |

The baseline is included deliberately. An R² of 0.93 means little on its own;
it means more next to what predicting the average would have achieved.

MAPE of ~18% is the honest headline: a typical prediction is off by roughly a
fifth of the true price. That is useful as a starting point and not useful as a
final number.

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
- **Live accuracy** — `POST /predictions/{id}/actual` records a real sale price
  against a prediction; `GET /model/performance` reports error over recent
  scored predictions next to the training metrics. Exposed as `model_live_mae`.

Drift is the earlier signal of the two: input distributions shift immediately,
whereas accuracy cannot be measured until outcomes come back.

Each API worker keeps its own drift window in memory, so with several workers
Prometheus sees one series per worker. Each is an independent estimate from its
own random share of traffic; alert on the maximum across them rather than
expecting a single number.

## Reproducing

```bash
python -m training.train_model
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
