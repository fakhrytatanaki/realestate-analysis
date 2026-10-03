"""Condition evaluators: the cues a rule graph's transitions are guarded by.

Every condition is a JSON object with a ``type``. Unknown types and malformed
conditions evaluate to false rather than raising, because conditions are partly
machine-written and one bad rule must not stop a parse; induction validation is
where they are rejected loudly.

=================  =====================================================
``always``         holds unconditionally
``all/any``        ``{"conditions": [...]}``
``not``            ``{"condition": {...}}``
``url_regex``      ``{"pattern"}`` -- searched in the URL, case-insensitive
``evidence``       ``{"key", "equals" | "in"}`` -- what linking pages said
``capture_range``  ``{"from", "to"}`` -- ``YYYY[-MM[-DD]]`` bounds, inclusive
``dom_css``        ``{"css", "min"=1, "max"}`` -- count of matching elements
``text_regex``     ``{"pattern", "css"?}`` -- in the page or selected text
``json_path``      ``{"script", "path", "min"=1}`` -- embedded JSON present
``spacy_match``    ``{"patterns", "lang"="xx", "css"?, "min"=1}`` -- spaCy
                   ``Matcher`` token patterns over the page or selected text
``template_fp``    ``{"fingerprint", "max_distance"=8}`` -- DOM similarity
=================  =====================================================
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any

from realestate.infrastructure.extraction.document import ParsedDocument, SelectorError, select
from realestate.infrastructure.extraction.fingerprint import (
    fingerprint_distance,
    structural_fingerprint,
)
from realestate.infrastructure.extraction.jsondata import resolve
from realestate.infrastructure.extraction.text import clean_text

#: The condition types the engine understands; validation rejects anything else.
CONDITION_TYPES = frozenset(
    {
        "always",
        "all",
        "any",
        "not",
        "url_regex",
        "evidence",
        "capture_range",
        "dom_css",
        "text_regex",
        "json_path",
        "spacy_match",
        "template_fp",
    }
)
#: Selectors too generic to identify a page template on their own.
GENERIC_SELECTORS = frozenset(
    {"*", "html", "body", "head", "div", "a", "span", "p", "li", "ul", "img", "title"}
)


@dataclass(slots=True)
class EvalContext:
    """What a condition can look at."""

    url: str
    document: ParsedDocument | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    captured_at: datetime | None = None


@lru_cache(maxsize=4096)
def compiled(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(pattern, flags)


def evaluate(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    """Whether ``condition`` holds; malformed conditions are simply false."""
    try:
        return _evaluate(condition, ctx)
    except (re.error, SelectorError, KeyError, TypeError, ValueError):
        return False


def check(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    """Like :func:`evaluate`, but malformed conditions raise -- for validation."""
    return _evaluate(condition, ctx)


def _evaluate(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    kind = condition.get("type")
    handler = _HANDLERS.get(str(kind))
    if handler is None:
        raise ValueError(f"unknown condition type {kind!r}")
    return handler(condition, ctx)


def _all(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    return all(_evaluate(child, ctx) for child in condition["conditions"])


def _any(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    return any(_evaluate(child, ctx) for child in condition["conditions"])


def _not(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    return not _evaluate(condition["condition"], ctx)


def _url_regex(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    return compiled(str(condition["pattern"])).search(ctx.url) is not None


def _evidence(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    value = ctx.evidence.get(str(condition["key"]))
    values = set(value) if isinstance(value, list) else ({value} if value is not None else set())
    if "equals" in condition:
        return condition["equals"] in values
    if "in" in condition:
        return bool(values & set(condition["in"]))
    return bool(values)


def _bound(text: str, *, upper: bool) -> datetime:
    parts = [int(part) for part in str(text).split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else (12 if upper else 1)
    if len(parts) > 2:
        day = parts[2]
    elif upper:
        day = 28 if month == 2 else (30 if month in (4, 6, 9, 11) else 31)
    else:
        day = 1
    moment = datetime(year, month, day, 23 if upper else 0, 59 if upper else 0, 59 if upper else 0)
    return moment


def _capture_range(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    captured = ctx.captured_at or (ctx.document.captured_at if ctx.document else None)
    if captured is None:
        return False
    naive = captured.replace(tzinfo=None)
    if condition.get("from") and naive < _bound(condition["from"], upper=False):
        return False
    return not (condition.get("to") and naive > _bound(condition["to"], upper=True))


def _count_ok(count: int, condition: Mapping[str, Any]) -> bool:
    minimum = int(condition.get("min", 1))
    maximum = condition.get("max")
    return count >= minimum and (maximum is None or count <= int(maximum))


def _dom_css(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    if ctx.document is None:
        return False
    return _count_ok(len(select(ctx.document.tree, str(condition["css"]))), condition)


def _scoped_text(condition: Mapping[str, Any], document: ParsedDocument) -> str:
    if condition.get("css"):
        nodes = select(document.tree, str(condition["css"]))
        return " ".join(clean_text(node.text(deep=True, separator=" ")) for node in nodes)
    return document.title + " " + document.body_text


def _text_regex(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    if ctx.document is None:
        return False
    matches = compiled(str(condition["pattern"])).findall(_scoped_text(condition, ctx.document))
    return _count_ok(len(matches), condition)


def _json_path(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    value_type = condition.get("value_type")
    if value_type not in (None, "array", "object"):
        raise ValueError("json_path value_type must be array or object")
    if ctx.document is None:
        return False
    data = ctx.document.scripts.get(str(condition["script"]))
    if data is None:
        return False
    resolved = resolve(data, condition.get("path"))
    if "equals" in condition:
        expected = condition["equals"]
        return any(type(value) is type(expected) and value == expected for value in resolved)
    if value_type is not None:
        expected_type = list if value_type == "array" else dict
        values = [value for value in resolved if isinstance(value, expected_type)]
        if not values:
            return False  # min=0 must not turn a missing path into an empty array
    else:
        values = [value for value in resolved if value not in (None, "", [], {})]
    count = len(values[0]) if len(values) == 1 and isinstance(values[0], list) else len(values)
    return _count_ok(count, condition)


def _spacy_match(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    if ctx.document is None:
        return False
    from realestate.infrastructure.extraction.nlp import count_matches

    text = _scoped_text(condition, ctx.document)[:100_000]
    count = count_matches(text, condition["patterns"], lang=str(condition.get("lang", "xx")))
    return _count_ok(count, condition)


def _template_fp(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    if ctx.document is None:
        return False
    distance = fingerprint_distance(
        structural_fingerprint(ctx.document), str(condition["fingerprint"])
    )
    return distance <= int(condition.get("max_distance", 8))


def _always(condition: Mapping[str, Any], ctx: EvalContext) -> bool:
    return True


_HANDLERS: dict[str, Callable[[Mapping[str, Any], EvalContext], bool]] = {
    "always": _always,
    "all": _all,
    "any": _any,
    "not": _not,
    "url_regex": _url_regex,
    "evidence": _evidence,
    "capture_range": _capture_range,
    "dom_css": _dom_css,
    "text_regex": _text_regex,
    "json_path": _json_path,
    "spacy_match": _spacy_match,
    "template_fp": _template_fp,
}


def identifying_cues(condition: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The leaf conditions that actually discriminate one page template.

    Used by validation to refuse templates guarded only by ``always`` or by a
    selector like ``div`` that every page satisfies.
    """
    kind = condition.get("type")
    if kind in ("all", "any"):
        return [cue for child in condition.get("conditions", []) for cue in identifying_cues(child)]
    if kind == "dom_css":
        return (
            []
            if str(condition.get("css", "")).strip().lower() in GENERIC_SELECTORS
            else [condition]
        )
    # URLs are not enough: the same URLs served several redesigns over the years.
    if kind in ("text_regex", "json_path", "template_fp", "spacy_match"):
        return [condition]
    return []
