from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, get_mb_client
from app.musicbrainz.client import MusicBrainzClient
from app.schemas import AlbumOut, SearchResponse
from app.services import albums as albums_service
from app.services import search as search_service

router = APIRouter(prefix="/api")


@router.get("/search", response_model=SearchResponse)
async def search_release_groups(
    q: Annotated[str, Query(min_length=1, max_length=200)],
    mb_client: Annotated[MusicBrainzClient, Depends(get_mb_client)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    return await search_service.search_release_groups(
        mb_client, q, limit=limit, offset=offset
    )


@router.get("/albums/{mbid}", response_model=AlbumOut)
async def get_album(
    mbid: str,
    session: Annotated[Session, Depends(get_db)],
    mb_client: Annotated[MusicBrainzClient, Depends(get_mb_client)],
) -> AlbumOut:
    return await albums_service.get_album(session, mbid, mb_client)
