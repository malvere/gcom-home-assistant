"""Exceptions deliberately contain no response text, request URL, or credentials."""


class GcomError(Exception):
    """Base client error."""


class AuthenticationError(GcomError):
    """Authentication failed or no usable token is available."""


class HttpError(GcomError):
    """The API returned an unsuccessful HTTP status."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Gcom API returned HTTP {status}")


class ProtocolError(GcomError):
    """The API returned an unexpected or unsuccessful payload."""


class MutationNotAllowed(GcomError):
    """A door or access-code operation requires explicit opt-in."""


class MutationOutcomeUnknown(GcomError):
    """A mutation may have reached the server, but no result was received."""


class ScopeError(GcomError):
    """The requested ID was absent from the current user's list."""
