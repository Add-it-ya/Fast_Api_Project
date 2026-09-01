"""Load test for POST /predict.

Logs in once, warms the cache, then fires TOTAL_REQUESTS across CONCURRENCY
workers and reports p50/p95/p99/max.

Cache hits and cache misses are reported separately. A run made entirely of
repeated feature vectors measures Redis, not the model, and would produce a
p95 that says nothing about the inference path.

    CONCURRENCY=100 TOTAL_REQUESTS=1000 python scripts/load_test.py
"""

import asyncio
import json
import math
import os
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.schemas.prediction import (  # noqa: E402
    Company,
    Fuel,
    Owner,
    SellerType,
    Transmission,
)

BASE_URL = os.getenv('BASE_URL', 'http://localhost:8000')
API_KEY = os.getenv('API_KEY', 'local-dev-api-key')
CONCURRENCY = int(os.getenv('CONCURRENCY', '100'))
TOTAL_REQUESTS = int(os.getenv('TOTAL_REQUESTS', '1000'))
# Size of the pool of distinct feature vectors. Smaller means more cache hits.
UNIQUE_VECTORS = int(os.getenv('UNIQUE_VECTORS', '200'))
USERNAME = os.getenv('LOAD_TEST_USER', 'loadtester')
PASSWORD = os.getenv('LOAD_TEST_PASSWORD', 'load-test-password')
# Fresh vectors per run by default, so a repeat run is not silently measuring a
# cache Redis warmed by the previous one. Pin SEED to reproduce a run exactly.
SEED = int(os.getenv('SEED', str(random.randrange(2**31))))
RESULTS_DIR = REPO_ROOT / 'benchmarks'


def build_vector(rng: random.Random) -> dict:
    return {
        'company': rng.choice(get_args(Company)),
        'year': rng.randint(1995, 2020),
        'owner': rng.choice(get_args(Owner)),
        'fuel': rng.choice(get_args(Fuel)),
        'seller_type': rng.choice(get_args(SellerType)),
        'transmission': rng.choice(get_args(Transmission)),
        'km_driven': rng.randint(1_000, 300_000),
        'mileage_mpg': round(rng.uniform(10, 90), 1),
        'engine_cc': rng.choice([800, 1000, 1200, 1500, 2000, 2500]),
        'max_power_bhp': round(rng.uniform(40, 250), 1),
        'torque_nm': round(rng.uniform(60, 400), 1),
        'seats': rng.choice([4, 5, 7]),
    }


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def summarise(latencies: list[float]) -> dict:
    if not latencies:
        return {'count': 0}
    return {
        'count': len(latencies),
        'mean_ms': round(statistics.fmean(latencies), 2),
        'p50_ms': round(percentile(latencies, 50), 2),
        'p95_ms': round(percentile(latencies, 95), 2),
        'p99_ms': round(percentile(latencies, 99), 2),
        'max_ms': round(max(latencies), 2),
    }


async def authenticate(client: httpx.AsyncClient) -> str:
    credentials = {'username': USERNAME, 'password': PASSWORD}
    register = await client.post('/register', json=credentials)
    if register.status_code not in (201, 409):
        raise SystemExit(f'Could not register load-test user: {register.status_code} {register.text}')

    login = await client.post('/login', json=credentials)
    if login.status_code != 200:
        raise SystemExit(f'Could not log in: {login.status_code} {login.text}')
    return login.json()['access_token']


async def fire(client, semaphore, headers, payload) -> tuple[float, int, bool]:
    async with semaphore:
        started = time.perf_counter()
        response = await client.post('/predict', json=payload, headers=headers)
        elapsed_ms = (time.perf_counter() - started) * 1000

    cached = False
    if response.status_code == 200:
        cached = response.json().get('cached', False)
    return elapsed_ms, response.status_code, cached


async def main() -> None:
    rng = random.Random(SEED)
    pool = [build_vector(rng) for _ in range(UNIQUE_VECTORS)]
    payloads = [pool[rng.randrange(UNIQUE_VECTORS)] for _ in range(TOTAL_REQUESTS)]

    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0, limits=limits) as client:
        token = await authenticate(client)
        headers = {'Authorization': f'Bearer {token}', 'api-key': API_KEY}

        warmup = await client.post('/predict', json=pool[0], headers=headers)
        if warmup.status_code != 200:
            raise SystemExit(f'Warm-up request failed: {warmup.status_code} {warmup.text}')

        semaphore = asyncio.Semaphore(CONCURRENCY)
        started = time.perf_counter()
        results = await asyncio.gather(*[fire(client, semaphore, headers, p) for p in payloads])
        wall_seconds = time.perf_counter() - started

    ok = [(ms, cached) for ms, status, cached in results if status == 200]
    failures: dict[int, int] = {}
    for _, status, _ in results:
        if status != 200:
            failures[status] = failures.get(status, 0) + 1

    all_latencies = [ms for ms, _ in ok]
    miss_latencies = [ms for ms, cached in ok if not cached]
    hit_latencies = [ms for ms, cached in ok if cached]

    report = {
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'config': {
            'base_url': BASE_URL,
            'concurrency': CONCURRENCY,
            'total_requests': TOTAL_REQUESTS,
            'unique_vectors': UNIQUE_VECTORS,
            'seed': SEED,
        },
        'wall_seconds': round(wall_seconds, 3),
        'throughput_rps': round(len(results) / wall_seconds, 1),
        'successful': len(ok),
        'failed': len(results) - len(ok),
        'failures_by_status': failures,
        'cache_hit_ratio': round(len(hit_latencies) / len(ok), 3) if ok else 0.0,
        'overall': summarise(all_latencies),
        'cache_miss': summarise(miss_latencies),
        'cache_hit': summarise(hit_latencies),
    }

    if failures.get(429):
        print(
            'WARNING: requests were rate limited. Raise RATE_LIMIT_REQUESTS on the '
            'API so the benchmark measures the prediction path, not the limiter.\n'
        )

    print(f'{TOTAL_REQUESTS} requests at concurrency {CONCURRENCY} against {BASE_URL}')
    print(f'wall time {wall_seconds:.2f}s   throughput {report["throughput_rps"]} req/s')
    print(
        f'succeeded {len(ok)}   failed {report["failed"]}   '
        f'cache hit ratio {report["cache_hit_ratio"]:.1%}'
    )
    if failures:
        print(f'failures by status: {failures}')
    print()
    header = f'{"":<12}{"count":>7}{"mean":>9}{"p50":>9}{"p95":>9}{"p99":>9}{"max":>9}'
    print(header)
    print('-' * len(header))
    for label, stats in (
        ('overall', report['overall']),
        ('cache miss', report['cache_miss']),
        ('cache hit', report['cache_hit']),
    ):
        if stats.get('count'):
            print(
                f'{label:<12}{stats["count"]:>7}{stats["mean_ms"]:>9.2f}{stats["p50_ms"]:>9.2f}'
                f'{stats["p95_ms"]:>9.2f}{stats["p99_ms"]:>9.2f}{stats["max_ms"]:>9.2f}'
            )
    print('\nall figures in milliseconds')

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = RESULTS_DIR / f'loadtest-c{CONCURRENCY}-n{TOTAL_REQUESTS}-{stamp}.json'
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(f'\nwrote {out.relative_to(REPO_ROOT)}')


if __name__ == '__main__':
    asyncio.run(main())
