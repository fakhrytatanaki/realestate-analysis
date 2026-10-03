"""Domain-level errors.

These carry no HTTP knowledge; ``realestate.api.errors`` maps them onto status
codes so the domain stays transport-agnostic.
"""

from __future__ import annotations


class RealEstateError(Exception):
    """Base class for every error this application raises deliberately."""


class ConfigurationError(RealEstateError):
    """Settings are missing or internally inconsistent."""


class NotFoundError(RealEstateError):
    """A requested entity does not exist."""

    def __init__(self, entity: str, identifier: object) -> None:
        super().__init__(f"{entity} '{identifier}' was not found")
        self.entity = entity
        self.identifier = identifier


class InvalidQueryError(RealEstateError):
    """A caller supplied a filter combination that cannot be satisfied."""


class BlobNotFoundError(NotFoundError):
    """A blob key has no stored object behind it."""

    def __init__(self, key: str) -> None:
        super().__init__("Blob", key)
        self.key = key


class BlobKeyError(RealEstateError):
    """A blob key is malformed or tries to escape the provider's root."""


class UnknownDataSourceError(NotFoundError):
    """No data source is registered under the given key."""

    def __init__(self, key: str) -> None:
        super().__init__("DataSource", key)
        self.key = key


class DataSourceDisabledError(RealEstateError):
    """The data source exists but is switched off in configuration."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Data source '{key}' is disabled in configuration")
        self.key = key


class DataSourceNotImplementedError(RealEstateError):
    """The data source is registered as a placeholder but has no working
    collection logic yet. See ``docs/architecture.md`` for how to implement one."""

    def __init__(self, key: str) -> None:
        super().__init__(
            f"Data source '{key}' is a registered stub with no implementation yet; "
            f"see docs/architecture.md for how to implement it"
        )
        self.key = key


class FetchError(RealEstateError):
    """A source failed while collecting raw payloads.

    ``retryable`` says whether trying again later may succeed (timeouts, rate
    limits, server errors) or not (404, gone, forbidden).
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ParseError(RealEstateError):
    """A source failed while turning a raw payload into listing drafts."""


class AuthorizationError(RealEstateError):
    """A privileged operation was attempted without valid credentials."""


class UnrecognisedDocumentError(ParseError):
    """No extraction rule recognised the document yet.

    Not a bug: the pipeline records the document as ``UNRECOGNISED`` and rule
    induction later learns a rule for it, after which it is re-parsed.
    """


class LlmError(RealEstateError):
    """The language model could not be reached or returned nothing usable."""
