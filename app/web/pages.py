from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.deps import get_current_user, get_db, get_mb_client
from app.models import User
from app.musicbrainz.client import MusicBrainzClient
from app.musicbrainz.errors import MusicBrainzError, MusicBrainzNotFound
from app.services import albums as albums_service
from app.services import search as search_service
from app.services.catalog import get_or_fetch_release_group
from app.services.display import (
    PLACEHOLDER_COVER_PATH,
    artists_label,
    search_cover_url,
    year_from_date,
)
from app.services.logs import (
    LogNotFound,
    album_logging_context,
    empty_form_values,
    get_owned_log,
    values_from_log,
)
from app.services.tracklist import format_duration, get_or_fetch_tracklist
from app.web.templating import render

router = APIRouter()

CurrentUser = Annotated[User | None, Depends(get_current_user)]
Db = Annotated[Session, Depends(get_db)]
Mb = Annotated[MusicBrainzClient, Depends(get_mb_client)]


@router.get("/")
def home() -> Response:
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/search", status_code=303)


@router.get("/search")
def search_page(request: Request, user: CurrentUser, q: str = "") -> Response:
    return render(
        request,
        "search.html",
        {"current_user": user, "q": q.strip()},
    )


@router.get("/search/results")
async def search_results(
    request: Request,
    user: CurrentUser,
    mb_client: Mb,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
) -> Response:
    query = q.strip()
    if len(query) < 2:
        return render(
            request,
            "partials/search_results.html",
            {
                "current_user": user,
                "q": query,
                "results": [],
                "hint": "Type at least 2 characters to search.",
            },
        )
    try:
        response = await search_service.search_release_groups(
            mb_client, query, limit=limit, offset=0
        )
    except MusicBrainzError:
        return render(
            request,
            "partials/search_results.html",
            {
                "current_user": user,
                "q": query,
                "results": [],
                "error": "MusicBrainz is unavailable right now. Please try again later.",
            },
            status_code=503,
        )

    cards = [
        {
            "mbid": hit.mbid,
            "title": hit.title,
            "artists": artists_label(hit.artists),
            "year": year_from_date(hit.first_release_date),
            "cover_url": search_cover_url(hit.mbid),
            "primary_type": hit.primary_type,
        }
        for hit in response.results
    ]
    return render(
        request,
        "partials/search_results.html",
        {
            "current_user": user,
            "q": query,
            "results": cards,
            "count": response.count,
            "placeholder_cover": PLACEHOLDER_COVER_PATH,
        },
    )


@router.get("/albums/{mbid}")
async def album_page(
    request: Request,
    mbid: str,
    user: CurrentUser,
    session: Db,
    mb_client: Mb,
    edit: int | None = None,
) -> Response:
    try:
        release_group = await get_or_fetch_release_group(session, mbid, mb_client)
        album = albums_service.album_to_out(release_group)
    except (MusicBrainzNotFound, ValueError):
        return render(
            request,
            "error.html",
            {
                "current_user": user,
                "title": "Album not found",
                "message": "We couldn't find that album.",
            },
            status_code=404,
        )
    except MusicBrainzError:
        return render(
            request,
            "error.html",
            {
                "current_user": user,
                "title": "MusicBrainz unavailable",
                "message": "MusicBrainz is unavailable right now. Please try again later.",
            },
            status_code=503,
        )

    editing_log = None
    form_values = empty_form_values(date.today())
    form_errors: dict[str, str] = {}
    if edit is not None and user is not None:
        try:
            editing_log = get_owned_log(session, edit, user.id)
            form_values = values_from_log(editing_log)
        except LogNotFound:
            editing_log = None

    logging_ctx = album_logging_context(
        session,
        release_group,
        user,
        today=date.today(),
        form_values=form_values,
        form_errors=form_errors,
        editing_log=editing_log,
    )
    return render(
        request,
        "album.html",
        {
            "current_user": user,
            "album": album,
            "artists_label": artists_label(album.artists),
            "cover_url": album.cover_url,
            "year": year_from_date(album.first_release_date),
            "placeholder_cover": PLACEHOLDER_COVER_PATH,
            **logging_ctx,
        },
    )


@router.get("/albums/{mbid}/tracks")
async def album_tracklist(
    request: Request,
    mbid: str,
    user: CurrentUser,
    session: Db,
    mb_client: Mb,
) -> Response:
    try:
        tracklist = await get_or_fetch_tracklist(session, mbid, mb_client)
    except MusicBrainzNotFound:
        return render(
            request,
            "partials/tracklist.html",
            {
                "current_user": user,
                "error": "No tracklist is available for this album.",
            },
            status_code=404,
        )
    except MusicBrainzError:
        return render(
            request,
            "partials/tracklist.html",
            {
                "current_user": user,
                "error": "MusicBrainz is unavailable right now. Please try again later.",
            },
            status_code=503,
        )

    return render(
        request,
        "partials/tracklist.html",
        {
            "current_user": user,
            "tracklist": tracklist,
            "tracks": [
                {
                    "disc": track.disc,
                    "number": track.number,
                    "title": track.title,
                    "duration": format_duration(track.length_ms),
                }
                for track in tracklist.tracks
            ],
            "multi_disc": len({track.disc for track in tracklist.tracks}) > 1,
        },
    )
