"""Running one extraction template (the action of a ``TEMPLATE`` state).

A template is plain JSON, written by the LLM during induction::

    {
      "page_kind": "LIST" | "DETAIL" | "OTHER",
      "items": {"css": "div#the-list > div.li"}            # repeated elements
             | {"script": "window.state", "path": "algolia.content.hits"}
             | null,                                       # DETAIL: page = 1 item
      "fields": {
        "title":  [{"css": "h3 a"}],                       # alternatives, in order
        "url":    [{"css": "h3 a", "attr": "href"}],
        "price":  [{"css": ".price"}, {"css": "h3 a", "regex": "(\\d[\\d,]*\\s*GBP)"}],
        "listed_at": [{"css": ".date"}],
        "category": [{"css": ".cat", "scope": "item"}, {"css": "h1", "scope": "page"}],
        "extra.seller_type": [{"attr": "data-ninja", "decode": "urlencoded_json",
                               "json": "sellerType"}]
      },
      "links": [{"css": "a.next", "rel": "PAGINATION"}],
      "default_currency": "EGP",
      "vocab": {"listing_type": [{"pattern": "for rent|للايجار", "value": "RENT"}], ...}
    }

Field specs locate a *string*; typed values come from :mod:`normalisers`. A
field spec may use ``css`` (relative to the item, or the page with
``"scope": "page"``), ``attr`` (else text), ``decode`` + ``json`` (JSON inside
an attribute), ``json`` (inside a JSON item), ``script`` + ``json`` (page-level
embedded JSON), ``template`` (``"/ad/{slug}-ID{externalID}.html"`` filled from a
JSON item), ``regex`` (group 1 if present) and ``const``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from selectolax.lexbor import LexborNode

from realestate.domain.archive import DiscoveredLink, surt_key
from realestate.domain.enums import LinkRel, ListingType, PageKind, PriceType, PropertyType
from realestate.domain.models import ListingDraft, Location, Price
from realestate.infrastructure.extraction.conditions import compiled
from realestate.infrastructure.extraction.document import ParsedDocument, select
from realestate.infrastructure.extraction.jsondata import decode_attribute, first_scalar, resolve
from realestate.infrastructure.extraction.normalisers import (
    detect_currency,
    parse_area,
    parse_date,
    parse_int,
    parse_price,
)
from realestate.infrastructure.extraction.text import absolute_url, clean_text

#: Fields a template may define; ``extra.<name>`` lands in ``attributes``.
KNOWN_FIELDS = frozenset(
    {
        "title",
        "url",
        "external_id",
        "price",
        "currency",
        "listed_at",
        "location",
        "city",
        "district",
        "category",
        "listing_type",
        "property_type",
        "bedrooms",
        "bathrooms",
        "area",
        "description",
        "image",
    }
)
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
#: DB column limits (see ``ListingModel``).
_MAX_TITLE = 512
_MAX_EXTERNAL_ID = 255
_MAX_URL = 2048


class TemplateError(ValueError):
    """A template is structurally unusable (unknown page kind, bad items spec)."""


@dataclass(slots=True)
class _Item:
    """One advert's scope: an element, a JSON object, or the whole page."""

    element: LexborNode | None = None
    data: Any = None
    whole_page: bool = False


@dataclass(slots=True)
class TemplateResult:
    page_kind: PageKind
    drafts: list[ListingDraft] = field(default_factory=list)
    links: list[DiscoveredLink] = field(default_factory=list)
    items_total: int = 0
    problems: list[str] = field(default_factory=list)
    vocab_misses: list[str] = field(default_factory=list)
    #: Declared fields that produced no value on any item.
    empty_fields: list[str] = field(default_factory=list)


def run_template(
    spec: Mapping[str, Any],
    document: ParsedDocument,
    *,
    vocab: Mapping[str, Sequence[Mapping[str, Any]]],
    template_key: str,
    graph_version: int,
    country_code: str | None,
) -> TemplateResult:
    """Apply ``spec`` to ``document``.

    Raises:
        TemplateError: when the spec itself is unusable.
        SelectorError: when a selector cannot be parsed.
    """
    try:
        page_kind = PageKind(str(spec.get("page_kind", "LIST")).upper())
    except ValueError as exc:
        raise TemplateError(f"unknown page_kind {spec.get('page_kind')!r}") from exc
    result = TemplateResult(page_kind=page_kind)
    if page_kind is PageKind.OTHER:
        return result

    fields = _normalise_fields(spec.get("fields") or {})
    merged_vocab = _merge_vocab(vocab, spec.get("vocab") or {})
    items = _items(spec.get("items"), document, page_kind)
    result.items_total = len(items)
    seen_ids: set[str] = set()
    filled: set[str] = set()

    for index, item in enumerate(items):
        values = {
            name: _field_value(alternatives, item, document)
            for name, alternatives in fields.items()
        }
        filled.update(name for name, value in values.items() if value)
        draft, problem, miss = _build_draft(
            values,
            item=item,
            document=document,
            vocab=merged_vocab,
            default_currency=spec.get("default_currency"),
            template_key=template_key,
            graph_version=graph_version,
            country_code=country_code,
        )
        if miss:
            result.vocab_misses.append(miss)
        if draft is None:
            result.problems.append(f"item {index}: {problem}")
            continue
        if draft.external_id in seen_ids:
            continue
        seen_ids.add(draft.external_id)
        result.drafts.append(draft)
        if draft.url and page_kind is PageKind.LIST:
            result.links.append(DiscoveredLink(url=draft.url, rel=LinkRel.DETAIL))

    if items:
        result.empty_fields = [name for name in fields if name not in filled]
    result.links.extend(_links(spec.get("links") or [], document))
    result.links = _unique_links(result.links, exclude=document.url)
    return result


# -- items and fields -----------------------------------------------------


def _items(items_spec: Any, document: ParsedDocument, page_kind: PageKind) -> list[_Item]:
    if not items_spec:
        if page_kind is PageKind.LIST:
            raise TemplateError("a LIST template needs an items spec")
        return [_Item(element=document.tree.body, whole_page=True)]
    if not isinstance(items_spec, Mapping):
        raise TemplateError("items must be an object")
    if items_spec.get("css"):
        return [_Item(element=node) for node in select(document.tree, str(items_spec["css"]))]
    if items_spec.get("script"):
        data = document.scripts.get(str(items_spec["script"]))
        if data is None:
            return []
        found = resolve(data, items_spec.get("path"))
        if len(found) == 1 and isinstance(found[0], list):
            found = found[0]
        return [_Item(data=entry) for entry in found if isinstance(entry, dict)]
    raise TemplateError("items needs 'css' or 'script'")


def _normalise_fields(raw: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    fields: dict[str, list[Mapping[str, Any]]] = {}
    for name, value in raw.items():
        if name not in KNOWN_FIELDS and not name.startswith("extra."):
            continue
        alternatives = value if isinstance(value, list) else [value]
        fields[name] = [alt for alt in alternatives if isinstance(alt, Mapping)]
    return fields


def _field_value(
    alternatives: Sequence[Mapping[str, Any]], item: _Item, document: ParsedDocument
) -> str | None:
    for spec in alternatives:
        value = _one_value(spec, item, document)
        if value:
            return value
    return None


def _one_value(spec: Mapping[str, Any], item: _Item, document: ParsedDocument) -> str | None:
    if spec.get("const") is not None:
        return str(spec["const"])
    scope_page = spec.get("scope") == "page"
    raw: str | None

    if spec.get("template"):
        source = item.data if (item.data is not None and not scope_page) else None
        raw = _fill(str(spec["template"]), source) if source is not None else None
    elif spec.get("script"):
        data = document.scripts.get(str(spec["script"]))
        raw = first_scalar(resolve(data, spec.get("json"))) if data is not None else None
    elif item.data is not None and not scope_page and not spec.get("css"):
        raw = first_scalar(resolve(item.data, spec.get("json")))
    else:
        scope: Any = document.tree if (scope_page or item.element is None) else item.element
        if spec.get("css"):
            nodes = select(scope, str(spec["css"]))
            if "index" in spec:
                index = int(spec["index"])
                nodes = [nodes[index]] if -len(nodes) <= index < len(nodes) else []
            elif not spec.get("regex"):
                nodes = nodes[:1]
        else:
            nodes = [scope] if isinstance(scope, LexborNode) else []
        # With a regex and no index, the first node whose text matches wins:
        # "label: value" rows (Bedrooms:, Bathrooms:) usually share one selector.
        for node in nodes:
            value = _apply_regex(spec, _node_value(spec, node))
            if value:
                return value
        return None

    return _apply_regex(spec, raw)


def _node_value(spec: Mapping[str, Any], node: LexborNode) -> str | None:
    if not spec.get("attr"):
        return node.text(deep=True, separator=" ")
    attr = str(spec["attr"])
    value = node.attributes.get(attr)
    if spec.get("decode"):
        decoded = decode_attribute(value, str(spec["decode"]))
        return first_scalar(resolve(decoded, spec.get("json")))
    if attr in ("title", "alt") and value:
        # Tooltips often repeat the visible text plus a suffix ("... - Cairo");
        # the visible text is the cleaner value when it is a prefix of it.
        visible = clean_text(node.text(deep=True, separator=" "))
        if len(visible) >= 8 and clean_text(value).startswith(visible):
            return visible
    return value


def _apply_regex(spec: Mapping[str, Any], raw: str | None) -> str | None:
    if raw and spec.get("regex"):
        match = compiled(str(spec["regex"]), re.IGNORECASE | re.DOTALL).search(raw)
        if match is None:
            return None
        raw = (
            next((group for group in match.groups() if group), match.group(0))
            if match.groups()
            else match.group(0)
        )
    return clean_text(raw) or None


def _fill(template: str, data: Any) -> str | None:
    missing = False

    def substitute(match: re.Match[str]) -> str:
        nonlocal missing
        value = first_scalar(resolve(data, match.group(1)))
        if value is None:
            missing = True
            return ""
        return value

    filled = _PLACEHOLDER.sub(substitute, template)
    return None if missing else filled


def _links(link_specs: Sequence[Any], document: ParsedDocument) -> list[DiscoveredLink]:
    links: list[DiscoveredLink] = []
    for spec in link_specs:
        if not isinstance(spec, Mapping) or not spec.get("css"):
            continue
        try:
            rel = LinkRel(str(spec.get("rel", "LIST")).upper())
        except ValueError:
            continue
        pattern = compiled(str(spec["regex"])) if spec.get("regex") else None
        for node in select(document.tree, str(spec["css"])):
            url = absolute_url(node.attributes.get(str(spec.get("attr", "href"))), document.url)
            if url and (pattern is None or pattern.search(url)):
                links.append(DiscoveredLink(url=url, rel=rel))
    return links


def _unique_links(links: Sequence[DiscoveredLink], *, exclude: str) -> list[DiscoveredLink]:
    excluded = surt_key(exclude)
    seen: set[str] = set()
    out: list[DiscoveredLink] = []
    for link in links:
        key = surt_key(link.url)
        if key == excluded or key in seen:
            continue
        seen.add(key)
        out.append(link)
    return out


# -- vocabulary and drafts ------------------------------------------------


def _merge_vocab(
    base: Mapping[str, Sequence[Mapping[str, Any]]], extra: Mapping[str, Any]
) -> dict[str, list[Mapping[str, Any]]]:
    merged = {key: list(entries) for key, entries in base.items()}
    for key, entries in extra.items():
        if isinstance(entries, list):
            merged.setdefault(key, []).extend(e for e in entries if isinstance(e, Mapping))
    return merged


def _vocab_lookup(
    entries: Sequence[Mapping[str, Any]],
    texts: Sequence[str | None],
    *,
    unambiguous: bool = False,
) -> str | None:
    """The value of the first entry matching the first text that matches any.

    With ``unambiguous``, a text matching entries of different values is
    skipped in favour of the next text.
    """
    for text in texts:
        if not text:
            continue
        found: list[str] = []
        for entry in entries:
            pattern = entry.get("pattern")
            if not pattern:
                continue
            try:
                if compiled(str(pattern)).search(text):
                    value = str(entry.get("value", "")).upper()
                    if value and value not in found:
                        found.append(value)
                    if not unambiguous:
                        break
            except re.error:
                continue
        if len(found) == 1 or (found and not unambiguous):
            return found[0]
    return None


def _build_draft(
    values: Mapping[str, str | None],
    *,
    item: _Item,
    document: ParsedDocument,
    vocab: Mapping[str, Sequence[Mapping[str, Any]]],
    default_currency: Any,
    template_key: str,
    graph_version: int,
    country_code: str | None,
) -> tuple[ListingDraft | None, str | None, str | None]:
    """``(draft, problem, vocab_miss)``; exactly one of draft/problem is set."""
    title = clean_text(values.get("title"), max_length=_MAX_TITLE)
    if not title:
        return None, "no title", None

    url = absolute_url(values.get("url"), document.url)
    if url is None and item.whole_page:
        url = absolute_url(document.url, document.url)
    if url and len(url) > _MAX_URL:
        url = None

    external_id = clean_text(values.get("external_id"))
    if not external_id and url:
        external_id = "u:" + hashlib.sha1(surt_key(url).encode()).hexdigest()[:20]
    if not external_id:
        return None, "no external id and no url", None
    external_id = external_id[:_MAX_EXTERNAL_ID]

    category = clean_text(values.get("category"))
    # The item's own text before page-wide context: list rows often carry the
    # category ("Houses - Apartments for Sale - Alexandria") without the
    # template mapping it to a field.
    item_text = (
        clean_text(item.element.text(deep=True, separator=" "))[:2000]
        if item.element is not None and not item.whole_page
        else None
    )
    context = [
        values.get("listing_type"),
        category,
        title,
        values.get("location"),
        item_text,
        document.url,
        document.title,
    ]
    listing_value = _vocab_lookup(vocab.get("listing_type", []), context)
    try:
        listing_type = ListingType(listing_value) if listing_value else None
    except ValueError:
        listing_type = None
    if listing_type is None:
        return None, "listing type unknown (no vocabulary match)", category or title

    # The title before the category: categories are coarse ("Houses -
    # Apartments") while titles name the thing ("luxury villa", "مكتب"). A
    # text naming several types (that same category, even as the template's
    # property_type field) says nothing and is skipped.
    property_value = _vocab_lookup(
        vocab.get("property_type", []),
        [values.get("property_type"), title, category, document.url],
        unambiguous=True,
    )
    try:
        property_type = PropertyType(property_value) if property_value else PropertyType.OTHER
    except ValueError:
        property_type = PropertyType.OTHER

    parsed = parse_price(
        values.get("price"),
        listing_type=listing_type,
        default_currency=str(default_currency).upper() if default_currency else None,
    )
    price_from_title = False
    if parsed.amount is None and parsed.price_type is not PriceType.ON_REQUEST:
        # Some adverts state the price only in the title ("2b, Red Sea -
        # 39600 GBP"); trusted only with an explicit currency marker, so
        # "3 bedrooms" never becomes a price.
        title_currency, _ = detect_currency(title)
        if title_currency:
            from_title = parse_price(title, listing_type=listing_type)
            if from_title.amount is not None:
                parsed, price_from_title = from_title, True
    currency = parsed.currency
    explicit_currency = None if price_from_title else values.get("currency")
    if explicit_currency:
        detected, _ = detect_currency(explicit_currency)
        if detected:
            currency = detected
        elif re.fullmatch(r"[A-Za-z]{3}", explicit_currency.strip()):
            currency = explicit_currency.strip().upper()

    location_name = clean_text(values.get("location"), max_length=512) or "unknown"
    city = clean_text(values.get("city"), max_length=128) or None
    district = clean_text(values.get("district"), max_length=128) or None

    attributes: dict[str, Any] = {}
    image = absolute_url(values.get("image"), document.url)
    if image:
        attributes["image"] = image
    for name, value in values.items():
        if name.startswith("extra.") and value:
            attributes[name.removeprefix("extra.")] = value
    attributes["_extraction"] = {
        "template": template_key,
        "graph_version": graph_version,
        "page_url": document.url,
    }
    attributes["_raw"] = {
        key: values[key]
        for key in ("price", "listed_at", "category", "location", "area")
        if values.get(key)
    }
    if price_from_title:
        attributes["_raw"]["price_source"] = "title"

    area: Decimal | None = parse_area(values.get("area"))
    draft = ListingDraft(
        external_id=external_id,
        title=title,
        listing_type=listing_type,
        property_type=property_type,
        location=Location(
            name=location_name, country_code=country_code, city=city, district=district
        ),
        price=Price(
            amount=parsed.amount, currency=(currency or "XXX")[:3], price_type=parsed.price_type
        ),
        url=url,
        description=clean_text(values.get("description"), max_length=8000) or None,
        area_sqm=area,
        bedrooms=parse_int(values.get("bedrooms")),
        bathrooms=parse_int(values.get("bathrooms")),
        listed_at=parse_date(values.get("listed_at"), captured_at=document.captured_at),
        # Archived adverts: whether they are still live is unknowable.
        is_active=document.captured_at is None,
        attributes=attributes,
        observed_at=document.captured_at,
    )
    return draft, None, None
