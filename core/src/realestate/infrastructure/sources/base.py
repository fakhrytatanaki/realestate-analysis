"""Reusable bases for concrete data sources.

Every portal implements :class:`~realestate.domain.ports.data_source.DataSource`,
but they cluster into a few collection techniques. These bases carry the
plumbing each technique needs so a new source only writes its own selectors.
"""

from __future__ import annotations

import asyncio
import random
import time
from abc import abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, ClassVar

import httpx

from realestate.domain.exceptions import FetchError
from realestate.domain.models import FetchContext, ListingDraft, RawPayload
from realestate.domain.ports.data_source import DataSource
from realestate.domain.ports.log_provider import LogProvider

#: Rotated on each request so a source does not present one fingerprint forever.
DEFAULT_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
)


class HttpDataSource(DataSource):
    """Base for sources collected over plain HTTP -- pages or private JSON APIs.

    Provides one lazily-created shared client, a concurrency cap, a minimum
    delay between requests, and retry with exponential backoff. Subclasses
    implement :meth:`fetch` and :meth:`parse` and call :meth:`request`.
    """

    #: Politeness settings; override per source.
    max_concurrency: ClassVar[int] = 4
    min_delay_seconds: ClassVar[float] = 0.5
    max_retries: ClassVar[int] = 3
    timeout_seconds: ClassVar[float] = 30.0
    #: Status codes worth retrying: transient server and rate-limit errors.
    retry_statuses: ClassVar[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

    def __init__(self, *, log: LogProvider, params: Mapping[str, Any] | None = None) -> None:
        self._log = log.bind(source_key=self.key)
        self._params = dict(params or {})
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._last_request_at = 0.0
        self._delay_lock = asyncio.Lock()

    @property
    def client(self) -> httpx.AsyncClient:
        """The shared client, created on first use inside the running loop."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers=self.default_headers(),
            )
        return self._client

    def default_headers(self) -> dict[str, str]:
        """Headers applied to every request; override to add site-specific ones."""
        return {
            "User-Agent": random.choice(DEFAULT_USER_AGENTS),
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Perform one rate-limited, retrying request.

        Raises:
            FetchError: once the retry budget is exhausted.
        """
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            async with self._semaphore:
                await self._respect_delay()
                try:
                    response = await self.client.request(
                        method, url, headers=self.default_headers(), **kwargs
                    )
                except httpx.HTTPError as exc:
                    last_error = exc
                else:
                    if response.status_code not in self.retry_statuses:
                        response.raise_for_status()
                        return response
                    last_error = httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )

            if attempt < self.max_retries:
                backoff = self.min_delay_seconds * (2 ** (attempt - 1))
                await self._log.warning(
                    "request failed, retrying",
                    url=url,
                    attempt=attempt,
                    backoff_seconds=round(backoff, 2),
                    error=str(last_error),
                )
                await asyncio.sleep(backoff)

        raise FetchError(
            f"{self.key}: {method} {url} failed after {self.max_retries} attempts: {last_error}"
        ) from last_error

    async def _respect_delay(self) -> None:
        """Keep at least ``min_delay_seconds`` between consecutive requests."""
        async with self._delay_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.min_delay_seconds:
                await asyncio.sleep(self.min_delay_seconds - elapsed)
            self._last_request_at = time.monotonic()

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class BrowserDataSource(DataSource):
    """Base for sources that need a real browser (camoufox, Playwright, ...).

    The lifecycle is declared here so the ingestion pipeline treats browser-based
    sources exactly like HTTP ones. Wiring an actual driver means overriding
    :meth:`_launch` and :meth:`_render`; until then they raise, which is honest
    rather than silently returning nothing.
    """

    #: Kept for the eventual driver; harmless while unimplemented.
    headless: ClassVar[bool] = True
    navigation_timeout_seconds: ClassVar[float] = 45.0

    def __init__(self, *, log: LogProvider, params: Mapping[str, Any] | None = None) -> None:
        self._log = log.bind(source_key=self.key)
        self._params = dict(params or {})
        self._browser: Any | None = None

    async def _launch(self) -> Any:
        """Start the browser and return the driver handle."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _launch() to start a browser driver"
        )

    async def _render(self, url: str, *, wait_for: str | None = None) -> str:
        """Navigate to ``url`` and return the settled HTML."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _render() to return page HTML"
        )

    @abstractmethod
    def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]: ...

    @abstractmethod
    async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]: ...

    async def aclose(self) -> None:
        if self._browser is not None:
            close = getattr(self._browser, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            self._browser = None
