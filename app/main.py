from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import router as api_router
from app.config import Settings, settings
from app.deps import LoginRequired
from app.musicbrainz.errors import MusicBrainzError, MusicBrainzNotFound
from app.web.auth import router as auth_router
from app.web.pages import router as pages_router
from app.web.templating import BASE_DIR

SESSION_COOKIE_NAME = "dd_session"
PLACEHOLDER_SECRET_PREFIX = "change-me"


def _check_secret_key(cfg: Settings) -> None:
    if not cfg.debug and cfg.secret_key.startswith(PLACEHOLDER_SECRET_PREFIX):
        raise RuntimeError("SECRET_KEY is still the placeholder; set a random value")


async def _musicbrainz_not_found(
    _request: Request, exc: Exception
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc) or "Not found"})


async def _musicbrainz_unavailable(
    _request: Request, _exc: Exception
) -> JSONResponse:
    # Retries exhausted, timeouts, and other client failures surface as 503.
    return JSONResponse(
        status_code=503,
        content={
            "detail": "MusicBrainz is unavailable right now. Please try again later."
        },
    )


async def _login_required(_request: Request, exc: Exception) -> RedirectResponse:
    assert isinstance(exc, LoginRequired)
    return RedirectResponse(f"/login?next={quote(exc.next_path, safe='/')}", status_code=303)


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
    application.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    application.add_exception_handler(MusicBrainzNotFound, _musicbrainz_not_found)
    application.add_exception_handler(MusicBrainzError, _musicbrainz_unavailable)
    application.add_exception_handler(LoginRequired, _login_required)
    return application


app = create_app()
