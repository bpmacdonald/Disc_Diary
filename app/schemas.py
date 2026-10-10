from pydantic import BaseModel, Field


class ArtistOut(BaseModel):
    mbid: str
    name: str
    join_phrase: str | None = None


class SearchHitOut(BaseModel):
    mbid: str
    title: str
    primary_type: str | None = None
    first_release_date: str | None = None
    artists: list[ArtistOut]
    score: int | None = None


class SearchResponse(BaseModel):
    count: int
    offset: int
    limit: int
    results: list[SearchHitOut]


class AlbumOut(BaseModel):
    mbid: str
    title: str
    artists: list[ArtistOut]
    first_release_date: str | None = None
    primary_type: str | None = None
    cover_url: str = Field(description="Stored CAA URL, or the placeholder path")
