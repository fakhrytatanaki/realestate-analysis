"""Port for classified-ad data sources.

This is the extension point of the whole system: every portal (dubizzle.eg,
Zillow, ...) implements this one interface, whatever its collection technique --
plain HTTP, a private JSON API, or a stealth browser such as camoufox.

The two stages are separate methods on purpose. :meth:`fetch` only collects
bytes, which the pipeline archives to the blob store before anything is parsed;
:meth:`parse` turns archived bytes into drafts. That split lets a broken
selector be fixed and replayed over stored payloads without re-hitting the site.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import ClassVar

from realestate.domain.archive import ArchiveScope, DiscoveredLink
from realestate.domain.models import FetchContext, ListingDraft, RawPayload


class DataSource(ABC):
    """A single classifieds provider."""

    #: Stable identifier; also the blob key prefix and the API path segment.
    key: ClassVar[str]
    #: Human-readable name for the ``/sources`` endpoint.
    display_name: ClassVar[str]
    #: ISO-3166-1 alpha-2 country this source primarily covers.
    country_code: ClassVar[str]

    @abstractmethod
    def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
        """Yield raw payloads, one per page / API response / rendered document.

        Implemented as an async generator. Must not parse or persist anything:
        failures here mean the site is unreachable, never that a selector broke.

        Sources that continue after individual fetch failures record attempts
        and failures in ``ctx.progress`` so the caller can budget and report them.

        Raises:
            FetchError: when collection fails irrecoverably.
        """

    @abstractmethod
    async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]:
        """Turn one archived payload into normalised drafts.

        Must be pure with respect to the network: given the same bytes it must
        produce the same drafts, so that replays are deterministic.

        Raises:
            ParseError: when the payload cannot be interpreted.
        """

    async def discover_links(self, payload: RawPayload) -> Sequence[DiscoveredLink]:
        """URLs an archived payload points at, for sources that crawl.

        Called by the pipeline after a successful :meth:`parse`, on the same
        archived bytes -- so, like parsing, it must be deterministic and must not
        touch the network. Most sources do not crawl and keep this default.
        """
        return ()

    async def healthcheck(self) -> bool:
        """Whether the source looks reachable. Override where it is cheap to check."""
        return True

    async def aclose(self) -> None:  # noqa: B027 - optional hook, not a requirement
        """Release HTTP clients, browsers, or file handles."""


class ArchiveDataSource(DataSource):
    """A source replayed from a web archive rather than scraped live.

    Its listings are historical observations: capture time, not wall-clock time,
    is what ``observed_at`` means for them.
    """

    @abstractmethod
    def archive_scope(self) -> ArchiveScope:
        """The archived domain and year range this source covers."""
