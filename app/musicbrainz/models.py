"""Pydantic models for only the MusicBrainz response fields we use."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _MBModel(BaseModel):
    # Unknown fields are ignored (MusicBrainz returns many we don't need).
    model_config = ConfigDict(populate_by_name=True)


class ArtistRef(_MBModel):
    id: str
    name: str
    sort_name: str | None = Field(default=None, alias="sort-name")


class ArtistCredit(_MBModel):
    name: str
    join_phrase: str = Field(default="", alias="joinphrase")
    artist: ArtistRef


class Tag(_MBModel):
    name: str
    count: int = 0


class ReleaseGroup(_MBModel):
    id: str
    title: str
    primary_type: str | None = Field(default=None, alias="primary-type")
    first_release_date: str | None = Field(default=None, alias="first-release-date")
    artist_credit: list[ArtistCredit] = Field(default_factory=list, alias="artist-credit")
    tags: list[Tag] = Field(default_factory=list)

    @field_validator("first_release_date", "primary_type", mode="after")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        return value or None


class ReleaseGroupSearchHit(ReleaseGroup):
    score: int | None = None


class ReleaseGroupSearchResult(_MBModel):
    count: int
    offset: int
    release_groups: list[ReleaseGroupSearchHit] = Field(
        default_factory=list, alias="release-groups"
    )


class Artist(_MBModel):
    id: str
    name: str
    sort_name: str | None = Field(default=None, alias="sort-name")
    type: str | None = None
    country: str | None = None
