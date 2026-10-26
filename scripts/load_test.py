"""Load test for POST /predict, or GET /health for the host's ceiling.

Logs in once, sends one warm-up request, then fires TOTAL_REQUESTS across
CONCURRENCY concurrent clients and reports p50/p95/p99/max.

Cache hits and cache misses are reported separately. A run made entirely of
repeated feature vectors measures Redis, not the model, and would produce a
p95 that says nothing about the inference path. UNIQUE_VECTORS=0 makes every
request a different car, so every prediction is a model call.

TARGET=health fires GET /health instead: no authentication, database or
model. It measures what the server and this host can do at all, which bounds
every /predict figure.

The client cannot see how many workers the server runs, so set
SERVER_WORKERS (and GIT_SHA) to have them recorded in the result file next to
the model version the API reports.

    CONCURRENCY=100 TOTAL_REQUESTS=1000 python scripts/load_test.py
    UNIQUE_VECTORS=0 SERVER_WORKERS=4 python scripts/load_test.py
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
CONCURRENCY = int(os.getenv('CONCURRENCY', '100'))
TOTAL_REQUESTS = int(os.getenv('TOTAL_REQUESTS', '1000'))
# Size of the pool of distinct feature vectors. Smaller means more cache hits;
# the hit ratio a run reports is set here, not by anything about real traffic.
# 0 means a new vector for every request.
UNIQUE_VECTORS = int(os.getenv('UNIQUE_VECTORS', '200'))
TARGET = os.getenv('TARGET', 'predict')
SERVER_WORKERS = os.getenv('SERVER_WORKERS', 'unknown')
GIT_SHA = os.getenv('GIT_SHA', 'unknown')
USERNAME = os.getenv('LOAD_TEST_USER', 'loadtester')
PASSWORD = os.getenv('LOAD_TEST_PASSWORD', 'load-test-password')
# Fresh vectors per run by default, so a repeat run is not silently measuring a
# cache Redis warmed by the previous one. Pin SEED to reproduce a run exactly.
SEED = int(os.getenv('SEED', str(random.randrange(2**31))))
# Groups a set of runs, so results taken before and after a change stay
# distinguishable once they are committed.
LABEL = os.getenv('LABEL', 'adhoc')
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
        try:
            if payload is None:
                response = await client.get('/health')
            else:
                response = await client.post('/predict', json=payload, headers=headers)
        except httpx.TransportError:
            # A dropped or refused connection is a failed request, not a reason
            # to throw away the whole run. Recorded as status 0.
            return (time.perf_counter() - started) * 1000, 0, False
        elapsed_ms = (time.perf_counter() - started) * 1000

    cached = False
    if payload is not None and response.status_code == 200:
        cached = response.json().get('cached', False)
    return elapsed_ms, response.status_code, cached


def build_payloads(rng: random.Random) -> list[dict | None]:
    if TARGET == 'health':
        return [None] * TOTAL_REQUESTS
    if UNIQUE_VECTORS == 0:
        return [build_vector(rng) for _ in range(TOTAL_REQUESTS)]
    pool = [build_vector(rng) for _ in range(UNIQUE_VECTORS)]
    return [pool[rng.randrange(UNIQUE_VECTORS)] for _ in range(TOTAL_REQUESTS)]


async def main() -> None:
    if TARGET not in ('predict', 'health'):
        raise SystemExit(f'TARGET must be predict or health, not {TARGET!r}')
    rng = random.Random(SEED)
    payloads = build_payloads(rng)

    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0, limits=limits) as client:
        info = await client.get('/model/info')
        model_version = info.json().get('version') if info.status_code == 200 else None

        headers = {}
        if TARGET == 'predict':
            headers = {'Authorization': f'Bearer {await authenticate(client)}'}

        # One request before the clock starts, so connection setup and any
        # lazy initialisation on the server are not counted against the run.
        warmup_payload = None if TARGET == 'health' else build_vector(random.Random(0))
        warmup = await fire(client, asyncio.Semaphore(1), headers, warmup_payload)
        if warmup[1] != 200:
            raise SystemExit(f'Warm-up request failed with status {warmup[1]}')

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
            'target': TARGET,
            'base_url': BASE_URL,
            'server_workers': SERVER_WORKERS,
            'git_sha': GIT_SHA,
            'model_version': model_version,
            'concurrency': CONCURRENCY,
            'total_requests': TOTAL_REQUESTS,
            'unique_vectors': UNIQUE_VECTORS,
            'seed': SEED,
            'label': LABEL,
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

    print(f'{TOTAL_REQUESTS} {TARGET} requests at concurrency {CONCURRENCY} against {BASE_URL}')
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

    out_dir = RESULTS_DIR / LABEL
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = out_dir / f'{TARGET}-c{CONCURRENCY}-n{TOTAL_REQUESTS}-{stamp}.json'
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(f'\nwrote {out.relative_to(REPO_ROOT)}')


if __name__ == '__main__':
    asyncio.run(main())
