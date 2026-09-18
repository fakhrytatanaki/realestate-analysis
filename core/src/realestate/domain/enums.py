"""Domain vocabulary shared by every layer.

All enums are ``str``-valued so they serialise cleanly through pydantic DTOs and
are stored verbatim by Tortoise's ``CharEnumField``.
"""

from __future__ import annotations

from enum import StrEnum


class ListingType(StrEnum):
    """What is on offer."""

    RENT = "RENT"
    SALE = "SALE"


class PropertyType(StrEnum):
    """Kind of property. ``OTHER`` is the fallback for anything a source
    reports that we have not mapped yet."""

    APARTMENT = "APARTMENT"
    VILLA = "VILLA"
    TOWNHOUSE = "TOWNHOUSE"
    HOUSE = "HOUSE"
    STUDIO = "STUDIO"
    LAND = "LAND"
    OFFICE = "OFFICE"
    SHOP = "SHOP"
    WAREHOUSE = "WAREHOUSE"
    OTHER = "OTHER"


class PriceType(StrEnum):
    """How the ``price`` figure should be read."""

    TOTAL = "TOTAL"
    PER_MONTH = "PER_MONTH"
    PER_WEEK = "PER_WEEK"
    PER_NIGHT = "PER_NIGHT"
    PER_YEAR = "PER_YEAR"
    PER_SQM = "PER_SQM"
    INSTALLMENT = "INSTALLMENT"
    ON_REQUEST = "ON_REQUEST"


class RawDocumentKind(StrEnum):
    """Wire format of an archived payload."""

    JSON = "JSON"
    HTML = "HTML"
    XML = "XML"
    SCREENSHOT = "SCREENSHOT"
    OTHER = "OTHER"


class RawDocumentStatus(StrEnum):
    """Lifecycle of an archived payload through the parse stage."""

    PENDING = "PENDING"
    PARSED = "PARSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class RunStatus(StrEnum):
    """Outcome of an ingestion run. ``PARTIAL`` means some items failed but the
    run as a whole produced results."""

    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RunTrigger(StrEnum):
    """What caused an ingestion run to start."""

    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    BACKFILL = "BACKFILL"


class SortOrder(StrEnum):
    """Ordering options exposed by the listing search API."""

    LISTED_AT_DESC = "listed_at_desc"
    LISTED_AT_ASC = "listed_at_asc"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    DISTANCE = "distance"


class LogLevel(StrEnum):
    """Severity levels understood by :class:`~realestate.domain.ports.log_provider.LogProvider`."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @property
    def severity(self) -> int:
        """Numeric rank, matching the stdlib ``logging`` levels."""
        return _LEVEL_SEVERITY[self]


_LEVEL_SEVERITY: dict[LogLevel, int] = {
    LogLevel.DEBUG: 10,
    LogLevel.INFO: 20,
    LogLevel.WARNING: 30,
    LogLevel.ERROR: 40,
    LogLevel.CRITICAL: 50,
}
