from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.deps import get_current_user, get_db, get_login_throttle, require_user
from app.models import User
from app.security.csrf import verify_csrf
from app.security.session import end_session, safe_next_path, start_session
from app.security.throttle import LoginThrottle
from app.services import auth as auth_service
from app.web.templating import render

router = APIRouter()

CurrentUser = Annotated[User | None, Depends(get_current_user)]
Db = Annotated[Session, Depends(get_db)]


def _client_ip(request: Request) -> str:
    # request.client is the direct peer. X-Forwarded-For is deliberately NOT
    # trusted: it is client-controlled unless a trusted proxy overwrites it.
    return request.client.host if request.client else "unknown"


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)  # 303: POST -> GET


@router.get("/signup")
def signup_page(request: Request, user: CurrentUser) -> Response:
    if user is not None:
        return _redirect("/account")
    return render(request, "signup.html")


@router.post("/signup", dependencies=[Depends(verify_csrf)])
def signup_submit(
    request: Request,
    db: Db,
    username: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> Response:
    result = auth_service.signup(db, username, email, password)
    if result.user is None:
        # Never echo the password back.
        context = {"errors": result.errors, "username": username, "email": email}
        return render(request, "signup.html", context, status_code=400)
    start_session(request, result.user.id)
    return _redirect("/account")


@router.get("/login")
def login_page(request: Request, user: CurrentUser, next: str = "") -> Response:
    if user is not None:
        return _redirect(safe_next_path(next))
    return render(request, "login.html", {"next": safe_next_path(next, "")})


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login_submit(
    request: Request,
    db: Db,
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> Response:
    result = auth_service.attempt_login(
        db, throttle, ip=_client_ip(request), username=username, password=password
    )
    safe_next = safe_next_path(next, "")
    if result.retry_after > 0:
        minutes = max(1, -(-result.retry_after // 60))
        error = (
            f"Too many failed attempts. Try again in {minutes} "
            f"minute{'s' if minutes != 1 else ''}."
        )
        response = render(
            request,
            "login.html",
            {"error": error, "username": username, "next": safe_next},
            status_code=429,
        )
        response.headers["Retry-After"] = str(result.retry_after)
        return response
    if result.user is None:
        return render(
            request,
            "login.html",
            {"error": auth_service.LOGIN_ERROR, "username": username, "next": safe_next},
            status_code=401,
        )
    start_session(request, result.user.id)
    return _redirect(safe_next_path(next))


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request) -> Response:
    end_session(request)
    return _redirect("/login")


@router.get("/account")
def account(request: Request, user: Annotated[User, Depends(require_user)]) -> Response:
    return render(request, "account.html", {"user": user})
