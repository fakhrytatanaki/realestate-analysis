"""Prompts and tool schemas for rule induction.

Bump a ``*_PROMPT_VERSION`` whenever the wording changes: it is part of the
LLM decision cache key, so stale answers to an old prompt are not reused.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import unquote

from realestate.domain.rules import RuleGap, RuleGraph

NAV_PROMPT_VERSION = "nav-2"
TEMPLATE_PROMPT_VERSION = "tpl-2"

NAV_TOOL = "submit_rules"
NAV_TOOL_DESCRIPTION = "Submit URL routing rules, at least one per group."
NAV_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "group": {"type": "integer", "description": "group number the rule is for"},
                    "pattern": {
                        "type": "string",
                        "description": "Python regex searched in the URL",
                    },
                    "decision": {"type": "string", "enum": ["FETCH", "SKIP", "DEFER"]},
                    "page_kind": {"type": "string", "enum": ["LIST", "DETAIL", "OTHER"]},
                    "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                    "reason": {"type": "string"},
                },
                "required": ["group", "pattern", "decision", "page_kind", "priority"],
            },
        }
    },
    "required": ["rules"],
}

NAV_SYSTEM = """\
You route URLs for a crawler that collects real-estate classified adverts \
(property for sale or for rent) from archived snapshots of a classifieds website \
spanning many years and several redesigns.

You get numbered groups of URLs; the URLs in a group share a URL shape. Write \
routing rules so that every group is covered. A rule is a Python regular \
expression, searched case-insensitively anywhere in the full URL, plus a decision:

- FETCH: pages worth downloading. page_kind LIST for listing, search or category \
result pages of property / real estate (including their pagination); page_kind \
DETAIL for a single property advert.
- SKIP: pages that cannot contain property adverts: other categories (cars, \
vehicles, mobile phones, electronics, jobs, fashion, furniture, services, pets...), \
help, login, register, account, user profiles, static or legal pages, images.
- DEFER: pages that may be property adverts but whose URL does not say so, e.g. \
advert detail URLs without category words. Deferred URLs are fetched only if a \
property list page links to them, so DEFER rather than FETCH when unsure.

Prefer general patterns that capture structure (path prefixes, category slugs, \
id markers such as -iid-123 or -ID8xYz.html) over single URLs, but never so broad \
that they mix property and non-property pages. A group often mixes both (e.g. \
category pages for real estate and for jobs share one shape): then give several \
rules for it. Rules are tried in the order you list them, so put specific rules \
(e.g. FETCH real-estate category ids or property words) before general ones \
(e.g. SKIP every other category page). Arabic text may be percent-encoded; \
the decoded form is shown next to such URLs. Priority is 0-100, higher is fetched \
first: property LIST pages 80-95, property DETAIL pages 50-70, others anything."""

TEMPLATE_TOOL = "submit_template"
TEMPLATE_TOOL_DESCRIPTION = "Submit an extraction template for this page design."
TEMPLATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "page_kind": {"type": "string", "enum": ["LIST", "DETAIL", "OTHER"]},
        "conditions": {"type": "array", "items": {"type": "object"}},
        "items": {"type": ["object", "null"]},
        "fields": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "object"}},
        },
        "links": {"type": "array", "items": {"type": "object"}},
        "default_currency": {"type": ["string", "null"]},
        "default_rental_price_type": {"type": "string", "enum": ["PER_MONTH", "UNKNOWN"]},
        "required_fields": {"type": "array", "items": {"type": "string"}},
        "strict_classification": {"type": "boolean"},
        "allow_title_price": {"type": "boolean"},
        "vocab": {
            "type": "object",
            "properties": {
                "listing_type": {"type": "array", "items": {"type": "object"}},
                "property_type": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
    "required": ["name", "page_kind", "conditions", "fields"],
}

TEMPLATE_SYSTEM = """\
You write extraction templates for archived pages of a real-estate classifieds \
website. The archive spans many years and redesigns, in English and Arabic. A \
template recognises ONE page design and says where the advert data is in it.

Template keys:
- name: short snake_case description, e.g. "list_div_rows_2013".
- page_kind: LIST (a page listing several adverts), DETAIL (a page for one \
advert), OTHER (a page without property adverts: other categories, errors, empty \
results, help, home pages). Use OTHER only for pages with no property adverts.
- conditions: checks that ALL hold for pages of this design and should fail for \
other designs. Types:
  {"type":"dom_css","css":"<selector>","min":N}   at least N elements match
  {"type":"text_regex","pattern":"<regex>"}        regex found in the page text
  {"type":"json_path","script":"<name>","path":"<path>","min":N}   embedded JSON array/value present
  {"type":"url_regex","pattern":"<regex>"}         regex found in the URL
  Use distinctive ids/classes; never only "div", "body" or "a".
- items (LIST only): {"css":"<selector matching each advert element>"} or, when \
adverts sit in embedded JSON, {"script":"<name>","path":"<path to the array>"}. \
DETAIL: null.
  JSON items may add "filters":[{"path":"category.lvl0.slug","pattern":"properties"}]. \
Every filter must full-match its per-item value; rejected items are reported.
- fields: field name -> list of alternatives, tried in order. Names: title \
(required), url, external_id, price, currency, listed_at, location, city, \
district, category, listing_type, property_type, bedrooms, bathrooms, area, \
description, image, or extra.<name>. Each alternative locates TEXT:
  {"css":"<selector relative to the item>"}           text of the first match
  {"css":"...","attr":"href"}                         an attribute value
  {"css":"...","index":1}                             the second match
  {"attr":"data-x","decode":"urlencoded_json","json":"key"}   JSON inside an attribute \
of the item element (decode: "json" or "urlencoded_json")
  {"json":"path.to.value"}                            inside a JSON item
  {"template":"/ad/{slug}-ID{externalID}.html"}       a URL built from JSON item keys
  {"css":"h1","scope":"page"}                         search the whole page instead of the item
  {"script":"<name>","json":"<path>"}                 page-level embedded JSON
  {"const":"..."}                                     fixed text
  Add "regex":"..." to keep only the first capture group of the text.
  CSS3 only: no :contains, no XPath, no :scope; a leading ">" means direct child.
  Never convert values yourself: point at raw text like "35,500ج.م", "02 Mar", \
"منذ 3 أيام", "106 م٢"; they are parsed later. external_id must be a stable advert \
id (an attribute, or a regex on the advert URL). url is the advert link.
- links: further pages to crawl, e.g. next-page links: \
[{"css":"<selector>","rel":"PAGINATION"}] (rel: PAGINATION, LIST or DETAIL). Advert \
URLs from the url field are followed automatically.
- default_currency: ISO code to assume for prices without a currency marker \
(e.g. "EGP"), or null.
- default_rental_price_type: PER_MONTH (legacy default) or UNKNOWN when the \
capture does not establish a rental period. Explicit period text still wins.
- required_fields: declared fields that must be present before an item is emitted.
- strict_classification: true uses only the mapped listing_type/property_type \
fields for vocabulary matching; it prevents title or page context from overriding \
per-item taxonomy. Defaults to false for existing templates.
- allow_title_price: false prevents missing/placeholder price fields from being \
replaced by a currency-marked title amount. Defaults to true for existing templates.
- vocab: regex patterns that map category / title / URL text to listing_type \
("SALE" or "RENT") and property_type (APARTMENT, VILLA, TOWNHOUSE, HOUSE, STUDIO, \
LAND, OFFICE, SHOP, WAREHOUSE, OTHER). Cover the English and Arabic wording seen \
on the page, e.g. {"pattern":"for rent|للايجار|للإيجار","value":"RENT"}. Every \
advert needs a listing_type match, so include patterns for this page's wording.

Only use selectors, attributes and JSON keys that appear in the page shown."""


def _readable(url: str) -> str:
    decoded = unquote(url)
    return url if decoded == url else f"{url}   (decoded: {decoded})"


def nav_user_prompt(domain: str, gaps: Sequence[RuleGap], graph: RuleGraph, *, samples: int) -> str:
    existing = [
        f"- {edge.condition.get('pattern')} -> {graph.node(edge.to_key).action.get('decision')}"
        for edge in graph.edges
        if edge.condition.get("type") == "url_regex" and graph.has_node(edge.to_key)
    ][-25:]
    lines = [f"Site: {domain}"]
    if existing:
        lines += ["", "Rules that already exist (these URLs did not match them):", *existing]
    lines += ["", "Groups:"]
    for number, gap in enumerate(gaps, start=1):
        lines.append(f"[{number}] shape {gap.fingerprint}  ({gap.occurrences} URLs)")
        lines += [f"    {_readable(url)}" for url in gap.samples[:samples]]
    return "\n".join(lines)


def template_user_prompt(
    *, url: str, captured: str, page_view: str, vocab: Mapping[str, Any]
) -> str:
    vocab_text = json.dumps(vocab, ensure_ascii=False) if vocab else "none yet"
    return (
        f"URL: {_readable(url)}\nCaptured: {captured}\n"
        f"Vocabulary already known (reuse or extend): {vocab_text}\n\n"
        "PAGE (scripts removed, runs of similar elements collapsed to examples, "
        f"long text truncated):\n{page_view}"
    )


def template_feedback(errors: Sequence[str]) -> str:
    bullet_list = "\n".join(f"- {error}" for error in errors)
    return (
        "The template was tested and rejected:\n"
        f"{bullet_list}\n"
        "Fix these problems using only selectors and keys present in the page, and "
        f"call {TEMPLATE_TOOL} again with the complete corrected template."
    )
