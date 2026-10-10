from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.deps import get_current_user, get_db, get_mb_client, require_user
from app.models import User
from app.musicbrainz.client import MusicBrainzClient
from app.musicbrainz.errors import MusicBrainzError, MusicBrainzNotFound
from app.security.csrf import verify_csrf
from app.services.catalog import get_or_fetch_release_group
from app.services.logs import (
    LogNotFound,
    album_logging_context,
    create_log,
    delete_log,
    empty_form_values,
    get_owned_log,
    parse_log_form,
    update_log,
    values_from_log,
)
from app.web.templating import render

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Mb = Annotated[MusicBrainzClient, Depends(get_mb_client)]
AuthUser = Annotated[User, Depends(require_user)]
MaybeUser = Annotated[User | None, Depends(get_current_user)]


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _today() -> date:
    return date.today()


def _checkbox(value: str | None) -> bool:
    return value in {"on", "true", "1", "yes"}


async def _load_release_group(session: Session, mbid: str, mb_client: MusicBrainzClient):
    try:
        return await get_or_fetch_release_group(session, mbid, mb_client)
    except ValueError as exc:
        raise MusicBrainzNotFound(f"Invalid or unknown release group: {mbid}") from exc


def _logging_partial(
    request: Request,
    session: Session,
    release_group,
    user: User | None,
    *,
    status_code: int = 200,
    form_values=None,
    form_errors=None,
    editing_log=None,
    flash: str | None = None,
) -> Response:
    context = album_logging_context(
        session,
        release_group,
        user,
        today=_today(),
        form_values=form_values,
        form_errors=form_errors,
        editing_log=editing_log,
    )
    context.update({"current_user": user, "flash": flash})
    return render(
        request, "partials/album_logging.html", context, status_code=status_code
    )


def _not_found(request: Request, user: User | None) -> Response:
    return render(
        request,
        "error.html",
        {
            "current_user": user,
            "title": "Log not found",
            "message": "We couldn't find that log.",
        },
        status_code=404,
    )


@router.post("/albums/{mbid}/logs", dependencies=[Depends(verify_csrf)])
async def create_album_log(
    request: Request,
    mbid: str,
    session: Db,
    mb_client: Mb,
    user: AuthUser,
    rating: Annotated[str | None, Form()] = None,
    review_text: Annotated[str | None, Form()] = None,
    listened_on: Annotated[str | None, Form()] = None,
    is_relisten: Annotated[str | None, Form()] = None,
) -> Response:
    try:
        release_group = await _load_release_group(session, mbid, mb_client)
    except MusicBrainzNotFound:
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

    parsed = parse_log_form(
        rating=rating,
        review_text=review_text,
        listened_on=listened_on,
        is_relisten=_checkbox(is_relisten),
        today=_today(),
    )
    if parsed.data is None:
        if _is_htmx(request):
            return _logging_partial(
                request,
                session,
                release_group,
                user,
                status_code=422,
                form_values=parsed.values,
                form_errors=parsed.errors,
            )
        return RedirectResponse(f"/albums/{mbid}?log_error=1", status_code=303)

    create_log(session, user, release_group, parsed.data)
    if _is_htmx(request):
        return _logging_partial(
            request,
            session,
            release_group,
            user,
            form_values=empty_form_values(_today()),
            flash="Saved your listen.",
        )
    return RedirectResponse(f"/albums/{mbid}", status_code=303)


@router.get("/logs/{log_id}/edit")
async def edit_log_form(
    request: Request,
    log_id: int,
    session: Db,
    mb_client: Mb,
    user: AuthUser,
) -> Response:
    try:
        log = get_owned_log(session, log_id, user.id)
    except LogNotFound:
        return _not_found(request, user)

    release_group = log.release_group
    # Ensure relationship is loaded for mbid in the partial.
    _ = release_group.mbid
    if _is_htmx(request):
        return _logging_partial(
            request,
            session,
            release_group,
            user,
            form_values=values_from_log(log),
            editing_log=log,
        )
    return RedirectResponse(f"/albums/{release_group.mbid}?edit={log.id}", status_code=303)


@router.post("/logs/{log_id}", dependencies=[Depends(verify_csrf)])
async def update_album_log(
    request: Request,
    log_id: int,
    session: Db,
    user: AuthUser,
    rating: Annotated[str | None, Form()] = None,
    review_text: Annotated[str | None, Form()] = None,
    listened_on: Annotated[str | None, Form()] = None,
    is_relisten: Annotated[str | None, Form()] = None,
) -> Response:
    try:
        log = get_owned_log(session, log_id, user.id)
    except LogNotFound:
        return _not_found(request, user)

    release_group = log.release_group
    parsed = parse_log_form(
        rating=rating,
        review_text=review_text,
        listened_on=listened_on,
        is_relisten=_checkbox(is_relisten),
        today=_today(),
    )
    if parsed.data is None:
        if _is_htmx(request):
            return _logging_partial(
                request,
                session,
                release_group,
                user,
                status_code=422,
                form_values=parsed.values,
                form_errors=parsed.errors,
                editing_log=log,
            )
        return RedirectResponse(
            f"/albums/{release_group.mbid}?edit={log.id}&log_error=1", status_code=303
        )

    update_log(session, log, parsed.data)
    if _is_htmx(request):
        return _logging_partial(
            request,
            session,
            release_group,
            user,
            form_values=empty_form_values(_today()),
            flash="Updated your listen.",
        )
    return RedirectResponse(f"/albums/{release_group.mbid}", status_code=303)


@router.post("/logs/{log_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete_album_log(
    request: Request,
    log_id: int,
    session: Db,
    user: AuthUser,
) -> Response:
    try:
        log = get_owned_log(session, log_id, user.id)
    except LogNotFound:
        return _not_found(request, user)

    release_group = log.release_group
    mbid = release_group.mbid
    delete_log(session, log)
    if _is_htmx(request):
        return _logging_partial(
            request,
            session,
            release_group,
            user,
            form_values=empty_form_values(_today()),
            flash="Deleted your listen.",
        )
    return RedirectResponse(f"/albums/{mbid}", status_code=303)
