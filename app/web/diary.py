from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.deps import get_current_user, get_db, require_user
from app.models import User
from app.services.diary import UserNotFound, diary_for_user, public_profile
from app.web.templating import render

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
AuthUser = Annotated[User, Depends(require_user)]
MaybeUser = Annotated[User | None, Depends(get_current_user)]


@router.get("/diary")
def diary_page(
    request: Request,
    user: AuthUser,
    session: Db,
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    diary = diary_for_user(session, user.id, page=page)
    return render(
        request,
        "diary.html",
        {
            "current_user": user,
            "diary": diary,
            "pagination": diary.pagination,
        },
    )


@router.get("/u/{username}")
def profile_page(
    request: Request,
    username: str,
    session: Db,
    viewer: MaybeUser,
) -> Response:
    try:
        profile = public_profile(session, username)
    except UserNotFound:
        return render(
            request,
            "404.html",
            {
                "current_user": viewer,
                "message": f"No profile found for “{username}”.",
            },
            status_code=404,
        )
    return render(
        request,
        "profile.html",
        {
            "current_user": viewer,
            "profile": profile,
            "is_own_profile": viewer is not None and viewer.id == profile.user.id,
        },
    )
