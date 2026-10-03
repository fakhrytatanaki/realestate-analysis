"""A compact rendering of a page for a small-context LLM.

Raw archived pages run 60 KB - 1 MB. The model needs the *structure* -- which
elements repeat, what their classes are, where the price sits -- not every
advert or every script. So:

* scripts, styles, SVG and the like are dropped (embedded JSON is summarised
  separately, see :func:`jsondata.summarise`);
* only identifying attributes survive (``id``, ``class``, ``href``, ``data-*``
  ...), truncated;
* runs of structurally similar siblings keep a couple of examples plus a count,
  which is what makes a 50-row result list fit;
* text is truncated per node.

If the result is still over budget, it is re-rendered with fewer examples and
shorter text before being cut.
"""

from __future__ import annotations

from dataclasses import dataclass

from selectolax.lexbor import LexborNode

from realestate.infrastructure.extraction.document import ParsedDocument
from realestate.infrastructure.extraction.jsondata import summarise
from realestate.infrastructure.extraction.privacy import (
    redact_attribute,
    redact_text,
    sensitive_key,
)
from realestate.infrastructure.extraction.text import clean_text

_SKIPPED = frozenset(
    {
        "script",
        "style",
        "noscript",
        "svg",
        "iframe",
        "link",
        "meta",
        "template",
        "canvas",
        "video",
        "audio",
        "source",
        "picture",
        "object",
        "embed",
        "head",
    }
)
_VOID = frozenset({"img", "br", "hr", "input", "area", "base", "col", "wbr"})
_BLOCK = frozenset(
    {
        "div",
        "li",
        "ul",
        "ol",
        "tr",
        "table",
        "tbody",
        "thead",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "main",
        "aside",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "dl",
        "dt",
        "dd",
        "td",
        "th",
        "body",
    }
)
_KEPT_ATTRIBUTES = (
    "id",
    "class",
    "href",
    "title",
    "itemprop",
    "itemtype",
    "lang",
    "dir",
    "name",
    "role",
    "action",
    "aria-label",
)


@dataclass(frozen=True, slots=True)
class _Style:
    keep_similar: int
    text_chars: int
    attribute_chars: int
    max_depth: int


_STYLES = (
    _Style(keep_similar=2, text_chars=80, attribute_chars=160, max_depth=40),
    _Style(keep_similar=2, text_chars=50, attribute_chars=100, max_depth=32),
    _Style(keep_similar=1, text_chars=40, attribute_chars=80, max_depth=28),
    _Style(keep_similar=1, text_chars=24, attribute_chars=60, max_depth=22),
)


def render(document: ParsedDocument, *, budget_chars: int = 24_000) -> str:
    """Structure + embedded-JSON summary, within ``budget_chars``."""
    header = f"<title>{redact_text(document.title)[:200]}</title>\n"
    json_summary = summarise(document.scripts)
    json_part = f"\n\nEMBEDDED JSON (scripts):\n{json_summary}" if json_summary else ""
    json_part = json_part[: budget_chars // 2]
    room = budget_chars - len(header) - len(json_part)
    body = document.tree.body or document.tree.root
    dom = ""
    for style in _STYLES:
        dom = _render_node(body, style, 0) if body is not None else ""
        if len(dom) <= room:
            break
    marker = "\n<!-- truncated -->"
    if len(dom) > room:
        dom = dom[: max(room - len(marker), 0)] + marker
    return f"{header}{dom}{json_part}"


def _attributes(node: LexborNode, style: _Style) -> str:
    parts: list[str] = []
    for name, value in node.attributes.items():
        if value is None or sensitive_key(name):
            continue
        if name in _KEPT_ATTRIBUTES or name.startswith("data-"):
            text = " ".join(redact_attribute(value).split())
            if len(text) > style.attribute_chars:
                text = text[: style.attribute_chars] + "…"
            parts.append(f'{name}="{text}"')
    return (" " + " ".join(parts)) if parts else ""


def _signature(node: LexborNode) -> str:
    """Tag plus the shape of its direct children: groups rows whose own
    classes alternate (``first``/``even``/``odd``) but whose content does not."""
    children: list[str] = []
    child = node.child
    while child is not None and len(children) < 6:
        if child.tag and not child.tag.startswith("-"):
            classes = (child.attributes.get("class") or "").split()
            children.append(f"{child.tag}.{classes[0] if classes else ''}")
        child = child.next
    return f"{node.tag}|{','.join(children)}"


def _render_node(node: LexborNode, style: _Style, depth: int) -> str:
    tag = node.tag or ""
    if tag == "-text":
        return clean_text(redact_text(node.text(deep=False)), max_length=style.text_chars)
    if tag.startswith("-") or tag in _SKIPPED:
        return ""
    if sensitive_key(node.attributes.get("aria-label") or ""):
        return ""
    if depth > style.max_depth:
        return "…"
    opening = f"<{tag}{_attributes(node, style)}>"
    if tag in _VOID:
        return opening

    rendered: list[str] = []
    run_signature: str | None = None
    run_length = 0
    skipped = 0

    def flush() -> None:
        nonlocal skipped
        if skipped:
            rendered.append(
                f"<!-- {skipped} more similar <{(run_signature or '').split('|')[0]}> -->"
            )
            skipped = 0

    child = node.child
    while child is not None:
        child_tag = child.tag or ""
        if child_tag == "-text":
            text = clean_text(redact_text(child.text(deep=False)), max_length=style.text_chars)
            if text:
                flush()
                run_signature, run_length = None, 0
                rendered.append(text)
        elif not child_tag.startswith("-") and child_tag not in _SKIPPED:
            signature = _signature(child)
            if signature == run_signature:
                run_length += 1
            else:
                flush()
                run_signature, run_length = signature, 1
            if run_length <= style.keep_similar:
                piece = _render_node(child, style, depth + 1)
                if piece:
                    rendered.append(piece)
            else:
                skipped += 1
        child = child.next
    flush()

    separator = "\n" if tag in _BLOCK else ""
    inner = separator.join(rendered)
    if not inner:
        return (
            opening + f"</{tag}>"
            if (node.attributes.get("id") or node.attributes.get("class"))
            else ""
        )
    return f"{opening}{separator}{inner}{separator}</{tag}>"
