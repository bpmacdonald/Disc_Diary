from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.musicbrainz.errors import MusicBrainzError, MusicBrainzNotFound

app = FastAPI(title="Disc Diary", version="0.1.0")
app.include_router(router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(MusicBrainzNotFound)
async def musicbrainz_not_found(
    _request: Request, exc: MusicBrainzNotFound
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc) or "Not found"})


@app.exception_handler(MusicBrainzError)
async def musicbrainz_unavailable(
    _request: Request, exc: MusicBrainzError
) -> JSONResponse:
    # Retries exhausted, timeouts, and other client failures surface as 503.
    return JSONResponse(
        status_code=503,
        content={
            "detail": "MusicBrainz is unavailable right now. Please try again later."
        },
    )
