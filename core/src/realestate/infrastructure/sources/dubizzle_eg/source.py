"""dubizzle.eg adapter -- registered placeholder.

Scaffolded so the source shows up in ``/sources`` and the pipeline can address
it, but every collection path fails loudly rather than pretending to work.

To implement: replace :meth:`fetch` with paginated calls to the search endpoint
via ``self.request`` (the base handles rate limiting and retries), and
:meth:`parse` with the mapping from their payload to
:class:`~realestate.domain.models.ListingDraft`. If the endpoint turns out to be
bot-protected, switch the base class to
:class:`~realestate.infrastructure.sources.base.BrowserDataSource` and drive
camoufox instead -- the pipeline does not care which you pick.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import ClassVar

from realestate.domain.exceptions import DataSourceNotImplementedError
from realestate.domain.models import FetchContext, ListingDraft, RawPayload
from realestate.infrastructure.sources.base import HttpDataSource


class DubizzleEgDataSource(HttpDataSource):
    """Egyptian classifieds portal (dubizzle.com.eg)."""

    key: ClassVar[str] = "dubizzle_eg"
    display_name: ClassVar[str] = "Dubizzle Egypt"
    country_code: ClassVar[str] = "EG"

    #: Egyptian portals are rate-sensitive; start conservative.
    max_concurrency: ClassVar[int] = 2
    min_delay_seconds: ClassVar[float] = 1.5

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
        raise DataSourceNotImplementedError(self.key)
        yield  # pragma: no cover - makes this an async generator

    async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]:
        raise DataSourceNotImplementedError(self.key)

    async def healthcheck(self) -> bool:
        return False
