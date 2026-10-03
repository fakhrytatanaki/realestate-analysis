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
import re
import time
from collections.abc import AsyncIterator, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from realestate.domain.archive import Capture, parse_timestamp
from realestate.domain.exceptions import FetchError
from realestate.domain.ports.archive import ArchiveIndex, CapturePage
from realestate.domain.ports.log_provider import LogProvider
from realestate.infrastructure.archive.rate_gate import RateGate

#: Name of the shared rate gate every client of the archive draws slots from.
ARCHIVE_GATE = "web.archive.org"
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
    requested_timestamp: str
    requested_original_url: str
    requested_replay_url: str
    redirect_chain: tuple[str, ...]
    timestamp_source: str
    replay_timestamp: str | None


class WaybackClient:
    """Shared HTTP plumbing for the CDX API and capture replay."""

    def __init__(
        self,
        settings: WaybackSettings | None = None,
        *,
        log: LogProvider,
        transport: httpx.AsyncBaseTransport | None = None,
        gate: RateGate | None = None,
    ) -> None:
        self._gate = gate
        self._settings = settings or WaybackSettings()
        self._log = log
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._cooldown = self._settings.rate_limit_cooldown_seconds

    @property
    def log(self) -> LogProvider:
        return self._log

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": self._settings.user_agent, "Accept-Encoding": "gzip"},
                transport=self._transport,
            )
        return self._client

    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        validate_redirect: Callable[[httpx.URL], None] | None = None,
    ) -> httpx.Response:
        """One polite GET. 404 raises immediately; 429/5xx/network errors retry.

        Raises:
            FetchError: when the request cannot succeed.
        """
        last_error = "no attempt made"
        for attempt in range(1, self._settings.max_retries + 1):
            started = time.monotonic()
            try:
                response = await self._get_redirects(url, params, validate_redirect)
            except httpx.HTTPError as exc:
                response = None
                last_error = f"{type(exc).__name__}: {exc}"
            await self._log.debug(
                "archive request",
                url=str(response.url) if response is not None else url,
                status=response.status_code if response is not None else last_error,
                bytes=len(response.content) if response is not None else 0,
                ms=int((time.monotonic() - started) * 1000),
                attempt=attempt,
            )

            if response is not None:
                if response.status_code == 200:
                    self._cooldown = self._settings.rate_limit_cooldown_seconds
                    return response
                if response.status_code in (403, 404, 410):
                    raise FetchError(f"{response.status_code} for {response.url}", retryable=False)
                last_error = f"HTTP {response.status_code}"
                if response.status_code == 429:
                    pause = _retry_after(response) or self._cooldown
                    self._cooldown = min(self._cooldown * 2, self._settings.max_cooldown_seconds)
                    await self._log.warning(
                        "archive rate limit, cooling down", url=url, seconds=round(pause)
                    )
                    if self._gate is not None:
                        await self._gate.hold(ARCHIVE_GATE, seconds=pause)
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

    async def _get_redirects(
        self,
        url: str,
        params: dict[str, Any] | None,
        validate_redirect: Callable[[httpx.URL], None] | None,
    ) -> httpx.Response:
        request = self.client.build_request("GET", url, params=params)
        history: list[httpx.Response] = []
        while True:
            _validate_archive_url(request.url)
            if validate_redirect is not None:
                validate_redirect(request.url)
            async with self._lock:  # every hop participates in single-flight throttling
                if self._gate is not None:
                    wait = await self._gate.reserve(
                        ARCHIVE_GATE, interval=self._settings.min_delay_seconds
                    )
                else:
                    wait = self._settings.min_delay_seconds - (
                        time.monotonic() - self._last_request_at
                    )
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    response = await self.client.send(request, follow_redirects=False)
                finally:
                    self._last_request_at = time.monotonic()
            response.history = list(history)
            if response.next_request is None:
                return response
            if len(history) >= self.client.max_redirects:
                raise FetchError(f"too many archive redirects for {url}", retryable=False)
            history.append(response)
            request = response.next_request

    async def fetch_capture(
        self,
        timestamp: str,
        original_url: str,
        *,
        allowed_hosts: Collection[str] | None = None,
    ) -> ArchivedResponse:
        """The original bytes of one capture (``id_`` replay: no toolbar, no rewriting).

        Every redirect is checked before I/O. The aware ``Memento-Datetime``
        takes precedence over a complete final replay timestamp; a short prefix
        alone is not proof. Preserve both URLs/times and the proof source.
        """
        try:
            host = urlsplit(original_url).hostname or ""
        except ValueError as exc:
            raise FetchError(
                f"invalid original capture URL: {original_url}", retryable=False
            ) from exc
        domain = host.removeprefix("www.")
        hosts = set(allowed_hosts) if allowed_hosts is not None else {domain, f"www.{domain}"}

        def validate(url: httpx.URL) -> None:
            _replay_location(url, hosts)

        replay_url = f"{REPLAY_PREFIX}/{timestamp}id_/{original_url}"
        response = await self.get(replay_url, validate_redirect=validate)
        replay_timestamp, served_url = _replay_location(response.url, hosts)
        proof = _served_timestamp(response)
        if proof is None:
            raise FetchError(
                f"no trustworthy served capture timestamp for {response.url}", retryable=False
            )
        served, timestamp_source = proof
        return ArchivedResponse(
            content=response.content,
            content_type=response.headers.get("content-type", "text/html"),
            captured_at=parse_timestamp(served),
            timestamp=served,
            original_url=served_url,
            replay_url=str(response.url),
            requested_timestamp=timestamp,
            requested_original_url=original_url,
            requested_replay_url=replay_url,
            redirect_chain=tuple(str(item.url) for item in [*response.history, response]),
            timestamp_source=timestamp_source,
            replay_timestamp=replay_timestamp if len(replay_timestamp) == 14 else None,
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


def _validate_archive_url(url: httpx.URL) -> None:
    if (
        url.scheme not in {"http", "https"}
        or url.host != "web.archive.org"
        or url.port not in {None, 80, 443}
        or url.userinfo
    ):
        raise FetchError(f"redirect/request escaped Wayback: {url}", retryable=False)


_REPLAY_PATH = re.compile(r"^/web/([0-9]{1,14})id_/(https?://.+)$")


def _replay_location(url: httpx.URL, allowed_hosts: Collection[str]) -> tuple[str, str]:
    """Validate the embedded original before requesting any replay redirect."""
    parts = urlsplit(str(url))
    match = _REPLAY_PATH.fullmatch(parts.path + (f"?{parts.query}" if parts.query else ""))
    if match is None:
        raise FetchError(f"not an original-byte Wayback replay URL: {url}", retryable=False)
    timestamp, original = match.groups()
    try:
        parse_timestamp(timestamp)
        location = urlsplit(original)
        valid = (
            location.hostname in allowed_hosts
            and location.port in {None, 80, 443}
            and location.username is None
            and location.password is None
        )
    except ValueError as exc:
        raise FetchError(f"invalid replay provenance: {url}", retryable=False) from exc
    if not valid:
        raise FetchError(f"unexpected original host in Wayback replay: {original}", retryable=False)
    return timestamp, original


def _served_timestamp(response: httpx.Response) -> tuple[str, str] | None:
    memento = response.headers.get("memento-datetime")
    if memento:
        try:
            moment = parsedate_to_datetime(memento)
            if moment.tzinfo is not None:
                moment = moment.astimezone(UTC)
                return f"{moment.year:04d}{moment:%m%d%H%M%S}", "memento-datetime"
        except (TypeError, ValueError, OverflowError):
            pass
    match = _REPLAY_PATH.match(response.url.path)
    if match is not None and len(match[1]) == 14:
        try:
            parse_timestamp(match[1])
        except ValueError:
            return None
        return match[1], "replay-url"
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
            await self._client.log.debug(
                "cdx page",
                domain=domain,
                year=year,
                captures=len(captures),
                resumed=resume_key is not None,
                more=next_key is not None,
            )
            yield CapturePage(captures=captures, resume_key=next_key)
            if not next_key or not captures:
                return
            resume_key = next_key

    async def lookup(
        self,
        url: str,
        *,
        around: str | None = None,
        window_days: int = 183,
        limit: int = 5,
    ) -> list[Capture]:
        params: dict[str, Any] = {
            "url": url,
            "matchType": "exact",
            "filter": ["statuscode:200", "mimetype:text/html"],
            "fl": "urlkey,timestamp,original,digest",
            "output": "json",
            "limit": str(limit),
            "collapse": "digest",
        }
        if around:
            centre = parse_timestamp(around)
            window = timedelta(days=window_days)
            params["from"] = (centre - window).strftime("%Y%m%d")
            params["to"] = (centre + window).strftime("%Y%m%d")
            # Nearest captures first (supported for exact-URL queries).
            params["sort"] = "closest"
            params["closest"] = centre.strftime("%Y%m%d%H%M%S")
        response = await self._client.get(CDX_ENDPOINT, params=params)
        captures, _ = parse_cdx_json(response.json() if response.content.strip() else [])
        await self._client.log.debug("cdx lookup", url=url, around=around, captures=len(captures))
        return captures[:limit]


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
