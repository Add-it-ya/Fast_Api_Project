"""Time single-row predictions with the artifact's n_jobs against n_jobs=1.

Sequential calls show the cost of one prediction. Eight threads at once stand
in for the asyncio.to_thread pool under load.

    python scripts/inference_benchmark.py
"""

import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import joblib
import pandas as pd

MODEL_PATH = Path(__file__).resolve().parent.parent / 'app' / 'models' / 'model.joblib'
CAR = {
    'company': 'Maruti',
    'year': 2015,
    'owner': 'Second',
    'fuel': 'Petrol',
    'seller_type': 'Individual',
    'transmission': 'Automatic',
    'km_driven': 200000,
    'mileage_mpg': 55,
    'engine_cc': 1250,
    'max_power_bhp': 80,
    'torque_nm': 200,
    'seats': 5,
}
CALLS = 400
THREADS = 8


def predict_once(model) -> float:
    frame = pd.DataFrame([CAR])
    started = time.perf_counter()
    model.predict(frame)
    return (time.perf_counter() - started) * 1000


def measure(model, label: str) -> None:
    for _ in range(20):
        predict_once(model)
    sequential = [predict_once(model) for _ in range(CALLS)]

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        started = time.perf_counter()
        concurrent = list(pool.map(lambda _: predict_once(model), range(CALLS)))
        wall = time.perf_counter() - started

    p95 = statistics.quantiles
    print(
        f'{label:10} one at a time: p50 {statistics.median(sequential):6.2f} ms, '
        f'p95 {p95(sequential, n=20)[18]:6.2f} ms | '
        f'{THREADS} threads: p50 {statistics.median(concurrent):6.2f} ms, '
        f'p95 {p95(concurrent, n=20)[18]:6.2f} ms, {CALLS / wall:4.0f} predictions/s'
    )


def main() -> None:
    model = joblib.load(MODEL_PATH)
    saved = model.named_steps['reg'].n_jobs
    print(f'{os.cpu_count()} cpus, artifact saved with n_jobs={saved}')
    measure(model, f'n_jobs={saved}')
    model.set_params(reg__n_jobs=1)
    measure(model, 'n_jobs=1')


if __name__ == '__main__':
    main()
