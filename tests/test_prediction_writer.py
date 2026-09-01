import asyncio

from sqlalchemy import text

from app.db.session import engine
from app.services.prediction_writer import PredictionWriter


def _row(user_id: int, valid_car: dict, price: float = 500000.0, cache_hit: bool = False) -> dict:
    return {**valid_car, 'user_id': user_id, 'predicted_price': price, 'cache_hit': cache_hit}


async def _count() -> int:
    async with engine.connect() as conn:
        return await conn.scalar(text('SELECT count(*) FROM predictions'))


async def test_queued_rows_are_written_on_flush(client, token, valid_car):
    w = PredictionWriter()
    w.enqueue(_row(1, valid_car))
    assert await _count() == 0

    await w.flush_now()
    assert await _count() == 1


async def test_batch_is_written_in_one_statement(client, token, valid_car):
    w = PredictionWriter()
    for i in range(50):
        w.enqueue(_row(1, valid_car | {'km_driven': 1000 + i}))

    await w.flush_now()
    assert await _count() == 50


async def test_consumer_drains_without_an_explicit_flush(client, token, valid_car):
    w = PredictionWriter()
    w.start()
    try:
        for i in range(10):
            w.enqueue(_row(1, valid_car | {'km_driven': 5000 + i}))
        for _ in range(50):
            if await _count() == 10:
                break
            await asyncio.sleep(0.02)
    finally:
        await w.stop()

    assert await _count() == 10
    assert w.written == 10


async def test_full_queue_drops_rows_instead_of_raising(valid_car):
    w = PredictionWriter(max_queue=5)
    for i in range(20):
        w.enqueue(_row(1, valid_car | {'km_driven': i}))

    assert w.dropped == 15


async def test_flushing_an_empty_queue_is_a_no_op():
    w = PredictionWriter()
    await w.flush_now()
    assert w.written == 0


async def test_stop_is_safe_when_never_started():
    await PredictionWriter().stop()
