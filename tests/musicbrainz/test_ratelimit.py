import asyncio

import pytest

from app.musicbrainz.ratelimit import TokenBucket
from tests.musicbrainz.fakes import FakeClock

pytestmark = pytest.mark.anyio


def _bucket(clock: FakeClock) -> TokenBucket:
    return TokenBucket(rate=1.0, capacity=1.0, clock=clock.time, sleep=clock.sleep)


async def test_first_acquire_is_immediate() -> None:
    clock = FakeClock()
    await _bucket(clock).acquire()
    assert clock.now == 0.0


async def test_concurrent_callers_are_spaced_one_second_apart() -> None:
    clock = FakeClock()
    bucket = _bucket(clock)
    stamps: list[float] = []

    async def caller() -> None:
        await bucket.acquire()
        stamps.append(clock.now)

    await asyncio.gather(*(caller() for _ in range(4)))

    assert stamps == [0.0, 1.0, 2.0, 3.0]


async def test_idle_time_does_not_allow_a_burst() -> None:
    clock = FakeClock()
    bucket = _bucket(clock)
    await bucket.acquire()
    clock.now += 60  # long idle: capacity is 1, so no stored-up burst
    stamps: list[float] = []
    for _ in range(2):
        await bucket.acquire()
        stamps.append(clock.now)

    assert stamps == [60.0, 61.0]


def test_invalid_configuration_rejected() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=0)
