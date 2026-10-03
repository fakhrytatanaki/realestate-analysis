"""Web-archive vocabulary: captures, the crawl frontier, discovered links.

Also the two pure URL functions the archive pipeline is built on:

* :func:`surt_key` canonicalises a URL the way the Wayback CDX index does, so a
  link found inside an archived page can be matched to the captures we
  enumerated without asking the archive again.
* :func:`url_shape` reduces a URL to a coarse pattern, so thousands of similar
  URLs nobody has a rule for become one question for rule induction.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from realestate.domain.enums import CrawlStatus, LinkRel, LinkRequestStatus, PageKind

#: Wayback timestamps are UTC ``YYYYMMDDhhmmss``; shorter ones are prefixes.
_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"


def parse_timestamp(timestamp: str) -> datetime:
    """``"20130305032637"`` -> aware UTC datetime. Short prefixes are padded."""
    padded = (timestamp + "00000101000000"[len(timestamp) :])[:14]
    return datetime.strptime(padded, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ArchiveScope:
    """What part of the archive a source covers."""

    domain: str
    from_year: int
    to_year: int


@dataclass(frozen=True, slots=True)
class Capture:
    """One row of the archive index: a URL as it was at one moment."""

    url_key: str
    timestamp: str
    original_url: str
    digest: str

    @property
    def captured_at(self) -> datetime:
        return parse_timestamp(self.timestamp)


@dataclass(frozen=True, slots=True)
class FrontierEntry:
    """A capture plus what the crawler has decided about it."""

    id: int
    source_key: str
    url_key: str
    timestamp: str
    original_url: str
    digest: str
    status: CrawlStatus
    priority: int = 0
    page_kind: PageKind | None = None
    route_node: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    error: str | None = None

    @property
    def captured_at(self) -> datetime:
        return parse_timestamp(self.timestamp)


@dataclass(frozen=True, slots=True)
class DiscoveredLink:
    """A URL a recognised page points at, and in what role."""

    url: str
    rel: LinkRel


@dataclass(frozen=True, slots=True)
class LinkRequest:
    """A linked URL no enumerated capture matched, to look up in the index by itself."""

    source_key: str
    url_key: str
    url: str
    rel: LinkRel
    parent_timestamp: str | None = None
    status: LinkRequestStatus = LinkRequestStatus.PENDING
    attempts: int = 0
    id: int | None = None


@dataclass(frozen=True, slots=True)
class ArchivedDocument:
    """An archived page as the rule engine sees it.

    ``captured_at`` is a parse *input*: archived pages say "02 Mar" or
    "13 hours ago", which only mean something relative to the capture.
    """

    content: bytes
    url: str
    captured_at: datetime | None = None
    content_type: str = "text/html"

    @classmethod
    def from_payload(
        cls,
        content: bytes,
        *,
        content_type: str,
        source_url: str | None,
        meta: dict[str, Any] | None,
    ) -> ArchivedDocument:
        """Rebuild the engine's view of an archived payload from its metadata."""
        meta = meta or {}
        captured_at: datetime | None = None
        if meta.get("captured_at"):
            try:
                captured_at = datetime.fromisoformat(str(meta["captured_at"]))
            except ValueError:
                captured_at = None
        if captured_at is None and meta.get("timestamp"):
            captured_at = parse_timestamp(str(meta["timestamp"]))
        return cls(
            content=content,
            url=str(meta.get("original_url") or source_url or ""),
            captured_at=captured_at,
            content_type=content_type,
        )


#: Route node of captures fetched as a random sample of rejected routing
#: decisions, to measure how often SKIP/DEFER/miss turned adverts away.
EXPLORE_NODE = "explore"

#: Appended to a route node's key on captures its decision would fetch but the
#: per-URL capture cap skipped.
CAPTURE_CAP = "#capture-cap"


def routing_node(route_node: str) -> str:
    """The rule a capture's ``route_node`` names, without the capture-cap marker."""
    return route_node.removesuffix(CAPTURE_CAP)


def link_evidence(captures: Sequence[FrontierEntry]) -> dict[str, list[str]]:
    """How recognised pages linked to any capture of one URL, merged for routing."""
    evidence: dict[str, list[str]] = {}
    for entry in captures:
        for rel in entry.evidence.get("linked_as") or []:
            rels = evidence.setdefault("linked_as", [])
            if rel not in rels:
                rels.append(rel)
    return evidence


def capture_year(timestamp: str) -> int:
    return int(timestamp[:4])


def capture_quarter(timestamp: str) -> str:
    """``"20131027..."`` -> ``"2013Q4"``: the stratum fetch order rotates through."""
    month = int(timestamp[4:6] or "1") if len(timestamp) >= 6 else 1
    return f"{timestamp[:4]}Q{(max(1, min(12, month)) - 1) // 3 + 1}"


def stratified_order(
    rows: Sequence[tuple[int, str, int]],
    *,
    taken: Mapping[str, int] | None = None,
    per_stratum: int | None = None,
) -> list[int]:
    """Frontier ids in fetch order: round-robin across capture quarters.

    ``rows`` are ``(id, timestamp, priority)``. Within a quarter, higher
    priority then older first; across quarters, each quarter's first pick,
    then each one's second, and so on -- so a dense month cannot starve a
    sparse one of a bounded fetch budget. ``per_stratum`` caps each quarter's
    total including ``taken`` (already fetched), for breadth pilots.
    """
    by_stratum: dict[str, list[tuple[int, str, int]]] = {}
    for entry_id, timestamp, priority in rows:
        by_stratum.setdefault(capture_quarter(timestamp), []).append(
            (-priority, timestamp, entry_id)
        )
    ranked: list[tuple[int, int, str, int]] = []
    for stratum, entries in by_stratum.items():
        entries.sort()
        if per_stratum is not None:
            entries = entries[: max(0, per_stratum - (taken or {}).get(stratum, 0))]
        ranked.extend(
            (rank, negative, timestamp, entry_id)
            for rank, (negative, timestamp, entry_id) in enumerate(entries)
        )
    ranked.sort()
    return [entry_id for *_, entry_id in ranked]


@dataclass(frozen=True, slots=True)
class IdentityPolicy:
    """How a source's adverts are identified: fixed code, never induced.

    Induced templates locate an advert's URL; this policy turns that URL into
    the external id, so a selector that grabs the site logo link cannot invent
    an identity. ``id_patterns`` are tried in order and group 1 is the id;
    ``reject_patterns`` name URLs that are never one advert (home, categories).
    """

    id_patterns: tuple[str, ...]
    reject_patterns: tuple[str, ...] = ()
    #: Advert URLs whose token is *not* trusted as identity (a base62 slug
    #: token, say) but still names one advert: evidence that a page lists
    #: adverts, never an external id.
    advert_patterns: tuple[str, ...] = ()

    def canonical_id(self, url: str | None) -> str | None:
        """The advert id in ``url``, or ``None`` if it names no advert."""
        if not url or self.rejects(url):
            return None
        return _first_match(self.id_patterns, url)

    def advert_key(self, url: str | None) -> str | None:
        """A key for the advert ``url`` links to, trusted as identity or not."""
        if not url or self.rejects(url):
            return None
        return _first_match(self.id_patterns, url) or _first_match(self.advert_patterns, url)

    def rejects(self, url: str) -> bool:
        return is_site_root(url) or any(
            re.search(pattern, url, re.IGNORECASE) for pattern in self.reject_patterns
        )


def _first_match(patterns: Sequence[str], url: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return None


def is_site_root(url: str) -> bool:
    """Whether ``url`` is a bare host (``http://www.olx.com.eg/``), never one advert."""
    raw = url.strip()
    if "://" not in raw:
        raw = "http://" + raw.lstrip("/")
    parts = urlsplit(raw)
    return parts.path.strip("/") == "" and not parts.query


# -- URL canonicalisation -------------------------------------------------

_WWW_PREFIX = re.compile(r"^www\d*\.")
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def surt_key(url: str) -> str:
    """Canonical, Wayback-compatible key for ``url``.

    ``http://www.olx.com.eg:80/Search/?b=2&a=1#x`` -> ``eg,com,olx)/search?a=1&b=2``.
    Lower-cased, ``www`` and default ports dropped, host labels reversed, query
    sorted, fragment and trailing slash removed -- what the CDX ``urlkey`` does
    for the cases that occur in practice.
    """
    raw = url.strip()
    if "://" not in raw:
        raw = "http://" + raw.lstrip("/")
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower().rstrip(".")
    host = _WWW_PREFIX.sub("", host)
    port = parts.port
    reversed_host = ",".join(reversed(host.split("."))) if host else ""
    if port is not None and str(port) != _DEFAULT_PORTS.get(parts.scheme.lower(), ""):
        reversed_host = f"{reversed_host}:{port}"

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query = ""
    if parts.query:
        pairs = sorted(parse_qsl(parts.query, keep_blank_values=True))
        query = "?" + "&".join(f"{key}={value}" if value else key for key, value in pairs)
    return f"{reversed_host}){path}{query}".lower()


_LOCALE_SEGMENTS = frozenset({"en", "ar", "fr", "de", "es"})
_ALPHA_SEGMENT = re.compile(r"^[a-z][a-z-]*$")
_TOKEN_SPLIT = re.compile(r"([-_.])")
#: Trailing structural part of a slug: short marker words followed by ids,
#: e.g. ``-cat-N-p-N``, ``-ref-X-iid-X``, ``-X.html``.
_TAIL_MARKERS = re.compile(r"((?:[-_](?:[a-z]{1,4}[-_])?X)+)$")


def _token_shape(token: str) -> str:
    """Ids become ``X``: anything with a digit, or a mixed-case base62 token."""
    if token in "-_.":
        return token
    if any(char.isdigit() for char in token):
        return "X"
    if len(token) >= 5 and token.isascii() and not token.islower() and not token.isupper():
        return "X"
    return token.lower()


def _segment_tail(segment: str) -> str:
    """Generalise the last path segment to its structural suffix."""
    tokens = [_token_shape(token) for token in _TOKEN_SPLIT.split(segment) if token]
    shaped = "".join(tokens)
    shaped = re.sub(r"(?:-X)+", "-X", shaped)
    if not shaped.isascii():
        shaped = re.sub(r"[^\x00-\x7f]+", "U", shaped)
    extension = re.search(r"\.[a-z0-9]{2,5}$", shaped)
    if extension:
        # Slug pages (`/ad/<words>-ID8igUm.html`): the words are content, only
        # "ends in an id" is structure.
        stem = shaped[: extension.start()]
        return ("*-X" if stem.endswith("X") else "*") + extension.group(0)
    match = _TAIL_MARKERS.search(shaped)
    if match:
        return "*" + match.group(1)
    return "*"


def url_shape(url: str) -> str:
    """Coarse pattern grouping URLs that probably share one routing decision.

    Keeps the host (city-style subdomains folded to ``*``), the first two
    literal path segments after an optional locale, the structural tail of the
    last segment, the depth, and the query parameter names. Over-merging is
    fine -- rule induction sees samples and may answer with several rules --
    under-merging only costs extra LLM calls.
    """
    raw = url if "://" in url else "http://" + url
    parts = urlsplit(raw)
    host = _WWW_PREFIX.sub("", (parts.hostname or "").lower())
    labels = host.split(".")
    if len(labels) > 3 and labels[0] not in {"m", "mobile"}:
        host = "*." + ".".join(labels[1:])

    segments = [unquote(s) for s in parts.path.split("/") if s]
    tail = _segment_tail(segments[-1]) if segments else ""
    segments = [segment.lower() for segment in segments]
    locale = ""
    if segments and segments[0] in _LOCALE_SEGMENTS:
        locale = segments.pop(0) + "/"
    prefix_parts: list[str] = []
    for segment in segments[:-1][:2]:
        prefix_parts.append(segment if _ALPHA_SEGMENT.match(segment) or len(segment) <= 3 else "*")
    query_keys = sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)})
    shape = f"{host}/{locale}{'/'.join(prefix_parts)}"
    shape = shape.rstrip("/") + f"/{tail}|d{len(segments)}"
    if query_keys:
        shape += "|q:" + ",".join(query_keys)
    return shape
