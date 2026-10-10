import asyncio
import time
from collections.abc import Awaitable, Callable


class TokenBucket:
    """Async token bucket. Callers queue fairly behind one lock.

    With capacity 1 and rate 1/s there is no burst: consecutive acquisitions
    are always at least 1/rate seconds apart, however many callers compete.
    """

    def __init__(
        self,
        rate: float = 1.0,
        capacity: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0 or capacity < 1:
            raise ValueError("rate must be > 0 and capacity >= 1")
        self._rate = rate
        self._capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = capacity
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)

    async def acquire(self) -> None:
        # Holding the lock while sleeping is deliberate: waiters line up
        # in FIFO order instead of stampeding when a token appears.
        async with self._lock:
            self._refill()
            if self._tokens < 1:
                await self._sleep((1 - self._tokens) / self._rate)
                self._refill()
            self._tokens -= 1
