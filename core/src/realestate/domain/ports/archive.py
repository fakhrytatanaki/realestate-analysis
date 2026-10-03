"""Ports for crawling a web archive.

The archive index (the Wayback CDX API in practice) says *what exists*; the
frontier records *what the crawler decided* about each capture, which makes a
crawl resumable and its decisions inspectable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from realestate.domain.archive import Capture, DiscoveredLink, FrontierEntry, LinkRequest
from realestate.domain.enums import CrawlStatus, LinkRel, LinkRequestStatus, PageKind


@dataclass(frozen=True, slots=True)
class CapturePage:
    """One page of index results plus the key to resume after it."""

    captures: list[Capture]
    resume_key: str | None


class ArchiveIndex(ABC):
    """Enumerates the captures an archive holds for a domain."""

    @abstractmethod
    def iter_captures(
        self,
        domain: str,
        year: int,
        *,
        resume_key: str | None = None,
        page_size: int = 5000,
    ) -> AsyncIterator[CapturePage]:
        """Successful HTML captures of ``domain`` and its subdomains in ``year``.

        Implemented as an async generator; each page carries the resume key for
        the next, so an interrupted enumeration continues where it stopped.
        """

    @abstractmethod
    async def lookup(
        self,
        url: str,
        *,
        around: str | None = None,
        window_days: int = 183,
        limit: int = 5,
    ) -> list[Capture]:
        """Successful HTML captures of exactly ``url``, closest to ``around`` first.

        ``around`` is a capture timestamp (the linking page's); only captures
        within ``window_days`` of it count. One index request per call.
        """


class CrawlFrontierRepository(ABC):
    """Persistent crawl state, one row per capture."""

    @abstractmethod
    async def add_captures(self, source_key: str, captures: Sequence[Capture]) -> int:
        """Insert unseen captures as ``DISCOVERED``; returns how many were new."""

    @abstractmethod
    async def url_keys_with_status(
        self, source_key: str, statuses: Sequence[CrawlStatus], *, limit: int
    ) -> list[str]:
        """Distinct URL keys having at least one capture in ``statuses``."""

    @abstractmethod
    async def captures_for(self, source_key: str, url_keys: Sequence[str]) -> list[FrontierEntry]:
        """Every capture of the given URL keys, oldest first."""

    @abstractmethod
    async def set_route(
        self,
        entry_ids: Sequence[int],
        *,
        status: CrawlStatus,
        route_node: str | None = None,
        priority: int = 0,
        page_kind: PageKind | None = None,
    ) -> None:
        """Record a routing outcome on captures."""

    @abstractmethod
    async def claim_queued(
        self,
        source_key: str,
        *,
        limit: int,
        now: datetime,
        per_quarter: int | None = None,
    ) -> list[FrontierEntry]:
        """Claim captures to fetch next (status ``FETCHING``), in :func:`stratified_order`.

        Skips captures waiting out a retry (``next_retry_at`` after ``now``);
        ``per_quarter`` caps fetched-plus-claimed captures per capture quarter.
        """

    @abstractmethod
    async def release_stale_claims(self, source_key: str, *, claimed_before: datetime) -> int:
        """Return ``FETCHING`` captures claimed before ``claimed_before`` to ``QUEUED``.

        A claim that old belongs to a run that died between fetch and archive.
        """

    @abstractmethod
    async def mark_fetched(self, entry_id: int) -> None:
        """The capture's payload is archived: the claim is complete."""

    @abstractmethod
    async def mark_failed(
        self, entry_id: int, error: str, *, retry_at: datetime | None = None
    ) -> None:
        """Count a failed attempt: back to ``QUEUED`` until ``retry_at``, else ``FAILED``."""

    @abstractmethod
    async def known_url_keys(self, source_key: str, url_keys: Sequence[str]) -> set[str]:
        """Which of ``url_keys`` have at least one capture in the frontier."""

    @abstractmethod
    async def captures_with_status(
        self, source_key: str, statuses: Sequence[CrawlStatus]
    ) -> list[tuple[int, str]]:
        """``(id, timestamp)`` of every capture in ``statuses`` (exploration samples)."""

    @abstractmethod
    async def reopen_capped(self, source_key: str) -> int:
        """Send captures skipped by the per-URL capture cap back to routing."""

    @abstractmethod
    async def status_by_quarter(self, source_key: str) -> dict[tuple[str, CrawlStatus], int]:
        """Capture counts per ``(capture quarter, status)``, for coverage reports."""

    @abstractmethod
    async def add_evidence(self, source_key: str, rels_by_url_key: Mapping[str, LinkRel]) -> int:
        """Note that a recognised page links to these URL keys.

        Captures that gain new evidence and are not yet queued or fetched go
        back to ``DISCOVERED`` so routing reconsiders them. Returns the number
        of URL keys that matched a known capture.
        """

    @abstractmethod
    async def reset_status(
        self, source_key: str, *, from_status: CrawlStatus, to_status: CrawlStatus
    ) -> int:
        """Move every capture in one status to another (e.g. retry unrouted)."""

    @abstractmethod
    async def counts(self, source_key: str) -> dict[CrawlStatus, int]:
        """Captures per status."""

    @abstractmethod
    async def spread_urls(self, source_key: str, *, limit: int) -> list[str]:
        """About ``limit`` original URLs spread evenly over the whole frontier, any status.

        A picture of the site's URL space, e.g. to tell how far a proposed
        routing rule reaches beyond the URLs it was written for.
        """

    @abstractmethod
    async def sample_urls(self, source_key: str, status: CrawlStatus, *, limit: int) -> list[str]:
        """Original URLs of distinct URL keys in ``status``."""


class CrawlCursorRepository(ABC):
    """Where each enumeration scope (e.g. one year of the index) got to."""

    @abstractmethod
    async def get(self, source_key: str, scope: str) -> tuple[str | None, bool]:
        """``(resume_key, done)``; ``(None, False)`` when never started."""

    @abstractmethod
    async def save(
        self, source_key: str, scope: str, *, resume_key: str | None, done: bool
    ) -> None:
        """Persist progress."""


class LinkRequestRepository(ABC):
    """Linked URLs awaiting an exact-URL index lookup."""

    @abstractmethod
    async def request(self, requests: Sequence[LinkRequest]) -> int:
        """Record new requests (a URL already requested is left alone); returns how many."""

    @abstractmethod
    async def pending(self, source_key: str, *, limit: int) -> list[LinkRequest]:
        """Pending requests, detail links first, then oldest."""

    @abstractmethod
    async def resolve(
        self,
        request_id: int,
        *,
        status: LinkRequestStatus,
        captures_found: int = 0,
        error: str | None = None,
    ) -> None:
        """Record a lookup's outcome; ``PENDING`` counts a failed attempt for retry."""

    @abstractmethod
    async def counts(self, source_key: str) -> dict[LinkRequestStatus, int]: ...


class LinkSink(ABC):
    """Receives links found on recognised pages during parsing."""

    @abstractmethod
    async def offer(
        self,
        source_key: str,
        links: Sequence[DiscoveredLink],
        *,
        parent_timestamp: str | None = None,
    ) -> int:
        """Record the links; returns how many matched something crawlable.

        ``parent_timestamp`` is the capture time of the linking page, used to
        look up links the frontier has never seen near that moment.
        """
