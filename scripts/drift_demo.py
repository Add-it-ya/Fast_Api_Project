"""Show the drift detector responding to a shift in traffic.

Sends a batch of requests drawn from the model's own training distribution,
reports PSI, then sends a batch skewed towards newer low-mileage luxury cars
and reports PSI again. The first batch should read stable, the second should
not.

Run the API with WEB_CONCURRENCY=1 for this: each worker keeps its own window,
so with several workers a single client sees whichever one answers.

    BASE_URL=http://localhost:8000 python scripts/drift_demo.py
"""

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

METADATA_PATH = REPO_ROOT / 'app' / 'models' / 'model_metadata.json'

BASE_URL = os.getenv('BASE_URL', 'http://localhost:8000')
API_KEY = os.getenv('API_KEY', 'local-dev-api-key')
USERNAME = os.getenv('DRIFT_DEMO_USER', 'drift-demo')
PASSWORD = os.getenv('DRIFT_DEMO_PASSWORD', 'drift-demo-password')

LUXURY = ['BMW', 'Audi', 'Mercedes-Benz', 'Jaguar', 'Volvo', 'Lexus']

# Near-discrete numeric features are recorded as categorical in the training
# distribution, so their sampled values come back as strings.
NUMERIC_CASTS = {
    'year': int,
    'seats': float,
    'km_driven': float,
    'mileage_mpg': float,
    'engine_cc': float,
    'max_power_bhp': float,
    'torque_nm': float,
}


def sample_in_distribution(reference: dict, rng: random.Random) -> dict:
    """Draw a request that looks like the training data."""
    row = {}
    for column, proportions in reference['categorical'].items():
        categories = list(proportions)
        weights = [proportions[c] for c in categories]
        value = rng.choices(categories, weights=weights, k=1)[0]
        row[column] = NUMERIC_CASTS[column](float(value)) if column in NUMERIC_CASTS else value

    for column, spec in reference['numeric'].items():
        edges, props = spec['edges'], spec['proportions']
        index = rng.choices(range(len(props)), weights=props, k=1)[0]
        value = rng.uniform(edges[index], edges[index + 1])
        row[column] = int(round(value)) if column in {'year', 'seats'} else round(value, 1)

    row['year'] = max(1980, min(2026, int(row['year'])))
    row['seats'] = float(max(1, min(20, int(row['seats']))))
    row['km_driven'] = max(1.0, float(row['km_driven']))
    return row


def sample_shifted(rng: random.Random) -> dict:
    """Newer, barely-driven, high-powered luxury cars: a plausible shift if the
    service were pointed at a premium marketplace."""
    return {
        'company': rng.choice(LUXURY),
        'year': rng.randint(2018, 2020),
        'owner': 'First',
        'fuel': 'Diesel',
        'seller_type': 'Dealer',
        'transmission': 'Automatic',
        'km_driven': float(rng.randint(1_000, 20_000)),
        'mileage_mpg': round(rng.uniform(12, 22), 1),
        'engine_cc': float(rng.choice([2000, 2500, 3000])),
        'max_power_bhp': round(rng.uniform(180, 250), 1),
        'torque_nm': round(rng.uniform(300, 400), 1),
        'seats': 5.0,
    }


def render(label: str, report: dict) -> None:
    print(f'\n{label}')
    print(
        f'  window {report["window_samples"]} samples, worst PSI '
        f'{report["worst_psi"]} ({report["status"]})'
    )
    if not report['features']:
        print('  (not enough samples yet)')
        return
    for entry in report['features'][:6]:
        bar = '#' * min(40, int(entry['psi'] * 40))
        print(f'  {entry["feature"]:<16}{entry["psi"]:>8.3f}  {entry["status"]:<12}{bar}')


async def send(client, headers, payloads) -> int:
    sent = 0
    for payload in payloads:
        response = await client.post('/predict', json=payload, headers=headers)
        if response.status_code == 200:
            sent += 1
        elif response.status_code == 422:
            continue
        else:
            raise SystemExit(f'{response.status_code}: {response.text}')
    return sent


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=int, default=150)
    args = parser.parse_args()

    rng = random.Random(11)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        credentials = {'username': USERNAME, 'password': PASSWORD}
        await client.post('/register', json=credentials)
        login = await client.post('/login', json=credentials)
        if login.status_code != 200:
            raise SystemExit(f'login failed: {login.status_code} {login.text}')
        headers = {
            'Authorization': f'Bearer {login.json()["access_token"]}',
            'api-key': API_KEY,
        }

        info = await client.get('/model/info')
        if info.status_code != 200:
            raise SystemExit('no model metadata; run python -m training.train_model first')
        print(f'model v{info.json()["version"]}, trained {info.json()["trained_at"][:10]}')

        if not (await client.get('/model/drift')).json()['configured']:
            raise SystemExit('drift reference not configured on the server')

        # Read the training distribution locally rather than over the API - it
        # is bulky and closer to the training data than /model/info should
        # expose.
        if not METADATA_PATH.exists():
            raise SystemExit(f'{METADATA_PATH} not found; run python -m training.train_model')
        reference = json.loads(METADATA_PATH.read_text())['training_distribution']

        in_dist = [sample_in_distribution(reference, rng) for _ in range(args.batch)]
        print(f'\nsending {args.batch} requests drawn from the training distribution...')
        await send(client, headers, in_dist)
        render('after in-distribution traffic:', (await client.get('/model/drift?recompute=true')).json())

        shifted = [sample_shifted(rng) for _ in range(args.batch * 4)]
        print(f'\nsending {len(shifted)} requests skewed to newer luxury cars...')
        await send(client, headers, shifted)
        render('after shifted traffic:', (await client.get('/model/drift?recompute=true')).json())


if __name__ == '__main__':
    asyncio.run(main())
