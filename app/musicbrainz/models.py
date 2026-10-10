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


class ReleaseSummary(_MBModel):
    """A release listed under a release group (no tracklist yet)."""

    id: str
    title: str
    status: str | None = None
    country: str | None = None
    date: str | None = None
    track_count: int | None = Field(default=None, alias="track-count")

    @field_validator("status", "country", "date", mode="after")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        return value or None


class ReleaseBrowseResult(_MBModel):
    # Browse responses use release-count / release-offset.
    count: int = Field(default=0, alias="release-count")
    offset: int = Field(default=0, alias="release-offset")
    releases: list[ReleaseSummary] = Field(default_factory=list)


class RecordingRef(_MBModel):
    id: str | None = None
    title: str | None = None
    length: int | None = None  # milliseconds


class Track(_MBModel):
    id: str | None = None
    position: int | None = None
    number: str | None = None
    title: str
    length: int | None = None
    recording: RecordingRef | None = None


class Medium(_MBModel):
    position: int = 1
    format: str | None = None
    track_count: int | None = Field(default=None, alias="track-count")
    tracks: list[Track] = Field(default_factory=list)


class ReleaseDetail(_MBModel):
    id: str
    title: str
    status: str | None = None
    country: str | None = None
    date: str | None = None
    media: list[Medium] = Field(default_factory=list)
