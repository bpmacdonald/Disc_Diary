"""Release-group search with a short-lived in-memory cache."""

from __future__ import annotations

from app.cache import TTLCache
from app.musicbrainz.client import MusicBrainzClient
from app.musicbrainz.models import ReleaseGroupSearchHit, ReleaseGroupSearchResult
from app.schemas import ArtistOut, SearchHitOut, SearchResponse

SEARCH_CACHE_TTL_SECONDS = 600.0  # 10 minutes
search_cache: TTLCache[ReleaseGroupSearchResult] = TTLCache(SEARCH_CACHE_TTL_SECONDS)


def _hit_to_out(hit: ReleaseGroupSearchHit) -> SearchHitOut:
    return SearchHitOut(
        mbid=hit.id,
        title=hit.title,
        primary_type=hit.primary_type,
        first_release_date=hit.first_release_date,
        artists=[
            ArtistOut(
                mbid=credit.artist.id,
                name=credit.artist.name,
                join_phrase=credit.join_phrase or None,
            )
            for credit in hit.artist_credit
        ],
        score=hit.score,
    )


async def search_release_groups(
    mb_client: MusicBrainzClient,
    query: str,
    *,
    limit: int = 25,
    offset: int = 0,
    cache: TTLCache[ReleaseGroupSearchResult] | None = None,
) -> SearchResponse:
    """Search MusicBrainz; cache by (query, limit, offset) for the TTL window."""
    store = search_cache if cache is None else cache
    key = (query.strip().lower(), limit, offset)

    async def fetch() -> ReleaseGroupSearchResult:
        return await mb_client.search_release_groups(query, limit=limit, offset=offset)

    result = await store.get_or_set(key, fetch)
    return SearchResponse(
        count=result.count,
        offset=result.offset,
        limit=limit,
        results=[_hit_to_out(hit) for hit in result.release_groups],
    )
