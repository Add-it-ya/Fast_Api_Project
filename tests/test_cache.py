from app.cache.redis_cache import (
    build_cache_key,
    get_cached_prediction,
    get_redis,
    set_cached_prediction,
)


def test_key_is_independent_of_field_order(valid_car):
    reversed_order = dict(reversed(list(valid_car.items())))
    assert build_cache_key(valid_car) == build_cache_key(reversed_order)


def test_different_features_produce_different_keys(valid_car):
    other = valid_car | {'km_driven': valid_car['km_driven'] + 1}
    assert build_cache_key(valid_car) != build_cache_key(other)


async def test_round_trip_preserves_the_value():
    await set_cached_prediction('prediction:test', 531520.94)
    assert await get_cached_prediction('prediction:test') == 531520.94


async def test_missing_key_returns_none():
    assert await get_cached_prediction('prediction:absent') is None


async def test_corrupt_cache_entry_is_ignored_not_executed():
    # The old implementation called eval() on this value.
    await get_redis().set('prediction:evil', '__import__("os").system("touch /tmp/pwned")')
    assert await get_cached_prediction('prediction:evil') is None
