"""Small in-memory TTL cache for ephemeral API responses."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(
        self,
        ttl_seconds: float = 600.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._store: dict[Hashable, tuple[float, T]] = {}
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._store.clear()

    async def get_or_set(
        self, key: Hashable, factory: Callable[[], Awaitable[T]]
    ) -> T:
        async with self._lock:
            hit = self._store.get(key)
            if hit is not None:
                expires_at, value = hit
                if self._clock() < expires_at:
                    return value
                del self._store[key]

            value = await factory()
            self._store[key] = (self._clock() + self._ttl, value)
            return value
