"""Choose the forest's size by cross-validation on the training split.

The held-out test rows are never touched here. Each configuration is scored
with 5-fold cross-validation on the training rows, and its compressed artifact
size is measured by fitting it once on all of them.

The pick is the smallest artifact whose mean CV MAE is within one standard
error of the best configuration's. Differences smaller than that are inside
the noise of the folds, so they are not worth a bigger file.

    python -m training.sweep
"""

import io
import math
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.model_selection import KFold, cross_validate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training.train_model import COMPRESS_LEVEL, build_pipeline, load_split  # noqa: E402

TREES = [50, 100, 150, 200]
DEPTHS = [8, 10, 12, 14, 18]
FOLDS = 5


def artifact_megabytes(model) -> float:
    buffer = io.BytesIO()
    joblib.dump(model, buffer, compress=COMPRESS_LEVEL)
    return buffer.tell() / 1e6


def main() -> None:
    x_train, _, y_train, _ = load_split()
    numeric = x_train.select_dtypes(include='number').columns.tolist()
    categorical = [c for c in x_train.columns if c not in numeric]
    folds = KFold(n_splits=FOLDS, shuffle=True, random_state=42)

    print(f'{FOLDS}-fold CV on {len(x_train):,} training rows; the test split is not used\n')
    results = []
    for trees in TREES:
        for depth in DEPTHS:
            params = {'n_estimators': trees, 'max_depth': depth, 'random_state': 42, 'n_jobs': -1}
            scores = cross_validate(
                build_pipeline(numeric, categorical, params),
                x_train,
                y_train,
                cv=folds,
                scoring=('neg_mean_absolute_error', 'r2', 'neg_mean_absolute_percentage_error'),
            )
            mae = -scores['test_neg_mean_absolute_error']
            size = artifact_megabytes(build_pipeline(numeric, categorical, params).fit(x_train, y_train))
            results.append(
                {
                    'trees': trees,
                    'depth': depth,
                    'mae': float(mae.mean()),
                    'mae_se': float(mae.std(ddof=1) / math.sqrt(FOLDS)),
                    'r2': float(np.mean(scores['test_r2'])),
                    'mape': float(-np.mean(scores['test_neg_mean_absolute_percentage_error']) * 100),
                    'mb': size,
                }
            )
            row = results[-1]
            print(
                f'  {trees:>3} trees, depth {depth:>2}: MAE {row["mae"]:,.0f} +/- {row["mae_se"]:,.0f}',
                flush=True,
            )

    best = min(results, key=lambda r: r['mae'])
    threshold = best['mae'] + best['mae_se']
    pick = min((r for r in results if r['mae'] <= threshold), key=lambda r: r['mb'])

    print('\n| trees | depth | CV MAE | +/- 1 SE | CV R² | CV MAPE | artifact |')
    print('|---:|---:|---:|---:|---:|---:|---:|')
    for r in sorted(results, key=lambda r: (r['trees'], r['depth'])):
        mark = '**' if r is pick else ''
        print(
            f'| {mark}{r["trees"]}{mark} | {mark}{r["depth"]}{mark} | {mark}₹{r["mae"]:,.0f}{mark} '
            f'| ₹{r["mae_se"]:,.0f} | {r["r2"]:.3f} | {r["mape"]:.2f}% | {r["mb"]:.1f} MB |'
        )

    print(f'\nbest CV MAE: {best["trees"]} trees, depth {best["depth"]}: ', end='')
    print(f'₹{best["mae"]:,.0f} +/- ₹{best["mae_se"]:,.0f}')
    print(f'within one SE (<= ₹{threshold:,.0f}), smallest artifact: ', end='')
    print(f'{pick["trees"]} trees, depth {pick["depth"]}')


if __name__ == '__main__':
    main()
