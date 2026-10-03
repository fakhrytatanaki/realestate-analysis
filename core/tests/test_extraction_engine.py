"""The rule engine against real archived captures of every site generation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from realestate.application.rules.seeds import EVIDENCE_NODE, seed_navigation_graph
from realestate.domain.archive import ArchivedDocument
from realestate.domain.enums import (
    LinkRel,
    ListingType,
    NodeKind,
    PageKind,
    PriceType,
    PropertyType,
    RouteDecision,
    RuleDomain,
)
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode
from realestate.infrastructure.extraction.document import ParsedDocument
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.extraction.normalisers import (
    parse_area,
    parse_date,
    parse_int,
    parse_price,
)
from realestate.infrastructure.extraction.template import run_template
from realestate.infrastructure.extraction.text import absolute_url
from tests.archive_fakes import TEMPLATES, fixture_document

ENGINE = HtmlRuleEngine()
LIST_FIXTURES = [
    "a1_2011_list",
    "a2_2013_list",
    "b_2015_list",
    "c_2019_list",
    "c_2021_list",
    "d_2023_list",
]


def graph_with(*names: str) -> RuleGraph:
    nodes = [RuleNode(name, NodeKind.TEMPLATE, TEMPLATES[name]["template"]) for name in names]
    edges = [
        RuleEdge(ROOT_KEY, name, TEMPLATES[name]["condition"], priority=10 * i)
        for i, name in enumerate(names)
    ]
    return RuleGraph.empty("olx", RuleDomain.EXTRACTION).extended(
        nodes=nodes, edges=edges, vocab=TEMPLATES["vocab"]
    )


# -- normalisers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("ج.م15,000", "15000", "EGP"),
        ("35,500ج.م", "35500", "EGP"),
        ("500,000 جنيه مصري", "500000", "EGP"),
        ("377,000 EGP", "377000", "EGP"),
        ("3b , Red Sea - 126322 GBP", "126322", "GBP"),
        ("1.2 مليون جنيه", "1200000", "EGP"),
        ("٣٥٠٠ جنيه", "3500", "EGP"),
        ("$ 120,000", "120000", "USD"),
        ("1.5m EGP", "1500000", "EGP"),
    ],
)
def test_parse_price_handles_archived_formats(text: str, amount: str, currency: str) -> None:
    parsed = parse_price(text, listing_type=ListingType.SALE)
    assert parsed.amount == Decimal(amount)
    assert parsed.currency == currency


def test_parse_price_types() -> None:
    assert parse_price("5,500 EGP", listing_type=ListingType.RENT).price_type is PriceType.PER_MONTH
    assert (
        parse_price("بمقدم 132 ألف", listing_type=ListingType.SALE).price_type
        is PriceType.INSTALLMENT
    )
    assert parse_price("0").price_type is PriceType.UNKNOWN
    assert parse_price("0").amount is None
    assert parse_price("Price on request").price_type is PriceType.ON_REQUEST
    assert parse_price("900", default_currency="EGP").currency == "EGP"


def test_placeholder_prices_are_unknown() -> None:
    """Archived adverts post "ج.م1.00" to get past a required price field."""
    for text in ("ج.م1.00", "ج.م8.00", "23 EGP"):
        parsed = parse_price(text, listing_type=ListingType.SALE)
        assert (parsed.amount, parsed.price_type) == (None, PriceType.UNKNOWN)
    assert parse_price("ج.م150", listing_type=ListingType.RENT).amount == Decimal("150.00")
    assert parse_price("$40 per night", listing_type=ListingType.RENT).amount == Decimal("40.00")


def test_parse_area_and_counts() -> None:
    assert parse_area("106.0 م٢") == Decimal("106.00")
    assert parse_area("Plot Area : 650 M") == Decimal("650.00")  # metres, not millions
    assert parse_area("1200 sq ft") == Decimal("111.48")
    assert parse_int("2 نوم") == 2
    assert parse_int("غرفتين") == 2
    assert parse_int("Bedrooms: 3") == 3


CAPTURE_2013 = datetime(2013, 3, 5, 3, 26, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "captured", "expected"),
    [
        ("02 Mar", CAPTURE_2013, datetime(2013, 3, 2, tzinfo=UTC)),
        ("28 Dec", CAPTURE_2013, datetime(2012, 12, 28, tzinfo=UTC)),  # yearless, would be future
        ("21 سبتمبر 2015", None, datetime(2015, 9, 21, tzinfo=UTC)),
        ("2019-04-04", None, datetime(2019, 4, 4, tzinfo=UTC)),
        ("13 hours and 4 minutes ago", CAPTURE_2013, datetime(2013, 3, 4, 14, 22, tzinfo=UTC)),
        ("منذ يومين", CAPTURE_2013, datetime(2013, 3, 3, 3, 26, tzinfo=UTC)),
        ("Yesterday", CAPTURE_2013, datetime(2013, 3, 4, 3, 26, tzinfo=UTC)),
        ("1362450000", CAPTURE_2013, datetime(2013, 3, 5, 2, 20, tzinfo=UTC)),
    ],
)
def test_parse_date_resolves_against_the_capture(
    text: str, captured: datetime | None, expected: datetime
) -> None:
    assert parse_date(text, captured_at=captured) == expected


def test_parse_date_rejects_dates_after_the_capture() -> None:
    assert parse_date("2014-01-01", captured_at=CAPTURE_2013) is None


def test_absolute_url_encodes_arabic_and_unwraps_wayback() -> None:
    assert absolute_url("/ad/دراجه-ID1.html", "https://www.olx.com.eg/en/") == (
        "https://www.olx.com.eg/ad/%D8%AF%D8%B1%D8%A7%D8%AC%D9%87-ID1.html"
    )
    assert (
        absolute_url("https://web.archive.org/web/2013id_/http://x.eg/a#f", "http://x.eg/")
        == "http://x.eg/a"
    )
    assert absolute_url("javascript:void(0)", "http://x.eg/") is None


# -- templates on every generation -------------------------------------------


@pytest.mark.parametrize("name", LIST_FIXTURES)
def test_every_generation_is_expressible_as_a_template(name: str) -> None:
    outcome = ENGINE.extract(graph_with(name), fixture_document(name), country_code="EG")

    assert outcome is not None, f"{name} not recognised"
    assert outcome.page_kind is PageKind.LIST
    assert outcome.items_valid >= 10
    assert outcome.items_valid == outcome.items_total
    for draft in outcome.drafts:
        assert draft.title and draft.external_id
        assert draft.observed_at == fixture_document(name).captured_at
        assert draft.is_active is False  # archived: liveness unknowable
        assert draft.attributes["_extraction"]["template"] == name
    priced = [d for d in outcome.drafts if d.price.amount is not None]
    assert len(priced) >= len(outcome.drafts) // 2
    assert any(link.rel is LinkRel.DETAIL for link in outcome.links)


def test_2013_list_reads_gbp_from_titles_and_yearless_dates() -> None:
    outcome = ENGINE.extract(
        graph_with("a2_2013_list"), fixture_document("a2_2013_list"), country_code="EG"
    )
    assert outcome is not None
    by_id = {draft.external_id: draft for draft in outcome.drafts}
    first = by_id["iid-487590261"]
    assert (first.price.amount, first.price.currency) == (Decimal("35500.00"), "EGP")
    assert first.listed_at == datetime(2013, 3, 2, tzinfo=UTC)
    gbp = by_id["iid-487301158"]
    assert (gbp.price.amount, gbp.price.currency, gbp.bedrooms) == (Decimal("126322.00"), "GBP", 3)
    assert any(link.rel is LinkRel.PAGINATION and "-p-2" in link.url for link in outcome.links)


def test_2019_list_reads_data_ninja_json() -> None:
    outcome = ENGINE.extract(
        graph_with("c_2019_list"), fixture_document("c_2019_list"), country_code="EG"
    )
    assert outcome is not None
    draft = next(d for d in outcome.drafts if d.external_id == "148306342")
    assert draft.location.name == "Alexandria"
    assert draft.listed_at == datetime(2019, 4, 4, tzinfo=UTC)
    assert draft.price.amount == Decimal("377000.00")
    assert draft.attributes["seller_type"] == "private"
    assert draft.property_type is PropertyType.APARTMENT


def test_2023_list_reads_embedded_state_and_builds_encoded_urls() -> None:
    outcome = ENGINE.extract(
        graph_with("d_2023_list"), fixture_document("d_2023_list"), country_code="EG"
    )
    assert outcome is not None
    draft = outcome.drafts[0]
    assert draft.external_id == "196255040"
    assert draft.listing_type is ListingType.RENT
    assert (draft.bedrooms, draft.area_sqm) == (3, Decimal("234.00"))
    assert draft.url is not None and draft.url.isascii() and "-ID196255040.html" in draft.url


def test_title_beats_coarse_category_for_property_type() -> None:
    outcome = ENGINE.extract(
        graph_with("a1_2011_list"), fixture_document("a1_2011_list"), country_code="EG"
    )
    assert outcome is not None
    villa = next(d for d in outcome.drafts if d.external_id == "iid-191449087")
    assert villa.property_type is PropertyType.VILLA


def test_detail_template_and_other_pages() -> None:
    detail = ENGINE.extract(
        graph_with("a2_2013_detail"), fixture_document("a2_2013_detail"), country_code="EG"
    )
    assert detail is not None and detail.page_kind is PageKind.DETAIL
    assert [d.external_id for d in detail.drafts] == ["568736822"]
    assert detail.drafts[0].bathrooms == 1

    other = ENGINE.extract(
        graph_with("c_2019_detail_other"),
        fixture_document("c_2019_detail_other"),
        country_code="EG",
    )
    assert other is not None and other.page_kind is PageKind.OTHER and other.drafts == []


def test_unknown_design_is_a_miss() -> None:
    graph = graph_with("a2_2013_list")
    assert ENGINE.extract(graph, fixture_document("c_2021_list"), country_code="EG") is None


def test_matching_but_empty_template_falls_through_to_the_next() -> None:
    """A too-generic early rule must not shadow a working later one."""
    greedy = RuleNode(
        "greedy",
        NodeKind.TEMPLATE,
        {"page_kind": "LIST", "items": {"css": "li.nope"}, "fields": {"title": [{"css": "a"}]}},
    )
    graph = graph_with("a2_2013_list").extended(
        nodes=[greedy],
        edges=[RuleEdge(ROOT_KEY, "greedy", {"type": "dom_css", "css": "#the-list"}, priority=-1)],
    )
    outcome = ENGINE.extract(graph, fixture_document("a2_2013_list"), country_code="EG")
    assert outcome is not None and outcome.template_key == "a2_2013_list"


def test_price_stated_only_in_the_title_needs_a_currency_marker() -> None:
    full = TEMPLATES["a2_2013_list"]["template"]
    # No title fallback in the template's own price field.
    fields = {**full["fields"], "price": [{"css": ".no-price-column"}]}
    graph = RuleGraph.empty("olx", RuleDomain.EXTRACTION).extended(
        nodes=[RuleNode("t", NodeKind.TEMPLATE, {**full, "fields": fields})],
        edges=[RuleEdge(ROOT_KEY, "t", {"type": "dom_css", "css": "#the-list"})],
        vocab=TEMPLATES["vocab"],
    )
    outcome = ENGINE.extract(graph, fixture_document("a2_2013_list"), country_code="EG")
    assert outcome is not None
    gbp = [d for d in outcome.drafts if d.title.endswith("GBP")]
    assert gbp and all(d.price.currency == "GBP" and d.price.amount for d in gbp)
    assert gbp[0].attributes["_raw"]["price_source"] == "title"
    # Titles without a currency marker never yield a price ("3 bedrooms").
    assert all(
        d.price.amount is None for d in outcome.drafts if not re.search(r"GBP|USD|EUR", d.title)
    )


def test_overlapping_templates_pick_the_richest() -> None:
    """A sparser template earlier in priority must not win over a fuller one."""
    full = TEMPLATES["a2_2013_list"]["template"]
    sparse_fields = {k: v for k, v in full["fields"].items() if k not in ("bedrooms", "listed_at")}
    sparse = RuleNode("sparse", NodeKind.TEMPLATE, {**full, "fields": sparse_fields})
    graph = graph_with("a2_2013_list").extended(
        nodes=[sparse],
        edges=[RuleEdge(ROOT_KEY, "sparse", {"type": "dom_css", "css": "#the-list"}, priority=-1)],
    )
    outcome = ENGINE.extract(graph, fixture_document("a2_2013_list"), country_code="EG")
    assert outcome is not None and outcome.template_key == "a2_2013_list"


def test_vocabulary_gaps_are_reported() -> None:
    name = "c_2021_list"
    graph = RuleGraph.empty("olx", RuleDomain.EXTRACTION).extended(
        nodes=[RuleNode(name, NodeKind.TEMPLATE, TEMPLATES[name]["template"])],
        edges=[RuleEdge(ROOT_KEY, name, TEMPLATES[name]["condition"])],
    )
    report = ENGINE.evaluate_candidate(
        graph,
        node=graph.node(name),
        condition=TEMPLATES[name]["condition"],
        document=fixture_document(name),
        document_ref=name,
        country_code="EG",
    )
    assert report.condition_matched and report.outcome is not None
    assert report.outcome.items_valid == 0 and report.outcome.vocab_misses


# -- conditions, routing, fingerprints ---------------------------------------


def test_check_condition_rejects_unusable_conditions() -> None:
    assert (
        ENGINE.check_condition({"type": "dom_css", "css": "div#the-list > div.li", "min": 3}) == []
    )
    assert ENGINE.check_condition({"type": "dom_css", "css": "li["})
    assert ENGINE.check_condition({"type": "text_regex", "pattern": "(unclosed"})
    assert ENGINE.check_condition({"type": "dom_css", "css": "div"})  # identifies nothing
    assert ENGINE.check_condition({"type": "url_regex", "pattern": "-cat-"})  # URLs outlive designs
    assert ENGINE.check_condition({"type": "magic"})


def test_routing_with_seed_graph_and_evidence() -> None:
    graph = seed_navigation_graph("olx").extended(
        nodes=[RuleNode("skip_cars", NodeKind.ROUTE, {"decision": "SKIP"})],
        edges=[
            RuleEdge(
                ROOT_KEY, "skip_cars", {"type": "url_regex", "pattern": "/vehicles/"}, priority=200
            )
        ],
    )
    assert ENGINE.route(graph, "https://olx.com.eg/ad/-ID9v1Io.html", {}) is None
    followed = ENGINE.route(graph, "https://olx.com.eg/ad/-ID9v1Io.html", {"linked_as": ["DETAIL"]})
    assert followed is not None
    assert (followed.decision, followed.node_key, followed.page_kind) == (
        RouteDecision.FETCH,
        EVIDENCE_NODE,
        PageKind.DETAIL,
    )
    skipped = ENGINE.route(graph, "https://olx.com.eg/vehicles/cars/", {})
    assert skipped is not None and skipped.decision is RouteDecision.SKIP


def test_fingerprints_cluster_designs() -> None:
    same = ENGINE.fingerprint_distance(
        ENGINE.fingerprint(fixture_document("a2_2013_list")),
        ENGINE.fingerprint(fixture_document("a2_2013_list_alex")),
    )
    different = ENGINE.fingerprint_distance(
        ENGINE.fingerprint(fixture_document("a2_2013_list")),
        ENGINE.fingerprint(fixture_document("a1_2011_list")),
    )
    assert same <= 10 < different


def test_prompt_view_is_compact_and_surfaces_embedded_json() -> None:
    view = ENGINE.prompt_view(fixture_document("d_2023_list"), budget_chars=24_000)
    assert len(view) <= 24_000
    assert 'path "algolia.content.hits"' in view
    listing = ENGINE.prompt_view(fixture_document("a2_2013_list"), budget_chars=24_000)
    assert "more similar <div>" in listing and "<script" not in listing


# 2013 city-subdomain list rows, as captured (cairocity.olx.com.eg).
_ROWS_2013 = """
<div id="the-list">
 <div class="li">
  <h3><a href="/villa-for-sale-in-mivida-iid-469672672"
         title="Villa for sale in Mivida compound, New Cairo - al-Qāhirah">
     Villa for sale in Mivida compound, New Cairo</a></h3>
  <div class="c-4"><span>Bedrooms: 3</span><span>Bathrooms: 2</span>
   <span>Square Meters: 560</span></div>
  <div class="itemlistinginfo"><a>Houses - Apartments for Sale - al-Qāhirah</a></div>
  <div class="third-column-container">ج.م4,000,000</div>
 </div>
 <div class="li">
  <h3><a href="/modern-apartment-iid-319193135"
         title="Modern Apartment Lovely Kitchen - al-Qāhirah">
     Modern Apartment Lovely Kitchen</a></h3>
  <div class="c-4"><span>Bedrooms: 3</span><span>Bathrooms: 3</span></div>
  <div class="itemlistinginfo"><a>Houses - Apartments for Rent - al-Qāhirah</a></div>
  <div class="third-column-container">ج.م1.00</div>
 </div>
</div>"""


def test_list_rows_with_label_value_spans_and_combined_categories() -> None:
    document = ParsedDocument(
        ArchivedDocument.from_payload(
            _ROWS_2013.encode(),
            content_type="text/html",
            source_url="http://cairocity.olx.com.eg/real-estate-cat-16",
            meta={"timestamp": "20130425050016"},
        )
    )
    spec = {
        "page_kind": "LIST",
        "items": {"css": "#the-list .li"},
        "fields": {
            "title": [{"css": "h3 a", "attr": "title"}],
            "url": [{"css": "h3 a", "attr": "href"}],
            "external_id": [{"css": "h3 a", "attr": "href", "regex": r"iid-(\d+)"}],
            "price": [{"css": ".third-column-container"}],
            # The category is mapped as location only, and as property type.
            "location": [{"css": ".itemlistinginfo a"}],
            "property_type": [{"css": ".itemlistinginfo a"}],
            "bedrooms": [{"css": ".c-4 span", "regex": r"Bedrooms:\s*(\d+)"}],
            "bathrooms": [{"css": ".c-4 span", "regex": r"Bathrooms:\s*(\d+)"}],
            "area": [{"css": ".c-4 span", "regex": r"Square Meters:\s*([\d,]+)"}],
        },
        "default_currency": "EGP",
    }
    vocab = {
        # "rent" alone, so neither title says sale/rent: only the row text does.
        "listing_type": [
            {"value": "RENT", "pattern": "for rent"},
            {"value": "SALE", "pattern": "for sale"},
        ],
        "property_type": [
            {"value": "VILLA", "pattern": "villa"},
            {"value": "APARTMENT", "pattern": "apartment"},
            {"value": "HOUSE", "pattern": "house"},
        ],
    }
    result = run_template(
        spec, document, vocab=vocab, template_key="t", graph_version=1, country_code="EG"
    )

    villa, flat = result.drafts
    assert villa.title == "Villa for sale in Mivida compound, New Cairo"  # no " - al-Qāhirah"
    assert (villa.bedrooms, villa.bathrooms, villa.area_sqm) == (3, 2, Decimal("560"))
    # "Houses - Apartments" names two types, so the title decides.
    assert villa.property_type is PropertyType.VILLA
    assert villa.listing_type is ListingType.SALE
    # Only the row's category text says "for Rent".
    assert flat.listing_type is ListingType.RENT
    assert flat.property_type is PropertyType.APARTMENT
    assert flat.price.amount is None  # "ج.م1.00" is a placeholder
    assert "area" not in result.empty_fields

    # With no field holding the category at all, the row text still decides.
    bare = {**spec, "fields": {k: v for k, v in spec["fields"].items() if k != "location"}}
    rerun = run_template(
        bare, document, vocab=vocab, template_key="t", graph_version=1, country_code="EG"
    )
    assert [d.listing_type for d in rerun.drafts] == [ListingType.SALE, ListingType.RENT]
