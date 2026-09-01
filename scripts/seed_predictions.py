"""Seed the predictions table with synthetic rows for query benchmarking.

Loads via PostgreSQL COPY (asyncpg's copy_records_to_table) rather than
row-by-row INSERT. Pass --compare to also time an INSERT loop over a small
slice so the two are measured on the same hardware in the same run.

    python scripts/seed_predictions.py --rows 1000000
    python scripts/seed_predictions.py --rows 1000000 --compare
"""

import argparse
import asyncio
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_args
from urllib.parse import urlparse

import asyncpg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.schemas.prediction import (  # noqa: E402
    Company,
    Fuel,
    Owner,
    SellerType,
    Transmission,
)

COLUMNS = [
    'user_id',
    'company',
    'year',
    'owner',
    'fuel',
    'seller_type',
    'transmission',
    'km_driven',
    'mileage_mpg',
    'engine_cc',
    'max_power_bhp',
    'torque_nm',
    'seats',
    'predicted_price',
    'cache_hit',
    'created_at',
]

COMPANIES = list(get_args(Company))
OWNERS = list(get_args(Owner))
FUELS = list(get_args(Fuel))
SELLERS = list(get_args(SellerType))
TRANSMISSIONS = list(get_args(Transmission))

# Spread created_at across two years so an ORDER BY created_at DESC has real
# work to do rather than sorting a handful of identical timestamps.
WINDOW_DAYS = 730


def dsn_from_settings() -> str:
    return settings.DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://')


def generate(count: int, rng: random.Random, user_ids: list[int]):
    start = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    for _ in range(count):
        yield (
            rng.choice(user_ids),
            rng.choice(COMPANIES),
            rng.randint(1995, 2020),
            rng.choice(OWNERS),
            rng.choice(FUELS),
            rng.choice(SELLERS),
            rng.choice(TRANSMISSIONS),
            float(rng.randint(1_000, 300_000)),
            round(rng.uniform(10, 90), 1),
            float(rng.choice([800, 1000, 1200, 1500, 2000, 2500])),
            round(rng.uniform(40, 250), 1),
            round(rng.uniform(60, 400), 1),
            float(rng.choice([4, 5, 7])),
            round(rng.uniform(50_000, 3_000_000), 2),
            rng.random() < 0.6,
            start + timedelta(seconds=rng.randrange(WINDOW_DAYS * 86_400)),
        )


async def ensure_users(conn: asyncpg.Connection, how_many: int = 5) -> list[int]:
    ids = [r['id'] for r in await conn.fetch('SELECT id FROM users ORDER BY id LIMIT $1', how_many)]
    if ids:
        return ids
    # A placeholder hash - these rows exist to satisfy the foreign key, and
    # nothing can authenticate as them.
    placeholder = '$2b$12$' + 'x' * 53
    for i in range(how_many):
        ids.append(
            await conn.fetchval(
                'INSERT INTO users (username, hashed_password) VALUES ($1, $2) RETURNING id',
                f'seed-user-{i}',
                placeholder,
            )
        )
    return ids


async def copy_load(conn, total: int, chunk: int, rng, user_ids) -> tuple[float, float]:
    """Returns (seconds spent in COPY, seconds end to end).

    Generating the synthetic rows in Python is a meaningful share of the wall
    time and says nothing about the database, so it is timed separately.
    """
    loaded = 0
    copy_seconds = 0.0
    started = time.perf_counter()
    while loaded < total:
        size = min(chunk, total - loaded)
        records = list(generate(size, rng, user_ids))

        copy_started = time.perf_counter()
        await conn.copy_records_to_table('predictions', records=records, columns=COLUMNS)
        copy_seconds += time.perf_counter() - copy_started

        loaded += size
        print(f'  {loaded:,} / {total:,}', end='\r', flush=True)
    print(f'  {loaded:,} / {total:,}')
    return copy_seconds, time.perf_counter() - started


async def insert_load(conn, total: int, rng, user_ids) -> float:
    placeholders = ', '.join(f'${i + 1}' for i in range(len(COLUMNS)))
    statement = f'INSERT INTO predictions ({", ".join(COLUMNS)}) VALUES ({placeholders})'
    records = list(generate(total, rng, user_ids))
    started = time.perf_counter()
    for record in records:
        await conn.execute(statement, *record)
    return time.perf_counter() - started


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', type=int, default=1_000_000)
    parser.add_argument('--chunk', type=int, default=50_000)
    parser.add_argument('--compare', action='store_true', help='also time row-by-row INSERT')
    parser.add_argument('--compare-rows', type=int, default=10_000)
    parser.add_argument('--truncate', action='store_true', help='empty the table first')
    args = parser.parse_args()

    rng = random.Random(7)
    parsed = urlparse(dsn_from_settings())
    conn = await asyncpg.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port,
        database=parsed.path.lstrip('/'),
    )

    try:
        if args.truncate:
            await conn.execute('TRUNCATE predictions RESTART IDENTITY')
            print('truncated predictions')

        user_ids = await ensure_users(conn)

        if args.compare:
            print(f'\nrow-by-row INSERT of {args.compare_rows:,} rows...')
            insert_seconds = await insert_load(conn, args.compare_rows, rng, user_ids)
            insert_rps = args.compare_rows / insert_seconds
            print(f'  {insert_seconds:.2f}s -> {insert_rps:,.0f} rows/sec')

        print(f'\nCOPY of {args.rows:,} rows in chunks of {args.chunk:,}...')
        copy_seconds, wall_seconds = await copy_load(conn, args.rows, args.chunk, rng, user_ids)
        copy_rps = args.rows / copy_seconds
        wall_rps = args.rows / wall_seconds
        print(f'  {copy_seconds:.2f}s in COPY      -> {copy_rps:,.0f} rows/sec')
        print(f'  {wall_seconds:.2f}s end to end   -> {wall_rps:,.0f} rows/sec (incl. generation)')

        if args.compare:
            print(f'\nCOPY is {copy_rps / insert_rps:.0f}x faster than row-by-row INSERT')

        total = await conn.fetchval('SELECT count(*) FROM predictions')
        size = await conn.fetchval("SELECT pg_size_pretty(pg_total_relation_size('predictions'))")
        heap = await conn.fetchval("SELECT pg_size_pretty(pg_relation_size('predictions'))")
        print(f'\npredictions: {total:,} rows, {size} total ({heap} heap)')
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
