"""Zillow adapter -- registered placeholder.

Scaffolded so the source is addressable while unimplemented. Zillow serves its
search results from a JSON blob embedded in the page and guards it behind bot
detection, so this one will most likely end up on
:class:`~realestate.infrastructure.sources.base.BrowserDataSource` with camoufox
rather than on plain HTTP. Either way, only this file changes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import ClassVar

from realestate.domain.exceptions import DataSourceNotImplementedError
from realestate.domain.models import FetchContext, ListingDraft, RawPayload
from realestate.infrastructure.sources.base import HttpDataSource


class ZillowDataSource(HttpDataSource):
    """North American listings portal (zillow.com)."""

    key: ClassVar[str] = "zillow"
    display_name: ClassVar[str] = "Zillow"
    country_code: ClassVar[str] = "US"

    max_concurrency: ClassVar[int] = 2
    min_delay_seconds: ClassVar[float] = 2.0

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
        raise DataSourceNotImplementedError(self.key)
        yield  # pragma: no cover - makes this an async generator

    async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]:
        raise DataSourceNotImplementedError(self.key)

    async def healthcheck(self) -> bool:
        return False
