"""Wayback Machine access: CDX enumeration and raw capture replay.

Politeness is not optional here. The Internet Archive throttles aggressive
clients (429s, then temporary IP blocks) and publishes no exact limits, so this
client is single-flight by default, keeps a minimum delay between requests,
honours ``Retry-After``, and backs off for minutes rather than seconds on 429.
It presents one fixed, honest User-Agent -- rotating agents against a
non-profit archive would be both rude and pointless.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from realestate.domain.archive import Capture, parse_timestamp
from realestate.domain.exceptions import FetchError
from realestate.domain.ports.archive import ArchiveIndex, CapturePage
from realestate.domain.ports.log_provider import LogProvider

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
REPLAY_PREFIX = "https://web.archive.org/web"
#: Override per source with ``params.user_agent``, ideally adding a contact address.
DEFAULT_USER_AGENT = "realestatepy-archive-research/0.1 (historical classifieds study)"


@dataclass(frozen=True, slots=True)
class WaybackSettings:
    user_agent: str = DEFAULT_USER_AGENT
    min_delay_seconds: float = 1.5
    max_retries: int = 5
    timeout_seconds: float = 90.0
    #: First wait after a 429 without ``Retry-After``; doubles up to the cap.
    rate_limit_cooldown_seconds: float = 60.0
    max_cooldown_seconds: float = 900.0

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> WaybackSettings:
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in params.items() if key in known})


@dataclass(frozen=True, slots=True)
class ArchivedResponse:
    content: bytes
    content_type: str
    captured_at: datetime
    timestamp: str
    original_url: str
    replay_url: str


class WaybackClient:
    """Shared HTTP plumbing for the CDX API and capture replay."""

    def __init__(
        self,
        settings: WaybackSettings | None = None,
        *,
        log: LogProvider,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings or WaybackSettings()
        self._log = log
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._cooldown = self._settings.rate_limit_cooldown_seconds

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": self._settings.user_agent, "Accept-Encoding": "gzip"},
                transport=self._transport,
            )
        return self._client

    async def get(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        """One polite GET. 404 raises immediately; 429/5xx/network errors retry.

        Raises:
            FetchError: when the request cannot succeed.
        """
        last_error = "no attempt made"
        for attempt in range(1, self._settings.max_retries + 1):
            async with self._lock:  # single flight, and the delay is per client
                wait = self._settings.min_delay_seconds - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    response = await self.client.get(url, params=params)
                except httpx.HTTPError as exc:
                    response = None
                    last_error = f"{type(exc).__name__}: {exc}"
                finally:
                    self._last_request_at = time.monotonic()

            if response is not None:
                if response.status_code == 200:
                    self._cooldown = self._settings.rate_limit_cooldown_seconds
                    return response
                if response.status_code in (403, 404, 410):
                    raise FetchError(f"{response.status_code} for {response.url}")
                last_error = f"HTTP {response.status_code}"
                if response.status_code == 429:
                    pause = _retry_after(response) or self._cooldown
                    self._cooldown = min(self._cooldown * 2, self._settings.max_cooldown_seconds)
                    await self._log.warning(
                        "archive rate limit, cooling down", url=url, seconds=round(pause)
                    )
                    await asyncio.sleep(pause)
                    continue
            if attempt < self._settings.max_retries:
                backoff = min(self._settings.min_delay_seconds * 2**attempt, 120.0)
                await self._log.warning(
                    "archive request failed, retrying",
                    url=url,
                    attempt=attempt,
                    error=last_error,
                    backoff_seconds=round(backoff, 1),
                )
                await asyncio.sleep(backoff)
        raise FetchError(f"{url} failed after {self._settings.max_retries} attempts: {last_error}")

    async def fetch_capture(self, timestamp: str, original_url: str) -> ArchivedResponse:
        """The original bytes of one capture (``id_`` replay: no toolbar, no rewriting).

        The archive may redirect to the nearest capture; the timestamp actually
        served is read back from ``Memento-Datetime`` or the final URL.
        """
        replay_url = f"{REPLAY_PREFIX}/{timestamp}id_/{original_url}"
        response = await self.get(replay_url)
        served = _served_timestamp(response) or timestamp
        return ArchivedResponse(
            content=response.content,
            content_type=response.headers.get("content-type", "text/html"),
            captured_at=parse_timestamp(served),
            timestamp=served,
            original_url=original_url,
            replay_url=str(response.url),
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(float(value), 1.0)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        return max((moment - datetime.now(moment.tzinfo)).total_seconds(), 1.0)


def _served_timestamp(response: httpx.Response) -> str | None:
    memento = response.headers.get("memento-datetime")
    if memento:
        try:
            return parsedate_to_datetime(memento).strftime("%Y%m%d%H%M%S")
        except (TypeError, ValueError):
            pass
    path = response.url.path
    marker = "/web/"
    if marker in path:
        candidate = path.split(marker, 1)[1].split("/", 1)[0].removesuffix("id_")
        if candidate.isdigit() and len(candidate) == 14:
            return candidate
    return None


class WaybackCdxIndex(ArchiveIndex):
    """The CDX API, paged with resume keys (``page=`` paging is unreliable with filters)."""

    def __init__(self, client: WaybackClient) -> None:
        self._client = client

    async def iter_captures(
        self,
        domain: str,
        year: int,
        *,
        resume_key: str | None = None,
        page_size: int = 5000,
    ) -> AsyncIterator[CapturePage]:
        while True:
            params: dict[str, Any] = {
                "url": domain,
                "matchType": "domain",
                "from": str(year),
                "to": str(year),
                "filter": ["statuscode:200", "mimetype:text/html"],
                "fl": "urlkey,timestamp,original,digest",
                "output": "json",
                "limit": str(page_size),
                "showResumeKey": "true",
            }
            if resume_key:
                params["resumeKey"] = resume_key
            response = await self._client.get(CDX_ENDPOINT, params=params)
            captures, next_key = parse_cdx_json(response.json() if response.content.strip() else [])
            yield CapturePage(captures=captures, resume_key=next_key)
            if not next_key or not captures:
                return
            resume_key = next_key


def parse_cdx_json(rows: list[list[str]]) -> tuple[list[Capture], str | None]:
    """CDX JSON rows -> captures plus the resume key.

    Layout: a header row, data rows, then (with ``showResumeKey``) an empty row
    and a one-element row holding the key.
    """
    if not rows:
        return [], None
    header, body = rows[0], rows[1:]
    resume_key: str | None = None
    if len(body) >= 2 and body[-2] == [] and len(body[-1]) == 1:
        resume_key = body[-1][0]
        body = body[:-2]
    index = {name: position for position, name in enumerate(header)}
    captures: list[Capture] = []
    for row in body:
        if len(row) < len(header):
            continue
        captures.append(
            Capture(
                url_key=row[index["urlkey"]],
                timestamp=row[index["timestamp"]],
                original_url=row[index["original"]],
                digest=row[index.get("digest", index["timestamp"])],
            )
        )
    return captures, resume_key
