"""A parsed archived document with lazily computed views.

Parsing HTML and decoding embedded JSON are the expensive steps, and one
document is evaluated against many conditions, so each view is built at most
once.
"""

from __future__ import annotations

from datetime import datetime
from functools import cached_property
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from realestate.domain.archive import ArchivedDocument
from realestate.infrastructure.extraction.jsondata import script_json
from realestate.infrastructure.extraction.text import clean_text, decode_html


class SelectorError(ValueError):
    """A rule contains a CSS selector the engine cannot parse."""


def select(scope: LexborHTMLParser | LexborNode, selector: str) -> list[LexborNode]:
    """CSS query that reports bad selectors as :class:`SelectorError`.

    A leading ``>`` means "direct children of the scope" -- lexbor has no
    ``:scope``, and induced rules for item fields naturally want it.
    """
    selector = selector.strip()
    direct = selector.startswith(">")
    if direct:
        selector = selector[1:].strip()
    try:
        nodes = list(scope.css(selector))
    except Exception as exc:  # selectolax raises its own error type
        raise SelectorError(f"invalid CSS selector {selector!r}: {exc}") from exc
    if direct and isinstance(scope, LexborNode):
        nodes = [
            node for node in nodes if node.parent is not None and node.parent.mem_id == scope.mem_id
        ]
    return nodes


class ParsedDocument:
    """An :class:`ArchivedDocument` plus cached parses."""

    def __init__(self, document: ArchivedDocument) -> None:
        self.document = document

    @property
    def url(self) -> str:
        return self.document.url

    @property
    def captured_at(self) -> datetime | None:
        return self.document.captured_at

    @cached_property
    def html(self) -> str:
        return decode_html(self.document.content, self.document.content_type)

    @cached_property
    def tree(self) -> LexborHTMLParser:
        return LexborHTMLParser(self.html)

    @cached_property
    def _script_data(self) -> tuple[dict[str, Any], list[str]]:
        problems: list[str] = []
        return script_json(self.tree, problems=problems), problems

    @property
    def scripts(self) -> dict[str, Any]:
        return self._script_data[0]

    @property
    def script_problems(self) -> list[str]:
        return self._script_data[1]

    @cached_property
    def title(self) -> str:
        node = self.tree.css_first("title")
        return clean_text(node.text(deep=True)) if node else ""

    @cached_property
    def body_text(self) -> str:
        body = self.tree.body
        return clean_text(body.text(deep=True, separator=" ")) if body else ""
