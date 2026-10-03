"""spaCy token patterns as a rule cue.

Only blank pipelines are used (tokenizer plus lexical attributes such as
``LIKE_NUM``, which understands Arabic-Indic digits), so no model downloads
are involved and results are deterministic. spaCy is an optional dependency:
without it, ``spacy_match`` conditions raise and therefore evaluate false.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=8)
def _pipeline(lang: str) -> Any:
    import spacy

    return spacy.blank(lang)


@lru_cache(maxsize=256)
def _matcher(lang: str, patterns_json: str) -> Any:
    from spacy.matcher import Matcher

    matcher = Matcher(_pipeline(lang).vocab)
    matcher.add("CUE", json.loads(patterns_json))
    return matcher


def count_matches(text: str, patterns: list[list[dict[str, Any]]], *, lang: str = "xx") -> int:
    """How many spans of ``text`` match any of the token ``patterns``."""
    try:
        matcher = _matcher(lang, json.dumps(patterns, sort_keys=True, ensure_ascii=False))
    except ImportError as exc:
        raise ValueError("spaCy is not installed") from exc
    return len(matcher(_pipeline(lang)(text)))


def spacy_available() -> bool:
    try:
        import spacy  # noqa: F401
    except ImportError:
        return False
    return True
