"""In-memory throttling of failed logins (per process; resets on restart)."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable


class LoginThrottle:
    """Sliding-window limiter on failed attempts.

    Three independent counters:
    - (ip, username): the tight limit that stops guessing one account from one place
    - ip: stops one machine from spraying many usernames
    - username: a high ceiling against distributed guessing of one account
    The tight limit is per (ip, username) so a stranger can't lock a victim out.
    """

    def __init__(
        self,
        *,
        window_seconds: float = 15 * 60,
        pair_limit: int = 5,
        ip_limit: int = 20,
        username_limit: int = 30,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._pair_limit = pair_limit
        self._ip_limit = ip_limit
        self._username_limit = username_limit
        self._max_keys = max_keys
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}

    def _limits(self, ip: str, username: str) -> list[tuple[str, int]]:
        name = username.strip().lower()[:100]
        return [
            (f"pair:{ip}|{name}", self._pair_limit),
            (f"ip:{ip}", self._ip_limit),
            (f"user:{name}", self._username_limit),
        ]

    def _live(self, key: str) -> deque[float] | None:
        attempts = self._failures.get(key)
        if attempts is None:
            return None
        cutoff = self._clock() - self._window
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
            return None
        return attempts

    def retry_after(self, ip: str, username: str) -> int:
        """Seconds until another attempt is allowed; 0 means allowed now."""
        wait = 0.0
        for key, limit in self._limits(ip, username):
            attempts = self._live(key)
            if attempts is not None and len(attempts) >= limit:
                unblock_at = attempts[-limit] + self._window
                wait = max(wait, unblock_at - self._clock())
        return math.ceil(wait) if wait > 0 else 0

    def record_failure(self, ip: str, username: str) -> None:
        now = self._clock()
        for key, _limit in self._limits(ip, username):
            self._failures.setdefault(key, deque()).append(now)
        if len(self._failures) > self._max_keys:
            self._prune()

    def reset(self, ip: str, username: str) -> None:
        """A successful login clears the (ip, username) counter only."""
        self._failures.pop(self._limits(ip, username)[0][0], None)

    def clear(self) -> None:
        self._failures.clear()

    def _prune(self) -> None:
        for key in list(self._failures):
            self._live(key)


login_throttle = LoginThrottle()
