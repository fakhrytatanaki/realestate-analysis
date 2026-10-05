"""Capture-backed OpenSooq extraction and redirect-destination discovery."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote

import httpx
import pytest

from realestate import cli
from realestate.application.rules.seeds import seed_navigation_graph
from realestate.application.services.archive_crawl_service import ArchiveCrawlService, cursor_key
from realestate.application.services.initial_rule_seed_service import (
    InitialRuleSeedService,
    same_rules,
)
from realestate.bootstrap import Container
from realestate.config.settings import Settings, SourceSettings
from realestate.domain.archive import ArchivedDocument, Capture, parse_timestamp, surt_key
from realestate.domain.enums import (
    CrawlStatus,
    LinkRel,
    ListingType,
    NodeKind,
    PageKind,
    PriceType,
    PropertyType,
    RawDocumentKind,
    RouteDecision,
    RuleDomain,
    RuleOrigin,
)
from realestate.domain.exceptions import ConfigurationError, UnrecognisedDocumentError
from realestate.domain.models import FetchContext, RawPayload
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode
from realestate.infrastructure.archive.wayback import (
    REPLAY_PREFIX,
    WaybackCdxIndex,
    WaybackClient,
    WaybackSettings,
)
from realestate.infrastructure.extraction.document import ParsedDocument
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.extraction.seeds import PackagedRuleSeedProvider
from realestate.infrastructure.sources.defaults import register_default_sources
from realestate.infrastructure.sources.opensooq_eg_wayback.source import (
    OPENSOOQ_EG_IDENTITY,
    OpensooqEgWaybackDataSource,
)
from realestate.infrastructure.sources.registry import DataSourceRegistry
from tests.archive_fakes import InMemoryCursors, InMemoryFrontier, InMemoryGraphs
from tests.conftest import NullLogProvider

SOURCE = OpensooqEgWaybackDataSource.key
FIXTURES = Path(__file__).parent / "fixtures" / "wayback_opensooq_eg"
INDEX = json.loads((FIXTURES / "index.json").read_text())
SEEDS = PackagedRuleSeedProvider()
ENGINE = HtmlRuleEngine()
FAST = WaybackSettings(min_delay_seconds=0, max_retries=1)


def document(name: str) -> ArchivedDocument:
    meta = INDEX[name]
    return ArchivedDocument(
        gzip.decompress((FIXTURES / f"{name}.html.gz").read_bytes()),
        meta["url"],
        captured_at=parse_timestamp(meta["served_timestamp"]),
    )


def payload(doc: ArchivedDocument) -> RawPayload:
    return RawPayload(
        content=doc.content,
        kind=RawDocumentKind.HTML,
        content_type="text/html",
        source_url=doc.url,
        meta={"captured_at": doc.captured_at.isoformat()},
    )


@pytest.mark.parametrize("name", list(INDEX))
def test_sanitized_fixtures_preserve_integrity_without_contact_or_runtime_data(name: str) -> None:
    doc = document(name)
    assert hashlib.sha256(doc.content).hexdigest() == INDEX[name]["fixture_sha256"]
    assert len(doc.content) == INDEX[name]["sanitized_bytes"]
    tree = ParsedDocument(doc).tree
    assert not tree.css("script, iframe, input, textarea")
    assert not tree.css('[data-id^="serp_call_btn_"], [data-id^="serp_chat_btn_"]')
    assert not re.search(rb"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", doc.content)


async def test_registered_archive_source_is_manual_with_configurable_scope() -> None:
    registry = register_default_sources(DataSourceRegistry(Settings(), NullLogProvider()))
    descriptor = registry.descriptor(SOURCE)
    assert descriptor.implemented and not descriptor.enabled
    source = registry.create(SOURCE)
    assert isinstance(source, OpensooqEgWaybackDataSource)
    scope = source.archive_scope()
    assert (scope.domain, scope.from_year, scope.to_year) == ("eg.opensooq.com", 2008, 2026)
    await source.aclose()
    custom = OpensooqEgWaybackDataSource(
        log=NullLogProvider(),
        params={"from_year": 2008, "to_year": 2009, "max_fetches_per_run": 2},
        frontier=InMemoryFrontier(),
        graphs=InMemoryGraphs(),
        engine=ENGINE,
    )
    assert custom.archive_scope().to_year == 2009
    await custom.aclose()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://eg.opensooq.com/search/118/?show=all", "118"),
        ("http://eg.opensooq.com:80/search/118/", "118"),
        ("https://eg.opensooq.com/ar/search/30839387/اكسنت-موديل-٩٥", "30839387"),
        ("https://eg.opensooq.com/ar/search/259322363", "259322363"),
        ("https://www.eg.opensooq.com/ar/search/259322363/?page=2", "259322363"),
        ("https://eg.opensooq.com/", None),
        ("http://eg.opensooq.com/view/2/", None),
        ("https://eg.opensooq.com/ar/", None),
        ("http://eg.opensooq.com/search/?sc2id=14", None),
        ("https://eg.opensooq.com/ar/عقارات/شقق-للبيع?page=2", None),
        ("https://eg.opensooq.com/ar/post/create?sc2id=1", None),
        ("https://eg.opensooq.com/ar/search/123car", None),
        ("https://jo.opensooq.com/ar/search/259322363", None),
        ("https://eg.opensooq.com.evil.example/ar/search/259322363", None),
    ],
)
def test_advert_identity_excludes_portal_category_city_and_other_hosts(
    url: str, expected: str | None
) -> None:
    assert OPENSOOQ_EG_IDENTITY.canonical_id(url) == expected


@pytest.mark.parametrize(
    ("url", "evidence", "decision", "kind"),
    [
        ("http://eg.opensooq.com/", {}, RouteDecision.FETCH, PageKind.OTHER),
        ("http://eg.opensooq.com:80/view/", {}, RouteDecision.FETCH, PageKind.OTHER),
        ("http://eg.opensooq.com/view/2/", {}, RouteDecision.FETCH, PageKind.OTHER),
        ("https://eg.opensooq.com/ar", {}, RouteDecision.FETCH, PageKind.OTHER),
        ("https://eg.opensooq.com/ar/", {}, RouteDecision.FETCH, PageKind.OTHER),
        ("http://eg.opensooq.com/search/?sc2id=1", {}, RouteDecision.FETCH, PageKind.LIST),
        (
            "http://eg.opensooq.com/search/?page=2&sc2id=14&scid=2",
            {},
            RouteDecision.FETCH,
            PageKind.LIST,
        ),
        ("https://eg.opensooq.com/ar/عقارات/شقق-للبيع", {}, RouteDecision.FETCH, PageKind.LIST),
        ("https://eg.opensooq.com/ar/search/123", {}, RouteDecision.DEFER, PageKind.DETAIL),
        (
            "http://eg.opensooq.com/search/118/?show=all",
            {"linked_as": ["DETAIL"]},
            RouteDecision.FETCH,
            PageKind.DETAIL,
        ),
        (
            "https://jo.opensooq.com/ar/search/123",
            {"linked_as": ["DETAIL"]},
            RouteDecision.SKIP,
            PageKind.OTHER,
        ),
        (
            "https://eg.opensooq.com.evil.example/view/",
            {"linked_as": ["LIST"]},
            RouteDecision.SKIP,
            PageKind.OTHER,
        ),
    ],
)
def test_navigation_routes_both_portals_and_property_paths(
    url: str, evidence: dict, decision: RouteDecision, kind: PageKind
) -> None:
    graph = SEEDS.load(SOURCE)[0]
    for candidate in (url, quote(url, safe=":/?=&")):
        outcome = ENGINE.route(graph, candidate, evidence)
        assert outcome is not None and (outcome.decision, outcome.page_kind) == (decision, kind)


@pytest.mark.parametrize(
    ("name", "purpose", "expected_ids"),
    [
        (
            "2008_sale",
            ListingType.SALE,
            ["131", "123", "122", "121", "120", "119", "117", "116", "111", "73", "72", "46"],
        ),
        ("2008_rent", ListingType.RENT, ["135", "118"]),
    ],
)
def test_legacy_property_tables_decode_windows1256_and_keep_missing_prices_unknown(
    name: str, purpose: ListingType, expected_ids: list[str]
) -> None:
    doc = document(name)
    outcome = ENGINE.extract(
        SEEDS.load(SOURCE)[1], doc, country_code="EG", identity=OPENSOOQ_EG_IDENTITY
    )
    assert outcome is not None and outcome.items_total == len(expected_ids)
    assert [draft.external_id for draft in outcome.drafts] == expected_ids
    assert outcome.problems == []
    assert all(draft.listing_type is purpose for draft in outcome.drafts)
    assert all(draft.observed_at == doc.captured_at for draft in outcome.drafts)
    assert all(draft.price.amount is None for draft in outcome.drafts)
    assert all(draft.price.price_type is PriceType.UNKNOWN for draft in outcome.drafts)
    first = outcome.drafts[0]
    assert first.property_type is PropertyType.APARTMENT
    assert first.listed_at == datetime(2008, 3, 27, tzinfo=UTC)
    assert first.location.city == ("Port Said" if purpose is ListingType.SALE else "Cairo")
    assert any(link.rel is LinkRel.DETAIL for link in outcome.links)
    if purpose is ListingType.SALE:
        # A price per metre mentioned in a title must not become a total price.
        assert outcome.drafts[3].external_id == "121"
        assert "4000" in outcome.drafts[3].title
        assert outcome.drafts[3].price.amount is None


@pytest.mark.parametrize("name", ["2008_portal", "2016_portal"])
def test_recognised_portals_offer_only_property_categories_without_homepage_adverts(
    name: str,
) -> None:
    doc = document(name)
    graph = SEEDS.load(SOURCE)[1]
    outcome = ENGINE.extract(graph, doc, country_code="EG", identity=OPENSOOQ_EG_IDENTITY)
    assert outcome is not None and outcome.page_kind is PageKind.OTHER
    assert outcome.items_total == 0 and outcome.drafts == []
    assert len(outcome.links) == (2 if name == "2008_portal" else 12)
    assert all(link.rel is LinkRel.LIST for link in outcome.links)
    navigation = SEEDS.load(SOURCE)[0]
    for link in outcome.links:
        routed = ENGINE.route(navigation, link.url, {})
        assert routed is not None and routed.decision is RouteDecision.FETCH


def test_newer_arabic_cards_keep_capture_time_price_area_and_duplicate_identity() -> None:
    doc = document("2025_sale")
    outcome = ENGINE.extract(
        SEEDS.load(SOURCE)[1], doc, country_code="EG", identity=OPENSOOQ_EG_IDENTITY
    )
    assert outcome is not None and (outcome.items_total, outcome.items_valid) == (30, 29)
    assert outcome.problems == [] and "duplicate_identity" in outcome.diagnostics
    assert len({draft.external_id for draft in outcome.drafts}) == 29
    first = outcome.drafts[0]
    assert first.external_id == "259322363"
    assert first.listing_type is ListingType.SALE
    assert first.property_type is PropertyType.APARTMENT
    assert first.price.amount == Decimal("4450000") and first.price.currency == "EGP"
    assert first.area_sqm == Decimal("184") and (first.bedrooms, first.bathrooms) == (3, 3)
    assert first.location.city == "Cairo" and first.location.district == "المقطم"
    assert first.observed_at == datetime(2025, 7, 14, 18, 23, 31, tzinfo=UTC)
    assert first.listed_at == first.observed_at - timedelta(hours=1)
    assert all(draft.observed_at == doc.captured_at for draft in outcome.drafts)
    assert any(link.rel is LinkRel.PAGINATION and "page=2" in link.url for link in outcome.links)


def test_rental_cards_use_explicit_daily_metadata_and_day_month_year_dates() -> None:
    doc = document("2025_rent")
    graph = SEEDS.load(SOURCE)[1]
    outcome = ENGINE.extract(graph, doc, country_code="EG", identity=OPENSOOQ_EG_IDENTITY)
    assert outcome is not None and (outcome.items_total, outcome.items_valid) == (30, 30)
    first = outcome.drafts[0]
    assert first.external_id == "265015571" and first.listing_type is ListingType.RENT
    assert first.price.amount == Decimal("3500") and first.price.price_type is PriceType.PER_NIGHT
    assert first.location.city == "Giza" and first.location.district == "الدقى"
    assert first.area_sqm == Decimal("220") and (first.bedrooms, first.bathrooms) == (3, 3)
    assert first.listed_at == datetime(2025, 7, 13, tzinfo=UTC)
    assert first.observed_at == datetime(2025, 7, 16, 7, 49, 15, tzinfo=UTC)
    missing_period = replace(doc, content=doc.content.replace("يومي".encode(), b""))
    unspecified = ENGINE.extract(
        graph, missing_period, country_code="EG", identity=OPENSOOQ_EG_IDENTITY
    )
    assert unspecified is not None
    assert unspecified.drafts[0].price.amount == Decimal("3500")
    assert unspecified.drafts[0].price.price_type is PriceType.UNKNOWN


def test_literal_and_encoded_arabic_category_urls_extract_identical_adverts() -> None:
    doc = document("2025_sale")
    graph = SEEDS.load(SOURCE)[1]
    encoded = ENGINE.extract(graph, doc, country_code="EG", identity=OPENSOOQ_EG_IDENTITY)
    literal = ENGINE.extract(
        graph,
        replace(doc, url="https://eg.opensooq.com/ar/عقارات/شقق-للبيع"),
        country_code="EG",
        identity=OPENSOOQ_EG_IDENTITY,
    )
    assert encoded is not None and literal is not None
    assert [(draft.external_id, draft.price, draft.area_sqm) for draft in encoded.drafts] == [
        (draft.external_id, draft.price, draft.area_sqm) for draft in literal.drafts
    ]


@pytest.mark.parametrize("mutation", ["category", "identity", "foreign_host", "challenge"])
def test_unverified_taxonomy_identity_and_challenges_remain_extraction_gaps(mutation: str) -> None:
    doc = document("2025_sale")
    if mutation == "category":
        doc = replace(doc, url="https://eg.opensooq.com/ar/سيارات-ومركبات/سيارات-للبيع")
    elif mutation == "identity":
        # Every card still has a plausible title/price, but no safe advert ID.
        content = re.sub(rb"/ar/search/[0-9]+", b"/ar/post/create", doc.content)
        doc = replace(doc, content=content)
    elif mutation == "foreign_host":
        content = doc.content.replace(
            b'href="/ar/search/', b'href="https://jo.opensooq.com/ar/search/'
        )
        doc = replace(doc, content=content)
    else:
        doc = replace(doc, content=b"<html><h1>Verify you are human</h1></html>")
    assert (
        ENGINE.extract(SEEDS.load(SOURCE)[1], doc, country_code="EG", identity=OPENSOOQ_EG_IDENTITY)
        is None
    )


def test_legacy_list_rejects_rows_from_other_categories() -> None:
    doc = document("2008_sale")
    doc = replace(doc, content=doc.content.replace(b"sc2id=1", b"sc2id=2"))
    assert (
        ENGINE.extract(SEEDS.load(SOURCE)[1], doc, country_code="EG", identity=OPENSOOQ_EG_IDENTITY)
        is None
    )


def test_legacy_sales_category_code_does_not_match_rental_code_by_prefix() -> None:
    doc = document("2008_sale")
    tree = ParsedDocument(doc).tree
    row = tree.css_first("a.bold_link").parent.parent
    category = row.css_first('a[href$="?sc2id=1"]')
    category.attrs["href"] = "http://eg.opensooq.com/search/?sc2id=14"
    mixed = replace(doc, content=tree.html.encode("cp1256"))
    outcome = ENGINE.extract(
        SEEDS.load(SOURCE)[1], mixed, country_code="EG", identity=OPENSOOQ_EG_IDENTITY
    )
    assert outcome is not None and outcome.items_valid == 11
    assert "131" not in {draft.external_id for draft in outcome.drafts}
    assert all(draft.listing_type is ListingType.SALE for draft in outcome.drafts)


async def test_rules_seed_cli_is_explicit_offline_and_retains_active_graphs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    graphs = InMemoryGraphs()
    container = Container(Settings())
    container.__dict__.update(log=NullLogProvider(), rule_graphs=graphs)
    monkeypatch.setattr(cli, "Container", lambda _: container)
    monkeypatch.setattr(container, "init_db", AsyncMock())
    monkeypatch.setattr(container, "aclose", AsyncMock())
    args = cli.build_parser().parse_args(["rules", "seed", "--source", SOURCE, "--dry-run"])
    assert await cli.run(args) == 0 and graphs.saved == []
    assert "would install: NAVIGATION, EXTRACTION" in capsys.readouterr().out
    args = cli.build_parser().parse_args(["rules", "seed", "--source", SOURCE])
    assert await cli.run(args) == 0
    assert "installed: NAVIGATION, EXTRACTION" in capsys.readouterr().out
    installed = tuple(graphs.saved)
    assert await cli.run(args) == 0 and tuple(graphs.saved) == installed
    assert "llm" not in container.__dict__ and "wayback" not in container.__dict__


ADVERT = "https://eg.opensooq.com/ar/search/280210947"


def induced_graphs() -> tuple[RuleGraph, RuleGraph]:
    """What a crawl run before seeding leaves: an LLM advert rule and template."""
    navigation = seed_navigation_graph(SOURCE).extended(
        nodes=[
            RuleNode(
                "nav.v2.1",
                NodeKind.ROUTE,
                {"decision": "FETCH", "priority": 90, "page_kind": "LIST"},
                origin=RuleOrigin.LLM,
            )
        ],
        edges=[RuleEdge(ROOT_KEY, "nav.v2.1", {"type": "url_regex", "pattern": r"/search/\d+/?"})],
        notes="llm: 1 navigation rule",
    )
    extraction = RuleGraph.empty(SOURCE, RuleDomain.EXTRACTION).extended(
        nodes=[
            RuleNode(
                "tpl.v1.cars", NodeKind.TEMPLATE, {"page_kind": "OTHER"}, origin=RuleOrigin.LLM
            )
        ],
        edges=[RuleEdge(ROOT_KEY, "tpl.v1.cars", {"type": "dom_css", "css": "#main_img_eg"})],
        notes="llm: template",
    )
    return navigation, extraction


async def test_replace_supersedes_graphs_a_crawl_induced_before_seeding() -> None:
    graphs = InMemoryGraphs()
    old_nav, old_ext = [await graphs.save_version(graph) for graph in induced_graphs()]
    service = InitialRuleSeedService(graphs=graphs, seeds=SEEDS)
    before = tuple(graphs.saved)
    kept = await service.seed(SOURCE)
    assert kept.retained == (RuleDomain.NAVIGATION, RuleDomain.EXTRACTION) and not kept.replaced
    dry = await service.seed(SOURCE, dry_run=True, replace_active=True)
    assert dry.replaced == ((RuleDomain.NAVIGATION, 2, 3), (RuleDomain.EXTRACTION, 1, 2))
    assert tuple(graphs.saved) == before

    report = await service.seed(SOURCE, replace_active=True)

    assert report.replaced == dry.replaced and not report.installed
    reviewed_nav, reviewed_ext = SEEDS.load(SOURCE)
    navigation = await graphs.active(SOURCE, RuleDomain.NAVIGATION)
    extraction = await graphs.active(SOURCE, RuleDomain.EXTRACTION)
    assert navigation is not None and extraction is not None
    assert (navigation.version, navigation.parent_id) == (3, old_nav.id)
    assert (extraction.version, extraction.parent_id) == (2, old_ext.id)
    assert same_rules(navigation, reviewed_nav) and same_rules(extraction, reviewed_ext)
    assert "replaces v2" in (navigation.notes or "")
    # The induced rule fetched every advert as a list; the reviewed one defers it.
    assert ENGINE.route(old_nav, ADVERT, {}).decision is RouteDecision.FETCH  # type: ignore[union-attr]
    assert ENGINE.route(navigation, ADVERT, {}).decision is RouteDecision.DEFER  # type: ignore[union-attr]
    saved = tuple(graphs.saved)
    again = await service.seed(SOURCE, replace_active=True)
    assert not again.replaced and len(again.retained) == 2 and tuple(graphs.saved) == saved


async def test_rules_seed_cli_replace_reroutes_what_the_superseded_rules_queued(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    graphs, frontier = InMemoryGraphs(), InMemoryFrontier()
    for graph in induced_graphs():
        await graphs.save_version(graph)
    await frontier.add_captures(SOURCE, [Capture(surt_key(ADVERT), "20260516145205", ADVERT, "D")])
    await frontier.set_route(
        [1], status=CrawlStatus.QUEUED, route_node="nav.v2.1", priority=90, page_kind=PageKind.LIST
    )
    crawler = ArchiveCrawlService(
        registry=Mock(),
        graphs=graphs,
        frontier=frontier,
        cursors=InMemoryCursors(),
        index=Mock(),
        engine=ENGINE,
        ingestion=Mock(),
        induction=Mock(),
        log=NullLogProvider(),
    )
    container = Container(Settings())
    container.__dict__.update(log=NullLogProvider(), rule_graphs=graphs, crawler=crawler)
    monkeypatch.setattr(cli, "Container", lambda _: container)
    monkeypatch.setattr(container, "init_db", AsyncMock())
    monkeypatch.setattr(container, "aclose", AsyncMock())
    command = ["rules", "seed", "--source", SOURCE, "--replace"]

    assert await cli.run(cli.build_parser().parse_args([*command, "--dry-run"])) == 0
    assert "would replace NAVIGATION v2 with the reviewed graph as v3" in capsys.readouterr().out
    assert len(graphs.saved) == 2 and frontier.rows[1].status is CrawlStatus.QUEUED

    assert await cli.run(cli.build_parser().parse_args(command)) == 0
    output = capsys.readouterr().out
    assert "replaced EXTRACTION v1 with the reviewed graph as v2" in output
    assert "reopened 1 captures of removed rules" in output
    assert f"parse --source {SOURCE} --stale" in output
    assert (frontier.rows[1].status, frontier.rows[1].route_node) == (
        CrawlStatus.DEFERRED,
        "seed.navigation.4",
    )
    curated = ["rules", "seed", "--source", "olx_eg_wayback", "--replace"]
    with pytest.raises(ConfigurationError):
        await cli.run(cli.build_parser().parse_args(curated))


async def test_enumeration_discovers_redirect_destinations_directly_under_the_egypt_host() -> None:
    registry = register_default_sources(
        DataSourceRegistry(
            Settings(sources={SOURCE: SourceSettings(params={"from_year": 2008, "to_year": 2016})}),
            NullLogProvider(),
        )
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        year = request.url.params["from"]
        names = {"2008": ["2008_portal", "2008_sale", "2008_rent"], "2016": ["2016_portal"]}
        rows = [
            [surt_key(INDEX[name]["url"]), INDEX[name]["served_timestamp"], INDEX[name]["url"], "D"]
            for name in names.get(year, [])
        ]
        return httpx.Response(200, json=[["urlkey", "timestamp", "original", "digest"], *rows])

    client = WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler))
    graphs, frontier, cursors = InMemoryGraphs(), InMemoryFrontier(), InMemoryCursors()
    await InitialRuleSeedService(graphs=graphs, seeds=SEEDS).seed(SOURCE)
    await cursors.save("olx_eg_wayback", "cdx:2008", resume_key="existing", done=False)
    crawler = ArchiveCrawlService(
        registry=registry,
        graphs=graphs,
        frontier=frontier,
        cursors=cursors,
        index=WaybackCdxIndex(client),
        engine=ENGINE,
        ingestion=Mock(),
        induction=Mock(),
        log=NullLogProvider(),
    )
    try:
        report = await crawler.enumerate(SOURCE)
        assert report.added == 4 and report.years_completed == list(range(2008, 2017))
        assert {entry.original_url for entry in frontier.rows.values()} == {
            INDEX[name]["url"] for name in ("2008_portal", "2008_sale", "2008_rent", "2016_portal")
        }
        assert all(request.url.params["url"] == "eg.opensooq.com" for request in requests)
        assert all(request.url.params["matchType"] == "domain" for request in requests)
        assert (await crawler.route(SOURCE)).queued == 4
        assert await cursors.get(SOURCE, cursor_key("eg.opensooq.com", 2008)) == (None, True)
        assert await cursors.get("olx_eg_wayback", "cdx:2008") == ("existing", False)
        assert (await crawler.enumerate(SOURCE)).pages == 0
    finally:
        await client.aclose()


@pytest.mark.parametrize("name", ["2008_portal", "2016_portal"])
async def test_replay_of_root_redirect_records_served_path_time_and_property_links(
    name: str,
) -> None:
    doc = document(name)
    requested = "20080325000000" if name == "2008_portal" else "20160101000000"
    original = "https://eg.opensooq.com/"
    start = f"{REPLAY_PREFIX}/{requested}id_/{original}"
    final = f"{REPLAY_PREFIX}/{INDEX[name]['served_timestamp']}id_/{doc.url}"
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        if str(request.url) == start:
            return httpx.Response(301, headers={"Location": final})
        assert str(request.url) == final
        return httpx.Response(200, content=doc.content)

    frontier, graphs = InMemoryFrontier(), InMemoryGraphs()
    await frontier.add_captures(SOURCE, [Capture(surt_key(original), requested, original, "ROOT")])
    await frontier.set_route([1], status=CrawlStatus.QUEUED, page_kind=PageKind.OTHER)
    await InitialRuleSeedService(graphs=graphs, seeds=SEEDS).seed(SOURCE)
    source = OpensooqEgWaybackDataSource(
        log=NullLogProvider(),
        frontier=frontier,
        graphs=graphs,
        engine=ENGINE,
        client=WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler)),
    )
    try:
        ctx = FetchContext(max_items=1)
        pages = [page async for page in source.fetch(ctx)]
        assert len(pages) == 1 and (ctx.progress.attempts, ctx.progress.failures) == (1, 0)
        page = pages[0]
        assert page.source_url == doc.url and page.meta["original_url"] == doc.url
        assert page.meta["timestamp"] == INDEX[name]["served_timestamp"]
        assert page.meta["requested_original_url"] == original
        assert page.meta["requested_timestamp"] == requested
        assert page.meta["redirect_chain"] == visits == [start, final]
        assert page.meta["served_url_key"] == surt_key(doc.url)
        assert await source.parse(page) == []
        assert await source.discover_links(page)
        report = await source.parse_report(page)
        assert report.page_kind == "OTHER" and report.graph_version == 1
        await source.acknowledge(page)
        assert frontier.rows[1].status is CrawlStatus.FETCHED
    finally:
        await source.aclose()


async def test_offline_parse_is_repeatable_pinned_and_reports_unrecognised_designs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient, "request", AsyncMock(side_effect=AssertionError("network"))
    )
    graphs = InMemoryGraphs()
    await InitialRuleSeedService(graphs=graphs, seeds=SEEDS).seed(SOURCE)
    source = OpensooqEgWaybackDataSource(
        log=NullLogProvider(), frontier=InMemoryFrontier(), graphs=graphs, engine=ENGINE
    )
    try:
        doc = document("2008_sale")
        first = await source.parse(payload(doc))
        graph = await graphs.active(SOURCE, RuleDomain.EXTRACTION)
        await graphs.save_version(graph.extended(notes="later graph"))
        assert await source.parse(payload(doc)) == first
        assert first[0].attributes["_extraction"]["graph_version"] == 1
        report = await source.parse_report(payload(doc))
        assert report.graph_version == 1 and report.items_valid == 12
        with pytest.raises(UnrecognisedDocumentError):
            await source.parse(payload(replace(doc, content=b"<h1>Unverified layout</h1>")))
    finally:
        await source.aclose()
