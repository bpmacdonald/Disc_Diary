class MusicBrainzError(Exception):
    """Base class for all MusicBrainz client failures."""


class MusicBrainzConfigError(MusicBrainzError):
    """The client is misconfigured (e.g. missing User-Agent)."""


class MusicBrainzNotFound(MusicBrainzError):
    """The requested entity does not exist (HTTP 404)."""


class MusicBrainzRetriesExhausted(MusicBrainzError):
    """The server kept answering 503/429 until retries ran out."""

    def __init__(self, status_code: int, attempts: int) -> None:
        super().__init__(
            f"MusicBrainz still returned HTTP {status_code} after {attempts} attempts"
        )
        self.status_code = status_code
        self.attempts = attempts
