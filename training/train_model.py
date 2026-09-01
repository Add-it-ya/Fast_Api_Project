"""Train the car price model and record what it was trained on.

Emits three things beside the fitted pipeline:

* validation metrics on a held-out split, next to a predict-the-mean baseline
  so the numbers mean something;
* the scikit-learn version the artifact was pickled with, because loading it
  under a different one raises rather than degrading;
* the training feature distribution, which app/services/drift.py compares live
  traffic against.

    python -m training.train_model
"""

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training.train_utils import (  # noqa: E402
    DATA_FILE_PATH,
    DROPPED_COLUMNS,
    METADATA_PATH,
    MODEL_DIR,
    MODEL_PATH,
    PSI_BINS,
    TARGET,
    file_sha256,
)

# Sized against a measured accuracy/artifact-size curve on this 5,540-row
# training set. 200 trees at depth 18 produced a 16 MB artifact with no better
# error than this: MAE 71,466 there against 71,366 here. Past roughly this
# point the forest is memorising, not learning, and only the file grows.
HYPERPARAMETERS = {'n_estimators': 100, 'max_depth': 12, 'random_state': 42, 'n_jobs': -1}

# Trades a little load time for a much smaller artifact - roughly 5x on this
# forest. The model is committed to the repository, so its size is a real cost.
COMPRESS_LEVEL = 3


def build_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    numeric_steps = Pipeline(
        steps=[('imputer', SimpleImputer(strategy='median')), ('scaler', StandardScaler())]
    )
    categorical_steps = Pipeline(
        steps=[
            ('imputer', SimpleImputer(strategy='constant', fill_value='missing')),
            ('encoder', OneHotEncoder(handle_unknown='ignore', sparse_output=False)),
        ]
    )
    preprocessor = ColumnTransformer(
        transformers=[('num', numeric_steps, numeric), ('cat', categorical_steps, categorical)]
    )
    return Pipeline(steps=[('pre', preprocessor), ('reg', RandomForestRegressor(**HYPERPARAMETERS))])


def score(y_true, y_pred) -> dict:
    return {
        'mae': round(float(mean_absolute_error(y_true, y_pred)), 2),
        'rmse': round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 2),
        'r2': round(float(r2_score(y_true, y_pred)), 4),
        'mape_pct': round(float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100), 2),
    }


def training_distribution(frame: pd.DataFrame, numeric: list[str], categorical: list[str]) -> dict:
    """Reference distribution for drift detection.

    Numeric features get quantile bin edges plus the proportion of training rows
    in each bin; categoricals get category proportions. Both are what the PSI
    calculation at serving time expects.
    """
    numeric_reference = {}
    discrete: list[str] = []

    for column in numeric:
        values = frame[column].dropna()
        # Quantile bins are meaningless for a feature with only a handful of
        # distinct values - they collapse onto each other and pile most of the
        # mass into one bin. Treat those as discrete instead.
        if values.nunique() <= PSI_BINS:
            discrete.append(column)
            continue

        quantiles = np.linspace(0, 1, PSI_BINS + 1)
        edges = np.unique(np.quantile(values, quantiles))
        counts, _ = np.histogram(values, bins=edges)
        total = counts.sum()
        numeric_reference[column] = {
            'edges': [float(e) for e in edges],
            'proportions': [float(c / total) for c in counts],
            'mean': float(values.mean()),
            'std': float(values.std()),
        }

    categorical_reference = {}
    for column in list(categorical) + discrete:
        proportions = frame[column].value_counts(normalize=True)
        categorical_reference[column] = {str(k): float(v) for k, v in proportions.items()}

    if discrete:
        print(f'treating as discrete for drift purposes: {", ".join(discrete)}')

    return {'numeric': numeric_reference, 'categorical': categorical_reference}


def next_version() -> int:
    path = Path(METADATA_PATH)
    if not path.exists():
        return 1
    try:
        return int(json.loads(path.read_text()).get('version', 0)) + 1
    except (ValueError, json.JSONDecodeError):
        return 1


def main() -> None:
    frame = pd.read_csv(DATA_FILE_PATH).drop_duplicates().drop(columns=DROPPED_COLUMNS)

    features = frame.drop(columns=TARGET)
    target = frame[TARGET].copy()

    x_train, x_test, y_train, y_test = train_test_split(features, target, test_size=0.2, random_state=42)

    numeric = x_train.select_dtypes(include='number').columns.tolist()
    categorical = [c for c in x_train.columns if c not in numeric]

    print(f'training on {len(x_train):,} rows, holding out {len(x_test):,}')
    model = build_pipeline(numeric, categorical)
    model.fit(x_train, y_train)

    metrics = score(y_test, model.predict(x_test))
    # Predicting the training mean for every row. Without this, an r2 means
    # very little to anyone reading it.
    baseline = score(y_test, np.full(len(y_test), y_train.mean()))

    version = next_version()
    metadata = {
        'version': version,
        'trained_at': datetime.now(timezone.utc).isoformat(),
        'model_type': type(model.named_steps['reg']).__name__,
        'hyperparameters': HYPERPARAMETERS,
        'sklearn_version': sklearn.__version__,
        'python_version': platform.python_version(),
        'data': {
            'file': DATA_FILE_PATH,
            'sha256': file_sha256(DATA_FILE_PATH),
            'rows_total': int(len(frame)),
            'rows_train': int(len(x_train)),
            'rows_test': int(len(x_test)),
        },
        'features': {'numeric': numeric, 'categorical': categorical},
        'metrics': metrics,
        'baseline_metrics': baseline,
        'training_distribution': training_distribution(x_train, numeric, categorical),
    }

    Path(MODEL_DIR).mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH, compress=COMPRESS_LEVEL)
    metadata['artifact_bytes'] = Path(MODEL_PATH).stat().st_size
    Path(METADATA_PATH).write_text(json.dumps(metadata, indent=2) + '\n')

    print(f'\nmodel v{version} -> {MODEL_PATH} ({metadata["artifact_bytes"] / 1e6:.1f} MB)')
    print(f'metadata        -> {METADATA_PATH}')
    print(f'sklearn         {sklearn.__version__}')
    print(f'\n{"":16}{"model":>14}{"mean baseline":>16}')
    for key in ('mae', 'rmse', 'r2', 'mape_pct'):
        print(f'{key:16}{metrics[key]:>14,.2f}{baseline[key]:>16,.2f}')


if __name__ == '__main__':
    main()
