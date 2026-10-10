"""Album (release-group) read model for the JSON API."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ReleaseGroup
from app.musicbrainz.client import MusicBrainzClient
from app.musicbrainz.errors import MusicBrainzNotFound
from app.schemas import AlbumOut, ArtistOut
from app.services.catalog import get_or_fetch_release_group, resolve_cover_url


def album_to_out(rg: ReleaseGroup) -> AlbumOut:
    artists = [
        ArtistOut(
            mbid=link.artist.mbid,
            name=link.artist.name,
            join_phrase=link.join_phrase,
        )
        for link in sorted(rg.artist_links, key=lambda link: link.position)
    ]
    return AlbumOut(
        mbid=rg.mbid,
        title=rg.title,
        artists=artists,
        first_release_date=rg.first_release_date,
        primary_type=rg.primary_type,
        cover_url=resolve_cover_url(rg.cover_url),
    )


async def get_album(
    session: Session, mbid: str, mb_client: MusicBrainzClient
) -> AlbumOut:
    """Load an album from the catalog cache, fetching when needed.

    Malformed MBIDs are treated as not found (HTTP 404), matching unknown IDs.
    """
    try:
        rg = await get_or_fetch_release_group(session, mbid, mb_client)
    except ValueError as exc:
        raise MusicBrainzNotFound(f"Invalid or unknown release group: {mbid}") from exc
    return album_to_out(rg)
