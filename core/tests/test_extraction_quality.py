"""Correctness rules the wayback assessment found missing: identity, price, area, places.

Each test pins one failure mode observed in the archived OLX corpus, on a
minimal page shaped like the real one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from realestate.domain.archive import (
    ArchivedDocument,
    IdentityPolicy,
    capture_quarter,
    is_site_root,
    parse_timestamp,
    stratified_order,
)
from realestate.domain.enums import ListingType, NodeKind, PageKind, RuleDomain, RuleOrigin
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode
from realestate.infrastructure.extraction.document import ParsedDocument
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.extraction.geo import is_country_name, resolve_city
from realestate.infrastructure.extraction.normalisers import parse_area
from realestate.infrastructure.extraction.template import run_template
from realestate.infrastructure.sources.olx_eg_wayback.source import OLX_EG_IDENTITY
from tests.archive_fakes import TEMPLATES

ENGINE = HtmlRuleEngine()
VOCAB = TEMPLATES["vocab"]

#: A 2013 detail page as archived: the logo links home, the advert's own URL
#: is only in the share box (and in the archived URL itself).
DETAIL_2013 = """<html><body>
<a id="headerlogolink" href="http://www.olx.com.eg">OLX</a>
<div id="levelpath"><a id="firstpath2" href="/houses-apartments-for-sale-cat-367">
  Houses - Apartments for Sale</a></div>
<div id="the-item">
  <div id="olx_item_title"><span class="h1">Apartment for Sale in Nasr City
    &#8212; Cairo</span></div>
  <div class="item-highlights price">ج.م165,000 <span>Price</span></div>
  <div class="item-highlights">645 <span>Square Meters</span></div>
  <ul id="item-data"><li>al 'Ubūr, al-Qalyūbiyah, Egypt</li><li>User's other ads</li></ul>
  <div id="description-text"><dl><dt>Bedrooms:</dt><dd>3</dd><dt>Bathrooms:</dt><dd>2</dd>
    <dt>Square Meters:</dt><dd>645</dd></dl>Nice flat near the club.</div>
  <input type="text" class="share-url" value="http://cairo.olx.com.eg/apartment-iid-{iid}"/>
</div></body></html>"""


def detail(iid: str, url: str | None = None) -> ArchivedDocument:
    return ArchivedDocument(
        content=DETAIL_2013.replace("{iid}", iid).encode(),
        url=url or f"http://cairo.olx.com.eg:80/apartment-iid-{iid}",
        captured_at=parse_timestamp("20130821130341"),
    )


def graph_of(*nodes: tuple[str, dict, dict, RuleOrigin]) -> RuleGraph:
    return RuleGraph.empty("s", RuleDomain.EXTRACTION).extended(
        nodes=[
            RuleNode(key, NodeKind.TEMPLATE, action, origin) for key, _, action, origin in nodes
        ],
        edges=[
            RuleEdge(ROOT_KEY, key, condition, priority=10 * index)
            for index, (key, condition, _, _) in enumerate(nodes)
        ],
        vocab=VOCAB,
    )


#: The induced v6 recipe: url from the logo, id recipes that never match.
LOGO_DETAIL = {
    "page_kind": "DETAIL",
    "items": None,
    "fields": {
        "title": [{"css": "#olx_item_title .h1"}],
        "url": [{"css": "#headerlogolink", "attr": "href", "scope": "page"}],
        "external_id": [{"css": "#addToFavorites", "attr": "href", "regex": "insert=(\\\\d+)"}],
        "category": [{"css": "#levelpath a#firstpath2"}],
    },
}
ON_ITEM = {"type": "dom_css", "css": "#the-item"}


# -- domain helpers -------------------------------------------------------------


def test_identity_policy_reads_ids_and_rejects_non_adverts() -> None:
    policy = OLX_EG_IDENTITY
    assert policy.canonical_id("http://cairo.olx.com.eg/flat-iid-535715253") == "535715253"
    assert policy.canonical_id("http://alubur.olx.com.eg:80/iid-440752857") == "440752857"
    assert policy.canonical_id("https://www.olx.com.eg/ad/x-ID196255040.html") == "196255040"
    # i2-era base62 tokens are not the advert id.
    assert policy.canonical_id("https://olx.com.eg/en/i2/ad/50-IDa2h1b.html") is None
    for url in (
        "http://www.olx.com.eg",
        "http://www.olx.com.eg/",
        "http://cairo.olx.com.eg/houses-apartments-for-sale-cat-367",
        "http://www.olx.com.eg/real-estate-cat-16-p-8",
    ):
        assert policy.rejects(url) and policy.canonical_id(url) is None
    assert is_site_root("http://www.olx.com.eg:80") and not is_site_root("http://x.eg/?q=1")
    assert IdentityPolicy(id_patterns=(r"ad(\d+)",)).canonical_id("http://x.eg/ad7") == "7"


def test_capture_quarters_and_stratified_order() -> None:
    assert capture_quarter("20131027120000") == "2013Q4"
    assert capture_quarter("2011") == "2011Q1"
    rows = [
        # A dense October and one capture each in two other quarters.
        (1, "20131001000000", 50),
        (2, "20131002000000", 50),
        (3, "20131003000000", 90),
        (4, "20130101000000", 10),
        (5, "20110601000000", 10),
    ]
    order = stratified_order(rows)
    # Each quarter's best first (priority, then age), then the rest of 2013Q4.
    assert order[:3] == [3, 5, 4] and order[3:] == [1, 2]
    capped = stratified_order(rows, taken={"2013Q4": 1}, per_stratum=2)
    assert capped.count(1) + capped.count(2) + capped.count(3) == 1


def test_revised_graph_retires_states_with_their_edges() -> None:
    graph = graph_of(
        ("a", ON_ITEM, LOGO_DETAIL, RuleOrigin.LLM), ("b", ON_ITEM, LOGO_DETAIL, RuleOrigin.LLM)
    )
    revised = graph.revised(remove=["a"], notes="retire a")
    assert revised.version == graph.version + 1
    assert not revised.has_node("a") and all(edge.to_key != "a" for edge in revised.edges)
    assert revised.first_edge_priority() < min(edge.priority for edge in revised.edges)
    with pytest.raises(ValueError):
        graph.revised(remove=[ROOT_KEY])
    with pytest.raises(ValueError):
        graph.revised(remove=["nope"])


# -- identity ---------------------------------------------------------------------


def test_logo_url_no_longer_merges_detail_pages_into_one_identity() -> None:
    graph = graph_of(("v6", ON_ITEM, LOGO_DETAIL, RuleOrigin.LLM))
    ids = set()
    for iid in ("535715253", "537756309"):
        outcome = ENGINE.extract(graph, detail(iid), country_code="EG", identity=OLX_EG_IDENTITY)
        assert outcome is not None and outcome.page_kind is PageKind.DETAIL
        (draft,) = outcome.drafts
        assert draft.external_id == iid and "iid-" + iid in (draft.url or "")
        assert outcome.warnings  # the recipe itself is still wrong, and says so
        ids.add(draft.external_id)
    assert len(ids) == 2


def test_without_a_policy_a_site_root_is_never_hashed_into_an_identity() -> None:
    graph = graph_of(("v6", ON_ITEM, LOGO_DETAIL, RuleOrigin.LLM))
    first = ENGINE.extract(graph, detail("1001"), country_code="EG")
    second = ENGINE.extract(graph, detail("1002"), country_code="EG")
    assert first is not None and second is not None
    # Falls back to the page's own URL, so different pages stay different.
    assert first.drafts[0].external_id != second.drafts[0].external_id
    assert first.drafts[0].url and "1001" in first.drafts[0].url


def test_template_id_disagreeing_with_the_advert_url_drops_the_item() -> None:
    spec = {
        **LOGO_DETAIL,
        "fields": {
            **LOGO_DETAIL["fields"],
            "url": [{"css": "input.share-url", "attr": "value"}],
            "external_id": [{"const": "999"}],
        },
    }
    result = run_template(
        spec,
        ParsedDocument(detail("535715253")),
        vocab=VOCAB,
        template_key="t",
        graph_version=1,
        country_code="EG",
        identity=OLX_EG_IDENTITY,
    )
    assert result.drafts == [] and "identity conflict" in result.problems[0]


# -- price, area, places ------------------------------------------------------------


def _list_page(
    price_cell: str, info: str = "Houses - Apartments for Sale - al-Bah̨r-al-Ah̨mar"
) -> ParsedDocument:
    html = f"""<div id="the-list"><div class="li">
      <h3><a href="http://hurgada.olx.com.eg/flat-for-sale-iid-537756309">Flat for sale</a></h3>
      <span class="itemlistinginfo">{info}</span>
      <div class="c-4"><span>Square Meters: 1,074</span></div>
      <div class="third-column-container">{price_cell}</div></div></div>"""
    return ParsedDocument(
        ArchivedDocument(html.encode(), "http://hurgada.olx.com.eg/real-estate-cat-16")
    )


V4_PRICE = {
    "page_kind": "LIST",
    "items": {"css": "#the-list .li"},
    "fields": {
        "title": [{"css": "h3 a"}],
        "url": [{"css": "h3 a", "attr": "href"}],
        "price": [{"css": ".third-column-container", "regex": "([\\d,.]+)"}],
        "location": [{"css": ".itemlistinginfo"}],
        "city": [{"css": ".itemlistinginfo", "regex": "-\\s*([^-]+)$"}],
        "area": [{"css": ".c-4 span", "regex": "Square Meters:\\s*([\\d,.]+)"}],
    },
}


def _run(spec: dict, document: ParsedDocument):  # type: ignore[no-untyped-def]
    return run_template(
        spec,
        document,
        vocab=VOCAB,
        template_key="t",
        graph_version=1,
        country_code="EG",
        identity=OLX_EG_IDENTITY,
    )


def test_a_price_regex_that_grabs_the_currency_dot_falls_back_to_the_whole_cell() -> None:
    (draft,) = _run(V4_PRICE, _list_page("ج.م125,000")).drafts
    assert (draft.price.amount, draft.price.currency) == (Decimal("125000.00"), "EGP")
    assert draft.attributes["_raw"] == {
        **draft.attributes["_raw"],
        "price": "ج.م125,000",
        "price_source": "field_fulltext",
    }
    # A placeholder stays unknown however it is read.
    (placeholder,) = _run(V4_PRICE, _list_page("ج.م2.00")).drafts
    assert placeholder.price.amount is None


def test_labelled_area_and_whole_place_names() -> None:
    (draft,) = _run(V4_PRICE, _list_page("ج.م125,000")).drafts
    assert draft.area_sqm == Decimal("1074.00")
    # The template's hyphen-split city ("Ah̨mar") loses to the place table.
    assert draft.location.city == "Red Sea"
    assert draft.location.name == "Houses - Apartments for Sale - al-Bah̨r-al-Ah̨mar"
    assert parse_area("Square Meters: 645") == Decimal("645.00")
    assert parse_area("المساحة: 120 م²") == Decimal("120.00")


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("al-Qāhirah", "Cairo"),
        ("Gizeh, Egypt", "Giza"),
        ("Šarm aš-Šayẖ", "Sharm El Sheikh"),
        ("Apartment for Sale in Hurghada, Al Bahr al Ahmar", "Hurghada"),  # city over governorate
        ("البحر الأحمر", "Red Sea"),
        ("Egypt", None),
        ("Ah̨mar", None),
    ],
)
def test_place_resolution(text: str, city: str | None) -> None:
    assert resolve_city(text, "EG") == city


def test_country_as_city_is_dropped_and_foreign_titles_are_flagged() -> None:
    assert is_country_name("Egypt", "EG") and not is_country_name("Cairo", "EG")
    spec = {
        **V4_PRICE,
        "fields": {
            **V4_PRICE["fields"],
            "title": [{"const": "Villa for sale in Larnaca, Cyprus"}],
            "city": [{"const": "Egypt"}],
            "location": [{"const": "Egypt"}],
        },
    }
    (draft,) = _run(spec, _list_page("ج.م125,000")).drafts
    assert draft.location.city is None
    assert draft.attributes["_flags"] == ["foreign_location_signal:cyprus"]


def test_an_ambiguous_category_lets_the_title_decide_sale_or_rent() -> None:
    spec = {
        **V4_PRICE,
        "fields": {**V4_PRICE["fields"], "category": [{"const": "Shops for rent / for sale"}]},
    }
    (draft,) = _run(spec, _list_page("ج.م125,000")).drafts  # title: "Flat for sale"
    assert draft.listing_type is ListingType.SALE
    neither = {
        **spec,
        "fields": {**spec["fields"], "title": [{"const": "Shop in Cairo"}], "location": []},
    }
    page = _list_page("ج.م125,000", info="Shops for rent / for sale")
    (fallback,) = _run(neither, page).drafts
    assert fallback.listing_type is ListingType.RENT  # first vocabulary match, as before


# -- curated templates --------------------------------------------------------------


def test_curated_detail_template_reads_the_2013_item_page() -> None:
    name = "a2_2013_detail_item"
    graph = graph_of(
        (name, TEMPLATES[name]["condition"], TEMPLATES[name]["template"], RuleOrigin.HUMAN)
    )
    outcome = ENGINE.extract(
        graph, detail("434809806"), country_code="EG", identity=OLX_EG_IDENTITY
    )
    assert outcome is not None and not outcome.warnings
    (draft,) = outcome.drafts
    assert draft.external_id == "434809806"
    assert draft.title == "Apartment for Sale in Nasr City"
    assert (draft.price.amount, draft.price.currency) == (Decimal("165000.00"), "EGP")
    assert (draft.bedrooms, draft.bathrooms, draft.area_sqm) == (2 + 1, 2, Decimal("645.00"))
    assert draft.location.city == "Obour"
    assert draft.listing_type is ListingType.SALE


def test_a_working_curated_template_beats_a_richer_induced_one() -> None:
    curated = {
        **LOGO_DETAIL,
        "fields": {
            "title": [{"css": "#olx_item_title .h1"}],
            "category": [{"css": "#levelpath a#firstpath2"}],
        },
    }
    induced = {
        **LOGO_DETAIL,
        "fields": {
            **LOGO_DETAIL["fields"],
            "price": [{"css": ".item-highlights.price"}],
            "description": [{"css": "#description-text"}],
        },
    }
    graph = graph_of(
        ("induced", ON_ITEM, induced, RuleOrigin.LLM),
        ("curated", ON_ITEM, curated, RuleOrigin.HUMAN),
    )
    outcome = ENGINE.extract(graph, detail("1"), country_code="EG", identity=OLX_EG_IDENTITY)
    assert outcome is not None and outcome.template_key == "curated"
