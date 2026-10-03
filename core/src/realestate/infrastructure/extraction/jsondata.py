"""Structured data embedded in HTML, and a small path language to read it.

Later page generations ship their data as JSON inside the page
(``window.state = {...}``, ``application/ld+json``, URL-encoded ``data-*``
attributes). Reading that is far more robust than scraping the rendered markup,
so the engine exposes it to rules and summarises it for the LLM.

Path syntax: ``a.b[0].c``, ``hits[*].title``, ``["location.lvl1"].name``. Keys
that themselves contain dots (``location.lvl1``) also resolve without quoting:
at each step the longest dotted run that exists as a key wins.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote

from selectolax.lexbor import LexborHTMLParser

_ASSIGNMENT = re.compile(
    r"(?:^|[;\s])(?:window\.|var\s+|let\s+|const\s+|self\.)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*=\s*(?=[\[{])"
)
_PATH_TOKEN = re.compile(r'\[\s*"([^"]*)"\s*\]|\[\s*\'([^\']*)\'\s*\]|\[(\*|\d+)\]|([^.\[\]]+)')


def script_json(tree: LexborHTMLParser) -> dict[str, Any]:
    """Every JSON value assigned or embedded in the page's scripts, by name.

    Names are the assignment target (``"window.state"`` is stored as both
    ``"window.state"`` and ``"state"``), ``"ld+json"`` (a list, one entry per
    block) or ``"#<id>"`` for a JSON script with an id (``#__NEXT_DATA__``).
    """
    found: dict[str, Any] = {}
    decoder = json.JSONDecoder()
    for script in tree.css("script"):
        text = script.text(deep=True) or ""
        if not text.strip():
            continue
        kind = (script.attributes.get("type") or "").lower()
        if "json" in kind:
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if "ld+json" in kind:
                found.setdefault("ld+json", []).append(value)
            script_id = script.attributes.get("id")
            if script_id:
                found[f"#{script_id}"] = value
            continue
        position = 0
        while (match := _ASSIGNMENT.search(text, position)) is not None:
            position = match.end()
            try:
                value, end = decoder.raw_decode(text, match.end())
            except ValueError:
                continue
            position = end  # never rescan the inside of a decoded blob
            if not isinstance(value, (dict, list)) or not value:
                continue
            name = match.group(1)
            found.setdefault(name, value)
            found.setdefault(f"window.{name}" if not name.startswith("window.") else name, value)
            found.setdefault(name.removeprefix("window."), value)
    return found


def decode_attribute(value: str | None, decode: str | None) -> Any:
    """An attribute value, optionally decoded from (URL-encoded) JSON."""
    if value is None or not decode:
        return value
    text = unquote(value) if decode == "urlencoded_json" else value
    try:
        return json.loads(text)
    except ValueError:
        return None


def _tokens(path: str) -> list[str]:
    tokens: list[str] = []
    for match in _PATH_TOKEN.finditer(path.strip().lstrip("$").lstrip(".")):
        quoted = match.group(1) if match.group(1) is not None else match.group(2)
        if quoted is not None:
            tokens.append("\x00" + quoted)  # marks "use as one literal key"
        elif match.group(3) is not None:
            tokens.append("[" + match.group(3) + "]")
        else:
            tokens.append(match.group(4))
    return tokens


def resolve(value: Any, path: str | None) -> list[Any]:
    """All values ``path`` reaches from ``value`` (wildcards fan out)."""
    if not path:
        return [value]
    return list(_resolve(value, _tokens(path)))


def _resolve(value: Any, tokens: list[str]) -> Iterator[Any]:
    if not tokens:
        yield value
        return
    head, rest = tokens[0], tokens[1:]
    if head.startswith("["):
        if not isinstance(value, list):
            return
        index = head[1:-1]
        if index == "*":
            for item in value:
                yield from _resolve(item, rest)
        elif int(index) < len(value):
            yield from _resolve(value[int(index)], rest)
        return
    if not isinstance(value, dict):
        if isinstance(value, list) and head.isdigit() and int(head) < len(value):
            yield from _resolve(value[int(head)], rest)
        return
    if head.startswith("\x00"):
        key = head[1:]
        if key in value:
            yield from _resolve(value[key], rest)
        return
    # Longest dotted run first: "location.lvl1.name" -> value["location.lvl1"]["name"].
    names = [head]
    for token in rest:
        if token.startswith(("[", "\x00")):
            break
        names.append(token)
    for length in range(len(names), 0, -1):
        key = ".".join(names[:length])
        if key in value:
            yield from _resolve(value[key], tokens[length:])
            return


def first_scalar(values: list[Any]) -> str | None:
    """The first non-empty scalar among resolved values, as text."""
    for value in values:
        if isinstance(value, list):
            nested = first_scalar(value)
            if nested is not None:
                return nested
            continue
        if isinstance(value, dict):
            # A localised object like {"en": "...", "ar": "..."} or {"name": ...}.
            for key in ("name", "en", "ar", "value", "label", "title"):
                if isinstance(value.get(key), (str, int, float)) and str(value[key]).strip():
                    return str(value[key])
            continue
        if value is None or isinstance(value, bool):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def summarise(data: dict[str, Any], *, max_arrays: int = 3, sample_chars: int = 1500) -> str:
    """Point the LLM at the useful parts of embedded JSON.

    Lists arrays of objects (the likely listings) with their path, length, and
    a truncated first element, largest first.
    """
    arrays: list[tuple[int, str, str, Any]] = []
    seen: set[int] = set()
    for name, value in data.items():
        if id(value) in seen or name.startswith("window."):
            continue
        seen.add(id(value))
        for path, items in _object_arrays(value, "", depth=0):
            arrays.append((len(items), name, path, items[0]))
    if not arrays:
        return ""
    arrays.sort(key=lambda entry: -entry[0])
    lines = []
    for length, name, path, first in arrays[:max_arrays]:
        sample = json.dumps(_truncate(first), ensure_ascii=False)[:sample_chars]
        lines.append(
            f'script "{name}" path "{path or "$"}": array of {length} objects. First: {sample}'
        )
    return "\n".join(lines)


def _object_arrays(value: Any, path: str, *, depth: int) -> Iterator[tuple[str, list[Any]]]:
    if depth > 7:
        return
    if isinstance(value, list):
        dicts = [item for item in value if isinstance(item, dict)]
        if len(dicts) >= 2 and len(dicts) >= len(value) // 2:
            yield path, dicts
        if value and isinstance(value[0], (dict, list)):
            yield from _object_arrays(value[0], f"{path}[0]", depth=depth + 1)
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                child_path = f"{path}.{key}" if path else key
                if "." in key:
                    child_path = f'{path}["{key}"]'
                yield from _object_arrays(child, child_path, depth=depth + 1)


def _truncate(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 80 else value[:77] + "..."
    if isinstance(value, list):
        return [_truncate(item, depth + 1) for item in value[:2]] + (
            ["..."] if len(value) > 2 else []
        )
    if isinstance(value, dict):
        if depth > 2:
            return "{...}"
        return {key: _truncate(child, depth + 1) for key, child in list(value.items())[:30]}
    return value
