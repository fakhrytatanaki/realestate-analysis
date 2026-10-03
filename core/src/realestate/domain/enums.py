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
    #: The source gave no usable figure -- distinct from ``ON_REQUEST``, where the
    #: seller deliberately withheld it. Common in archived, partial adverts.
    UNKNOWN = "UNKNOWN"


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
    #: No extraction rule recognised the document yet. Not an error: rule
    #: induction turns these into new rules, after which they are re-parsed.
    UNRECOGNISED = "UNRECOGNISED"


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
    #: Re-parsing archived payloads (reparse, rebuild); fetches nothing.
    REPLAY = "REPLAY"


class SortOrder(StrEnum):
    """Ordering options exposed by the listing search API."""

    LISTED_AT_DESC = "listed_at_desc"
    LISTED_AT_ASC = "listed_at_asc"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    DISTANCE = "distance"


class PageKind(StrEnum):
    """What an archived page is, as far as listing extraction is concerned."""

    LIST = "LIST"
    DETAIL = "DETAIL"
    #: Recognised, but carries no listings (help pages, other categories, ...).
    OTHER = "OTHER"


class CrawlStatus(StrEnum):
    """Lifecycle of one archived capture in the crawl frontier."""

    #: Enumerated from the archive index, not yet routed.
    DISCOVERED = "DISCOVERED"
    #: No navigation rule matched; waiting for rule induction.
    UNROUTED = "UNROUTED"
    QUEUED = "QUEUED"
    #: Claimed by a fetch; becomes FETCHED once its payload is archived, or
    #: returns to QUEUED if the claim goes stale (an interrupted run).
    FETCHING = "FETCHING"
    #: Ambiguous; fetched only if a recognised page later links to it.
    DEFERRED = "DEFERRED"
    SKIPPED = "SKIPPED"
    FETCHED = "FETCHED"
    FAILED = "FAILED"


class RouteDecision(StrEnum):
    """What a navigation rule says to do with a URL."""

    FETCH = "FETCH"
    SKIP = "SKIP"
    DEFER = "DEFER"


class LinkRel(StrEnum):
    """How a recognised page refers to another URL."""

    DETAIL = "DETAIL"
    LIST = "LIST"
    PAGINATION = "PAGINATION"


class RuleDomain(StrEnum):
    """Which decision a rule graph caches."""

    NAVIGATION = "NAVIGATION"
    EXTRACTION = "EXTRACTION"


class RuleGraphStatus(StrEnum):
    """Lifecycle of one immutable rule graph version."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class NodeKind(StrEnum):
    """States of a rule graph.

    ``ROOT`` and ``BRANCH`` are classification states whose outgoing edges are
    tried in priority order; ``ROUTE`` and ``TEMPLATE`` are terminal states that
    carry the action -- a navigation decision or an extraction recipe.
    """

    ROOT = "ROOT"
    BRANCH = "BRANCH"
    ROUTE = "ROUTE"
    TEMPLATE = "TEMPLATE"


class RuleOrigin(StrEnum):
    """Who authored a rule node."""

    SEED = "SEED"
    LLM = "LLM"
    HUMAN = "HUMAN"


class LinkRequestStatus(StrEnum):
    """A link to a URL the frontier had no capture of, awaiting an exact CDX lookup."""

    PENDING = "PENDING"
    #: The archive has captures near the linking page's time; added to the frontier.
    FOUND = "FOUND"
    #: The archive has no capture of it in the window.
    NONE = "NONE"
    FAILED = "FAILED"


class GapStatus(StrEnum):
    """Lifecycle of a cluster of inputs no rule handled."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    #: Induction kept failing validation; left for a human or a better model.
    FAILED = "FAILED"


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
