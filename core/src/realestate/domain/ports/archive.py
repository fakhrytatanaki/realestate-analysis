"""Ports for crawling a web archive.

The archive index (the Wayback CDX API in practice) says *what exists*; the
frontier records *what the crawler decided* about each capture, which makes a
crawl resumable and its decisions inspectable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass

from realestate.domain.archive import Capture, DiscoveredLink, FrontierEntry
from realestate.domain.enums import CrawlStatus, LinkRel, PageKind


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
    async def next_queued(self, source_key: str, *, limit: int) -> list[FrontierEntry]:
        """Captures to fetch next: highest priority first, then oldest."""

    @abstractmethod
    async def mark_fetched(self, entry_id: int) -> None: ...

    @abstractmethod
    async def mark_failed(self, entry_id: int, error: str) -> None: ...

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


class LinkSink(ABC):
    """Receives links found on recognised pages during parsing."""

    @abstractmethod
    async def offer(self, source_key: str, links: Sequence[DiscoveredLink]) -> int:
        """Record the links; returns how many matched something crawlable."""
