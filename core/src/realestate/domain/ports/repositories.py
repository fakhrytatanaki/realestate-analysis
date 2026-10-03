"""Persistence ports.

Defined in terms of domain objects only, so the application layer can be tested
against in-memory fakes and the Tortoise implementation stays swappable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from realestate.domain.enums import RawDocumentStatus, RunStatus, RunTrigger
from realestate.domain.models import (
    BlobRef,
    Listing,
    ListingDraft,
    Page,
    RawDocument,
    RawPayload,
    ScrapeRun,
    UpsertResult,
)
from realestate.domain.query import ListingQuery


class ListingRepository(ABC):
    """Reads and writes aggregated listings."""

    @abstractmethod
    async def upsert_many(
        self,
        drafts: Sequence[ListingDraft],
        *,
        source_key: str,
        raw_document_id: UUID | None = None,
    ) -> UpsertResult:
        """Insert or update drafts, keyed on ``(source_key, external_id)``.

        A draft whose content hash matches the stored row counts as *unchanged*:
        only ``last_seen_at`` is touched, leaving ``updated_at`` alone so genuine
        changes remain visible.
        """

    @abstractmethod
    async def search(self, query: ListingQuery) -> Page[Listing]:
        """Return the matching page plus the total count."""

    @abstractmethod
    async def get(self, listing_id: UUID) -> Listing | None:
        """Fetch one listing by primary key."""

    @abstractmethod
    async def mark_stale(self, source_key: str, *, not_seen_since: datetime) -> int:
        """Deactivate listings from ``source_key`` missing since ``not_seen_since``.

        Returns the number of rows deactivated.
        """

    @abstractmethod
    async def count(self, source_key: str | None = None) -> int:
        """Total listings, optionally restricted to one source."""


class RawDocumentRepository(ABC):
    """Tracks archived payloads and their parse state."""

    @abstractmethod
    async def create(
        self,
        *,
        source_key: str,
        payload: RawPayload,
        blob: BlobRef,
        scrape_run_id: UUID | None = None,
    ) -> RawDocument:
        """Record a freshly archived payload as ``PENDING``."""

    @abstractmethod
    async def get(self, document_id: UUID) -> RawDocument | None:
        """Fetch one document's metadata."""

    @abstractmethod
    async def list_by_status(
        self,
        *,
        source_key: str | None = None,
        status: RawDocumentStatus = RawDocumentStatus.PENDING,
        limit: int = 100,
        fetched_after: datetime | None = None,
    ) -> list[RawDocument]:
        """Documents in a given parse state, oldest first.

        Passing ``PARSED`` is how a replay re-reads already-processed payloads.
        """

    @abstractmethod
    async def mark_parsed(self, document_id: UUID) -> None:
        """Flag a document as successfully parsed."""

    @abstractmethod
    async def mark_failed(self, document_id: UUID, error: str) -> None:
        """Flag a document as unparseable and store the reason."""

    @abstractmethod
    async def mark_unrecognised(self, document_id: UUID, reason: str) -> None:
        """Flag a document no extraction rule recognised yet (not a failure)."""

    @abstractmethod
    async def find_by_sha256(self, source_key: str, sha256: str) -> RawDocument | None:
        """Look up an identical earlier payload, to skip redundant work."""


class ScrapeRunRepository(ABC):
    """Ingestion run history."""

    @abstractmethod
    async def start(self, source_key: str, trigger: RunTrigger) -> ScrapeRun:
        """Open a ``RUNNING`` record."""

    @abstractmethod
    async def finish(
        self,
        run_id: UUID,
        *,
        status: RunStatus,
        documents_fetched: int = 0,
        listings_created: int = 0,
        listings_updated: int = 0,
        errors: int = 0,
        error_message: str | None = None,
    ) -> ScrapeRun:
        """Close a run with its final counters."""

    @abstractmethod
    async def get(self, run_id: UUID) -> ScrapeRun | None:
        """Fetch one run."""

    @abstractmethod
    async def list(
        self,
        *,
        source_key: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Page[ScrapeRun]:
        """Runs, newest first."""

    @abstractmethod
    async def latest_per_source(self) -> dict[str, ScrapeRun]:
        """Most recent run for each source, keyed by source key."""
