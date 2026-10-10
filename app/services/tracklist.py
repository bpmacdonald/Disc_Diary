"""Fetch and cache a representative release's tracklist for a release group."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.models import ReleaseGroup, ReleaseGroupTracklist
from app.musicbrainz.client import MusicBrainzClient
from app.musicbrainz.errors import MusicBrainzNotFound
from app.musicbrainz.models import ReleaseDetail, ReleaseSummary
from app.services.catalog import get_or_fetch_release_group, is_fresh

# Prefer releases from these countries when dates/status tie.
_COUNTRY_RANK = {"XW": 0, "XE": 1, "US": 2, "GB": 3, "JP": 4}


@dataclass(frozen=True)
class TrackRow:
    disc: int
    position: int
    number: str
    title: str
    length_ms: int | None
    recording_mbid: str | None


@dataclass(frozen=True)
class TracklistView:
    release_group_mbid: str
    release_mbid: str
    release_title: str | None
    tracks: list[TrackRow]
    fetched_at: datetime


def pick_representative_release(releases: list[ReleaseSummary]) -> ReleaseSummary | None:
    """Choose one release to stand in for the release group's tracklist.

    Prefer Official status, then earliest date, then preferred country,
    then highest known track count. Pure ranking — no API calls.
    """
    if not releases:
        return None

    def sort_key(release: ReleaseSummary) -> tuple:
        status_rank = 0 if (release.status or "").lower() == "official" else 1
        date = release.date or "9999"
        country = _COUNTRY_RANK.get((release.country or "").upper(), 50)
        # Negate so more tracks sort earlier.
        tracks = -(release.track_count or 0)
        return (status_rank, date, country, tracks, release.id)

    return min(releases, key=sort_key)


def tracks_from_release(detail: ReleaseDetail) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for medium in detail.media:
        disc = medium.position or 1
        for track in medium.tracks:
            title = track.title or (track.recording.title if track.recording else "") or ""
            length = track.length
            if length is None and track.recording is not None:
                length = track.recording.length
            rows.append(
                {
                    "disc": disc,
                    "position": track.position or 0,
                    "number": track.number or str(track.position or ""),
                    "title": title,
                    "length_ms": length,
                    "recording_mbid": track.recording.id if track.recording else None,
                }
            )
    return rows


def format_duration(length_ms: int | None) -> str:
    if length_ms is None or length_ms < 0:
        return ""
    total_seconds = length_ms // 1000
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def _to_view(rg_mbid: str, cached: ReleaseGroupTracklist) -> TracklistView:
    tracks = [
        TrackRow(
            disc=int(row.get("disc", 1)),
            position=int(row.get("position", 0)),
            number=str(row.get("number") or ""),
            title=str(row.get("title") or ""),
            length_ms=row.get("length_ms"),
            recording_mbid=row.get("recording_mbid"),
        )
        for row in cached.tracks
    ]
    return TracklistView(
        release_group_mbid=rg_mbid,
        release_mbid=cached.release_mbid,
        release_title=cached.release_title,
        tracks=tracks,
        fetched_at=cached.fetched_at,
    )


async def get_or_fetch_tracklist(
    session: Session,
    release_group_mbid: str,
    mb_client: MusicBrainzClient,
    *,
    staleness_days: int | None = None,
    now: datetime | None = None,
) -> TracklistView:
    """Return a cached tracklist, refreshing from MusicBrainz when stale."""
    current = now or datetime.now(timezone.utc)
    # Ensures the release group row exists (and is fresh) before we attach tracks.
    rg = await get_or_fetch_release_group(
        session, release_group_mbid, mb_client, staleness_days=staleness_days, now=current
    )
    cached = session.scalar(
        select(ReleaseGroupTracklist).where(
            ReleaseGroupTracklist.release_group_id == rg.id
        )
    )
    if cached is not None and is_fresh(
        cached.fetched_at, staleness_days=staleness_days, now=current
    ):
        return _to_view(rg.mbid, cached)

    browse = await mb_client.browse_releases_for_group(rg.mbid)
    chosen = pick_representative_release(browse.releases)
    if chosen is None:
        # Official filter can be empty; retry with no status filter once.
        browse = await mb_client.browse_releases_for_group(rg.mbid, status=None)
        chosen = pick_representative_release(browse.releases)
    if chosen is None:
        raise MusicBrainzNotFound(f"No releases found for release group {rg.mbid}")

    detail = await mb_client.get_release(chosen.id)
    track_rows = tracks_from_release(detail)
    session.execute(
        insert(ReleaseGroupTracklist)
        .values(
            release_group_id=rg.id,
            release_mbid=detail.id,
            release_title=detail.title,
            tracks=track_rows,
            fetched_at=current,
        )
        .on_conflict_do_update(
            index_elements=[ReleaseGroupTracklist.release_group_id],
            set_={
                "release_mbid": detail.id,
                "release_title": detail.title,
                "tracks": track_rows,
                "fetched_at": current,
            },
        )
    )
    session.flush()
    session.expire_all()
    saved = session.scalar(
        select(ReleaseGroupTracklist).where(
            ReleaseGroupTracklist.release_group_id == rg.id
        )
    )
    if saved is None:  # pragma: no cover
        raise RuntimeError("Tracklist missing after upsert")
    return _to_view(rg.mbid, saved)


def load_release_group_with_tracklist(
    session: Session, mbid: str
) -> ReleaseGroup | None:
    return session.scalars(
        select(ReleaseGroup)
        .where(ReleaseGroup.mbid == mbid)
        .options(selectinload(ReleaseGroup.tracklist))
    ).first()
