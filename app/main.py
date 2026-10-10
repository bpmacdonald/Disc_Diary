import logging
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler as fastapi_http_exception_handler
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import router as api_router
from app.config import Settings, settings
from app.deps import LoginRequired
from app.musicbrainz.errors import MusicBrainzError, MusicBrainzNotFound
from app.web.auth import router as auth_router
from app.web.diary import router as diary_router
from app.web.logs import router as logs_router
from app.web.pages import router as pages_router
from app.web.templating import BASE_DIR, render

SESSION_COOKIE_NAME = "dd_session"
PLACEHOLDER_SECRET_PREFIX = "change-me"
logger = logging.getLogger(__name__)


def _check_secret_key(cfg: Settings) -> None:
    if not cfg.debug and cfg.secret_key.startswith(PLACEHOLDER_SECRET_PREFIX):
        raise RuntimeError("SECRET_KEY is still the placeholder; set a random value")


def _wants_json(request: Request) -> bool:
    path = request.url.path
    if path.startswith("/api/"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


async def _musicbrainz_not_found(
    request: Request, exc: Exception
) -> Response:
    if _wants_json(request):
        return JSONResponse(status_code=404, content={"detail": str(exc) or "Not found"})
    return render(
        request,
        "404.html",
        {"message": str(exc) or "We couldn’t find that album."},
        status_code=404,
    )


async def _musicbrainz_unavailable(
    request: Request, _exc: Exception
) -> Response:
    # Retries exhausted, timeouts, and other client failures surface as 503.
    if _wants_json(request):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "MusicBrainz is unavailable right now. Please try again later."
            },
        )
    return render(
        request,
        "error.html",
        {
            "title": "MusicBrainz unavailable",
            "message": "MusicBrainz is unavailable right now. Please try again later.",
        },
        status_code=503,
    )


async def _login_required(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, LoginRequired)
    login_url = f"/login?next={quote(exc.next_path, safe='/')}"
    # HTMX ignores normal redirects for non-GET; HX-Redirect sends the browser.
    if request.headers.get("hx-request") == "true":
        response = Response(status_code=200)
        response.headers["HX-Redirect"] = login_url
        return response
    return RedirectResponse(login_url, status_code=303)


async def _http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    if _wants_json(request):
        return await fastapi_http_exception_handler(request, exc)
    if exc.status_code == 404:
        detail = exc.detail if isinstance(exc.detail, str) else None
        return render(
            request,
            "404.html",
            {"message": detail or "We couldn’t find what you were looking for."},
            status_code=404,
        )
    if exc.status_code >= 500:
        return render(request, "500.html", status_code=exc.status_code)
    return render(
        request,
        "error.html",
        {
            "title": "Error",
            "message": exc.detail if isinstance(exc.detail, str) else "Request failed.",
        },
        status_code=exc.status_code,
    )


async def _unhandled_error(request: Request, exc: Exception) -> Response:
    logger.exception("Unhandled error on %s", request.url.path)
    if _wants_json(request):
        return JSONResponse(
            status_code=500, content={"detail": "Internal server error"}
        )
    try:
        return render(request, "500.html", status_code=500)
    except Exception:  # pragma: no cover - last-resort plain response
        return Response("Internal Server Error", status_code=500)


def create_app(cfg: Settings | None = None) -> FastAPI:
    cfg = cfg or settings
    _check_secret_key(cfg)

    application = FastAPI(title="Disc Diary", version="0.1.0")
    application.add_middleware(
        SessionMiddleware,
        secret_key=cfg.secret_key,
        session_cookie=SESSION_COOKIE_NAME,
        max_age=cfg.session_max_age_seconds,
        same_site="lax",
        https_only=not cfg.debug,  # sets the Secure attribute; HttpOnly is always on
    )
    application.include_router(api_router)
    application.include_router(auth_router)
    application.include_router(pages_router)
    application.include_router(logs_router)
    application.include_router(diary_router)
    application.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    application.add_exception_handler(MusicBrainzNotFound, _musicbrainz_not_found)
    application.add_exception_handler(MusicBrainzError, _musicbrainz_unavailable)
    application.add_exception_handler(LoginRequired, _login_required)
    application.add_exception_handler(StarletteHTTPException, _http_exception)
    application.add_exception_handler(Exception, _unhandled_error)
    return application


app = create_app()
