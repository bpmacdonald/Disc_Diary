"""Small helpers for presenting catalog data in templates."""

from __future__ import annotations

from app.schemas import ArtistOut, SearchHitOut
from app.services.catalog import PLACEHOLDER_COVER_PATH, resolve_cover_url

# Cover Art Archive sized front image; the <img> falls back if CAA has no art.
CAA_FRONT_THUMB = "https://coverartarchive.org/release-group/{mbid}/front-250"


def year_from_date(value: str | None) -> str | None:
    if value and len(value) >= 4 and value[:4].isdigit():
        return value[:4]
    return None


def artists_label(artists: list[ArtistOut]) -> str:
    parts: list[str] = []
    for artist in artists:
        parts.append(artist.name)
        if artist.join_phrase:
            parts.append(artist.join_phrase)
    return "".join(parts) if parts else "Unknown artist"


def search_cover_url(mbid: str) -> str:
    """Candidate cover URL for search cards (may 404; templates use onerror)."""
    return CAA_FRONT_THUMB.format(mbid=mbid)


def album_cover_url(stored: str | None) -> str:
    return resolve_cover_url(stored)


def search_hit_year(hit: SearchHitOut) -> str | None:
    return year_from_date(hit.first_release_date)


__all__ = [
    "PLACEHOLDER_COVER_PATH",
    "artists_label",
    "album_cover_url",
    "search_cover_url",
    "search_hit_year",
    "year_from_date",
]
