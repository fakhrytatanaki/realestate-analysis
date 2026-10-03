"""Framework-free domain objects.

Nothing in this module may import fastapi, pydantic or tortoise: these types are
the common currency between the scraping side and the query side, and keeping
them plain makes both trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from realestate.domain.archive import parse_timestamp
from realestate.domain.enums import (
    ListingType,
    PriceType,
    PropertyType,
    RawDocumentKind,
    RawDocumentStatus,
    RunStatus,
    RunTrigger,
)


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """WGS-84 coordinate pair."""

    latitude: Decimal
    longitude: Decimal

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude {self.latitude} out of range")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude {self.longitude} out of range")


@dataclass(frozen=True, slots=True)
class Location:
    """Where a listing is. Free-text plus optional coordinates, because most
    sources give a human label and only sometimes a precise point."""

    name: str
    country_code: str | None = None
    city: str | None = None
    district: str | None = None
    point: GeoPoint | None = None


@dataclass(frozen=True, slots=True)
class Price:
    """A money figure together with the reading instructions for it.

    ``amount`` is ``None`` for :attr:`PriceType.ON_REQUEST` listings.
    ``installment_plan`` carries the extra terms when ``price_type`` is
    :attr:`PriceType.INSTALLMENT`.
    """

    amount: Decimal | None
    currency: str
    price_type: PriceType = PriceType.TOTAL
    installment_plan: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class BlobRef:
    """Handle to a stored object, returned by every :class:`BlobProvider`."""

    key: str
    uri: str
    size_bytes: int
    sha256: str
    content_type: str


@dataclass(frozen=True, slots=True)
class FetchContext:
    """Knobs handed to a data source for one collection pass.

    ``params`` carries source-specific settings straight from
    ``etc/settings.toml`` so the generic pipeline never needs to know about them.
    """

    max_items: int | None = None
    page_limit: int | None = None
    since: datetime | None = None
    cursor: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawPayload:
    """One unit of collected data, exactly as the source produced it.

    ``content`` is deliberately ``bytes`` so an HTML page, a JSON API response
    and a browser screenshot all travel through the same pipe.
    """

    content: bytes
    kind: RawDocumentKind
    content_type: str
    source_url: str | None = None
    external_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ListingDraft:
    """The normalisation contract every data source must satisfy.

    A draft is a listing that has been parsed but not yet persisted; it carries
    no database identity, only the source's own ``external_id``.
    """

    external_id: str
    title: str
    listing_type: ListingType
    property_type: PropertyType
    location: Location
    price: Price
    url: str | None = None
    description: str | None = None
    area_sqm: Decimal | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    listed_at: datetime | None = None
    is_active: bool = True
    attributes: dict[str, Any] = field(default_factory=dict)
    #: When the advert was observed in this state. Archive sources set it to the
    #: capture time; ``None`` means "now", which is right for live scrapes.
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Listing:
    """A persisted, aggregated listing as read back from storage."""

    id: UUID
    source_key: str
    external_id: str
    title: str
    listing_type: ListingType
    property_type: PropertyType
    location: Location
    price: Price
    url: str | None
    description: str | None
    area_sqm: Decimal | None
    bedrooms: int | None
    bathrooms: int | None
    listed_at: datetime | None
    is_active: bool
    attributes: dict[str, Any]
    content_hash: str
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
    raw_document_id: UUID | None = None
    # Populated only by radius searches; not a stored column.
    distance_km: float | None = None
    #: Earliest / latest observation time; capture times for archive sources.
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RawDocument:
    """Metadata for one archived payload. The bytes live in the blob store."""

    id: UUID
    source_key: str
    kind: RawDocumentKind
    status: RawDocumentStatus
    blob_key: str
    blob_uri: str
    content_type: str
    size_bytes: int
    sha256: str
    fetched_at: datetime
    external_id: str | None = None
    source_url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    parsed_at: datetime | None = None
    parse_error: str | None = None
    attempts: int = 0
    scrape_run_id: UUID | None = None
    #: Extraction graph version of the last parse (archive sources only).
    graph_version: int | None = None
    parse_report: dict[str, Any] | None = None

    @property
    def captured_at(self) -> datetime | None:
        """Capture time for archived payloads (``meta``), else ``None``."""
        meta = self.meta or {}
        if meta.get("captured_at"):
            try:
                return datetime.fromisoformat(str(meta["captured_at"]))
            except ValueError:
                pass
        if meta.get("timestamp"):
            try:
                return parse_timestamp(str(meta["timestamp"]))
            except ValueError:
                return None
        return None


@dataclass(frozen=True, slots=True)
class ParseReport:
    """What parsing one archived document saw, beyond its drafts.

    Status ``PARSED`` only says a rule matched; this says how well: which
    template, how many items it examined and kept, why it dropped the rest,
    and whether a page routed as a list or detail page came out as ``OTHER``.
    """

    graph_version: int | None
    template_key: str | None = None
    #: Who wrote the winning template (``LLM``, ``HUMAN``, ``SEED``).
    template_origin: str | None = None
    page_kind: str | None = None
    items_total: int = 0
    items_valid: int = 0
    problems: tuple[str, ...] = ()
    empty_fields: tuple[str, ...] = ()
    #: Routed as LIST/DETAIL but recognised as OTHER (or as the other kind).
    hint_mismatch: bool = False

    @property
    def identity_problems(self) -> int:
        return sum(1 for problem in self.problems if "identity" in problem)

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph_version": self.graph_version,
            "template_key": self.template_key,
            "template_origin": self.template_origin,
            "page_kind": self.page_kind,
            "items_total": self.items_total,
            "items_valid": self.items_valid,
            "problems": list(self.problems[:20]),
            "problem_count": len(self.problems),
            "identity_problems": self.identity_problems,
            "empty_fields": list(self.empty_fields),
            "hint_mismatch": self.hint_mismatch,
        }


@dataclass(frozen=True, slots=True)
class ScrapeRun:
    """One execution of the ingestion pipeline for a single source."""

    id: UUID
    source_key: str
    trigger: RunTrigger
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    documents_fetched: int = 0
    listings_created: int = 0
    listings_updated: int = 0
    errors: int = 0
    error_message: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """Outcome of writing a batch of drafts."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0

    def __add__(self, other: UpsertResult) -> UpsertResult:
        return UpsertResult(
            created=self.created + other.created,
            updated=self.updated + other.updated,
            unchanged=self.unchanged + other.unchanged,
        )


@dataclass(frozen=True, slots=True)
class Page[T]:
    """A slice of results plus the total matching count."""

    items: list[T]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """What the registry knows about one data source, for the ``/sources`` API."""

    key: str
    display_name: str
    country_code: str
    enabled: bool
    implemented: bool
    interval_minutes: float | None = None
    crontab: str | None = None


@dataclass(frozen=True, slots=True)
class User:
    """An account that may sign in to the app.

    ``email`` is stored normalised (stripped, lower-cased) so lookups are exact.
    """

    id: UUID
    email: str
    display_name: str
    password_hash: str
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Session:
    """A signed-in browser session.

    Only the sha256 of the bearer token is kept: a leaked row cannot be replayed,
    and the raw token exists solely in the client's cookie.
    """

    id: UUID
    user_id: UUID
    token_hash: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime
    user_agent: str | None = None
