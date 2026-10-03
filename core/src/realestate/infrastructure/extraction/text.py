"""Decoding and text clean-up shared by the extraction engine."""

from __future__ import annotations

import html
import re
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

_CHARSET_HEADER = re.compile(r"charset=[\"']?([\w.:-]+)", re.IGNORECASE)
_CHARSET_META = re.compile(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
#: Arabic-Indic and Extended (Persian) digits, plus Arabic separators.
_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹\u066c\u066b",  # ... plus Arabic thousands and decimal separators
    "01234567890123456789,.",
)
#: Prefix the Wayback Machine adds when it rewrites links in replayed pages.
_WAYBACK_PREFIX = re.compile(r"^(?:https?:)?//web\.archive\.org/web/\d+[a-z_]*/", re.IGNORECASE)


def decode_html(content: bytes, content_type: str | None = None) -> str:
    """Bytes to text: declared charset, then ``<meta>``, then sensible fallbacks.

    Old Arabic sites are sometimes windows-1256, so that is tried before the
    lossless latin-1 last resort.
    """
    candidates: list[str] = []
    if content_type:
        match = _CHARSET_HEADER.search(content_type)
        if match:
            candidates.append(match.group(1))
    meta = _CHARSET_META.search(content[:4096])
    if meta:
        candidates.append(meta.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "cp1256"]
    for charset in candidates:
        try:
            return content.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("latin-1")


def normalise_digits(text: str) -> str:
    """Arabic-Indic digits and separators to ASCII."""
    return text.translate(_DIGITS)


def clean_text(text: str | None, *, max_length: int | None = None) -> str:
    """Unescape entities, collapse whitespace, trim, optionally truncate."""
    if not text:
        return ""
    cleaned = _WHITESPACE.sub(" ", html.unescape(text)).strip()
    if max_length is not None and len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1].rstrip() + "…"
    return cleaned


def absolute_url(href: str | None, base: str) -> str | None:
    """Resolve ``href`` against the page URL, undoing Wayback link rewriting.

    Returns ``None`` for things that are not navigable pages (``javascript:``,
    ``mailto:``, bare fragments).
    """
    if not href:
        return None
    href = html.unescape(href.strip())
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    href = _WAYBACK_PREFIX.sub("", href)
    base = _WAYBACK_PREFIX.sub("", base)
    resolved = urljoin(base, href)
    parts = urlsplit(resolved)
    if parts.scheme not in ("http", "https"):
        return None
    # Percent-encode raw non-ASCII (Arabic slugs) the way browsers and the
    # archive index do, leaving existing escapes alone, so keys match the CDX.
    path = quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/%:@!$&'()*+,;=-._~?")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))
