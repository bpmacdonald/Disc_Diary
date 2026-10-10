import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from types import TracebackType
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.musicbrainz.errors import (
    MusicBrainzConfigError,
    MusicBrainzError,
    MusicBrainzNotFound,
    MusicBrainzRetriesExhausted,
)
from app.musicbrainz.models import Artist, ReleaseGroup, ReleaseGroupSearchResult
from app.musicbrainz.ratelimit import TokenBucket

BASE_URL = "https://musicbrainz.org/ws/2"
RETRY_STATUSES = frozenset({429, 503})
TIMEOUT = httpx.Timeout(15.0, connect=5.0)

M = TypeVar("M", bound=BaseModel)
Sleep = Callable[[float], Awaitable[None]]


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def normalize_mbid(mbid: str) -> str:
    """Validate an MBID so it can never alter the request path."""
    try:
        return str(uuid.UUID(mbid))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"Invalid MBID: {mbid!r}") from exc


def _user_agent_from_settings() -> str:
    try:
        from app.config import settings

        return settings.mb_user_agent
    except ValidationError as exc:
        raise MusicBrainzConfigError(
            "MB_USER_AGENT is missing; set it in the environment"
        ) from exc


class MusicBrainzClient:
    """The single async MusicBrainz client. Use get_musicbrainz_client()."""

    def __init__(
        self,
        user_agent: str | None = None,
        *,
        base_url: str = BASE_URL,
        limiter: TokenBucket | None = None,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        max_retry_after: float = 120.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if user_agent is None:
            user_agent = _user_agent_from_settings()
        if not user_agent.strip():
            raise MusicBrainzConfigError("MB_USER_AGENT must be set and non-empty")

        self._limiter = limiter or TokenBucket(rate=1.0, capacity=1.0)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._max_retry_after = max_retry_after
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": user_agent.strip(), "Accept": "application/json"},
            timeout=TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "MusicBrainzClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ---- public API -------------------------------------------------

    async def search_release_groups(
        self, query: str, limit: int = 25, offset: int = 0
    ) -> ReleaseGroupSearchResult:
        data = await self._get(
            "release-group", {"query": query, "limit": limit, "offset": offset}
        )
        return self._parse(ReleaseGroupSearchResult, data)

    async def get_release_group(self, mbid: str) -> ReleaseGroup:
        data = await self._get(
            f"release-group/{normalize_mbid(mbid)}", {"inc": "artists+tags"}
        )
        return self._parse(ReleaseGroup, data)

    async def get_artist(self, mbid: str) -> Artist:
        data = await self._get(f"artist/{normalize_mbid(mbid)}", {})
        return self._parse(Artist, data)

    # ---- internals --------------------------------------------------

    @staticmethod
    def _parse(model: type[M], data: Any) -> M:
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise MusicBrainzError(f"Unexpected response shape: {exc}") from exc

    def _backoff_delay(self, attempt: int) -> float:
        return min(self._backoff_base * 2**attempt, self._backoff_max)

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        if retry_after is None:
            return self._backoff_delay(attempt)
        if retry_after > self._max_retry_after:
            raise MusicBrainzRetriesExhausted(response.status_code, attempt + 1)
        return retry_after

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        query = {**params, "fmt": "json"}
        last_status = 0
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()  # every attempt counts as a request
            try:
                response = await self._http.get(path, params=query)
            except httpx.HTTPError as exc:
                raise MusicBrainzError(f"Request to MusicBrainz failed: {exc!r}") from exc

            last_status = response.status_code
            if last_status in RETRY_STATUSES:
                if attempt < self._max_retries:
                    await self._sleep(self._retry_delay(response, attempt))
                continue
            if last_status == 404:
                raise MusicBrainzNotFound(f"Not found: {path}")
            if last_status >= 400:
                raise MusicBrainzError(f"MusicBrainz returned HTTP {last_status}")
            try:
                return response.json()
            except ValueError as exc:
                raise MusicBrainzError("MusicBrainz returned invalid JSON") from exc

        raise MusicBrainzRetriesExhausted(last_status, self._max_retries + 1)


@lru_cache(maxsize=1)
def get_musicbrainz_client() -> MusicBrainzClient:
    """Process-wide singleton so the rate limiter is truly global."""
    return MusicBrainzClient()
