import asyncio

from prometheus_client import REGISTRY
from sqlalchemy import text

from app.db.repositories import PredictionRepository
from app.db.session import engine
from app.services.prediction_writer import PredictionWriter


def _row(user_id: int, valid_car: dict, price: float = 500000.0, cache_hit: bool = False) -> dict:
    return {**valid_car, 'user_id': user_id, 'predicted_price': price, 'cache_hit': cache_hit}


async def _count() -> int:
    async with engine.connect() as conn:
        return await conn.scalar(text('SELECT count(*) FROM predictions'))


def _dropped(reason: str) -> float:
    return REGISTRY.get_sample_value('prediction_log_rows_dropped_total', {'reason': reason}) or 0.0


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
    before = _dropped('queue_full')
    w = PredictionWriter(max_queue=5)
    for i in range(20):
        w.enqueue(_row(1, valid_car | {'km_driven': i}))

    assert w.dropped == 15
    assert _dropped('queue_full') == before + 15


async def test_one_rejected_row_does_not_lose_its_batch(client, token, valid_car):
    """A row whose user was deleted after the token was issued breaks the
    foreign key. The database rejects the whole statement; the other rows in
    the batch did nothing wrong and must still be written."""
    before = _dropped('rejected')
    w = PredictionWriter()
    for i in range(5):
        w.enqueue(_row(1, valid_car | {'km_driven': 1000 + i}))
        if i == 2:
            w.enqueue(_row(999_999, valid_car))

    await w.flush_now()

    assert await _count() == 5
    assert w.written == 5
    assert w.dropped == 1
    assert _dropped('rejected') == before + 1


async def test_rows_lost_to_a_failed_write_are_counted(valid_car, monkeypatch):
    """Losing rows silently is what the shedding alert exists to prevent."""

    async def unreachable(self, rows):
        raise ConnectionRefusedError('database unreachable')

    monkeypatch.setattr(PredictionRepository, 'log_many', unreachable)
    before = _dropped('write_failed')
    w = PredictionWriter()
    for i in range(3):
        w.enqueue(_row(1, valid_car | {'km_driven': 1000 + i}))

    await w.flush_now()

    assert w.written == 0
    assert w.dropped == 3
    assert _dropped('write_failed') == before + 3


async def test_flushing_an_empty_queue_is_a_no_op():
    w = PredictionWriter()
    await w.flush_now()
    assert w.written == 0


async def test_stop_is_safe_when_never_started():
    await PredictionWriter().stop()
