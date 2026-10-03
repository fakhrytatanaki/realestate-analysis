"""Initial source/seed contracts and capture-backed offline regression checks."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from realestate import cli
from realestate.application.rules.proposals import TemplateProposal
from realestate.application.rules.seeds import EVIDENCE_NODE, seed_navigation_graph
from realestate.application.services.archive_crawl_service import ArchiveCrawlService
from realestate.application.services.initial_rule_seed_service import InitialRuleSeedService
from realestate.bootstrap import Container
from realestate.config.settings import Settings, SourceSettings
from realestate.domain.archive import ArchivedDocument, Capture, parse_timestamp, surt_key
from realestate.domain.enums import PageKind, PriceType, PropertyType, RawDocumentKind, RuleDomain
from realestate.domain.enums import RouteDecision as Decision
from realestate.domain.exceptions import UnrecognisedDocumentError
from realestate.domain.models import RawPayload
from realestate.infrastructure.extraction.document import ParsedDocument
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.extraction.normalisers import parse_price
from realestate.infrastructure.extraction.seeds import (
    PackagedRuleSeedProvider,
    compile_seed_bundle,
)
from realestate.infrastructure.sources.defaults import register_default_sources
from realestate.infrastructure.sources.dubizzle_eg_wayback.source import DubizzleEgWaybackDataSource
from realestate.infrastructure.sources.registry import DataSourceRegistry
from tests.archive_fakes import FixtureIndex, InMemoryCursors, InMemoryFrontier, InMemoryGraphs
from tests.conftest import NullLogProvider

FIXTURES = Path(__file__).parent / "fixtures" / "wayback_dubizzle_eg"
INDEX = json.loads((FIXTURES / "index.json").read_text())
SOURCE = DubizzleEgWaybackDataSource.key
SEEDS = PackagedRuleSeedProvider()
ENGINE = HtmlRuleEngine()


def fixture_document(name: str) -> ArchivedDocument:
    entry = INDEX[name]
    return ArchivedDocument(
        gzip.decompress((FIXTURES / f"{name}.html.gz").read_bytes()),
        entry["url"],
        captured_at=parse_timestamp(entry["served_timestamp"]),
    )


def seed_data() -> dict:
    path = Path(__file__).parents[1] / "src/realestate/infrastructure/sources"
    return json.loads((path / "dubizzle_eg_wayback/rules.json").read_text())


@pytest.mark.parametrize("name", list(INDEX))
def test_fixture_integrity_and_no_runtime_or_contact_data(name: str) -> None:
    document = fixture_document(name)
    assert hashlib.sha256(document.content).hexdigest() == INDEX[name]["fixture_sha256"]
    assert len(document.content) == INDEX[name]["sanitized_bytes"]
    if b"@" in document.content:  # JSON-LD uses @type/@context, which are safe.
        assert not re.search(rb"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", document.content)
    assert not re.search(rb"(?<!\d)(?:\+20)?01[0125][0-9]{8}(?!\d)", document.content)
    for forbidden in (b"apiKey", b"authToken", b"phoneNumber", b"contactInfo", b"session"):
        assert forbidden not in document.content
    assert bool(ParsedDocument(document).scripts.get("state")) == INDEX[name]["state_decodes"]


def test_all_investigation_cases_and_large_payload_are_represented() -> None:
    captures = [name for name, meta in INDEX.items() if meta["kind"] == "sanitized_capture"]
    assert set(captures) == {
        "2023_rent",
        "2023_detail",
        "2023_sale",
        "2024_sale",
        "2025_sale",
        "2026_sale",
    }
    assert INDEX["2026_sale"]["original_bytes"] == 5_167_456
    assert len(fixture_document("large_payload").content) > 5 * 1024 * 1024
    assert not fixture_document("2023_sale").content.endswith(b"</html>")


def test_detail_fixture_retains_zero_jsonld_price_conflict_and_array_hierarchy() -> None:
    scripts = ParsedDocument(fixture_document("2023_detail")).scripts
    ad = scripts["state"]["ad"]["data"]
    assert ad["externalID"] == "196521164"
    assert ad["price"] == 0 and ad["extraFields"]["price"] == 200
    assert ad["category"][1]["slug"] == "apartments-duplex-for-rent"
    assert {place["level"] for place in ad["location"]} == {0, 1, 2}
    product = scripts["ld+json"][0]
    assert str(product["sku"]) == ad["externalID"]
    assert product["offers"][0]["price"] == 0


async def test_unsupported_seed_source_does_not_write_any_graph() -> None:
    graphs = InMemoryGraphs()
    with pytest.raises(ValueError, match="no reviewed rule seeds"):
        await InitialRuleSeedService(graphs=graphs, seeds=SEEDS).seed("olx_eg_wayback")
    assert graphs.saved == []


@pytest.mark.parametrize(
    "name",
    [
        "2023_rent",
        "2024_sale",
        "2025_sale",
        "2026_sale",
        "mixed_categories",
        "large_payload",
        "2023_sale",
        "2023_detail",
        "same_ad_detail",
    ],
)
def test_reviewed_extraction_matches_expected_values(name: str) -> None:
    document = fixture_document(name)
    outcome = ENGINE.extract(SEEDS.load(SOURCE)[1], document, country_code="EG")
    assert outcome is not None
    actual = [
        {
            "external_id": draft.external_id,
            "amount": str(draft.price.amount) if draft.price.amount is not None else None,
            "currency": draft.price.currency,
            "listing_type": draft.listing_type.value,
            "area_sqm": str(draft.area_sqm) if draft.area_sqm is not None else None,
        }
        for draft in outcome.drafts
    ]
    assert actual == [
        {key: value for key, value in expected.items() if key != "price_type"}
        for expected in INDEX[name]["expected"]
    ]
    for draft in outcome.drafts:
        assert draft.observed_at == document.captured_at
        assert not draft.is_active
        assert draft.url is not None
        assert "/ad/" in draft.url and "dubizzle.com.eg/" in draft.url
        assert f"-ID{draft.external_id}.html" in draft.url
        assert draft.property_type is PropertyType.OTHER  # subtype codes remain unvalidated
        assert draft.description is None
        if draft.listing_type.value == "RENT":
            assert draft.price.price_type is (
                PriceType.PER_NIGHT if name == "2023_detail" else PriceType.UNKNOWN
            )
        if draft.listed_at:
            assert draft.listed_at <= document.captured_at
    if name == "mixed_categories":
        assert outcome.items_total == 4
        assert len(outcome.drafts) == 2
        assert outcome.problems == ["item 1: item filter rejected"]


@pytest.mark.parametrize(
    "name",
    [
        "non_property",
        "missing_identity",
        "missing_state",
        "challenge",
    ],
)
def test_unvalidated_or_unusable_layouts_remain_gaps(name: str) -> None:
    assert ENGINE.extract(SEEDS.load(SOURCE)[1], fixture_document(name), country_code="EG") is None


def test_missing_capture_time_and_unexpected_host_cannot_emit_active_listings() -> None:
    doc = fixture_document("2023_rent")
    graph = SEEDS.load(SOURCE)[1]
    assert ENGINE.extract(graph, ArchivedDocument(doc.content, doc.url), country_code="EG") is None
    assert (
        ENGINE.extract(
            graph,
            ArchivedDocument(
                doc.content,
                "https://evil.example/properties/apartments-duplex-for-rent/",
                captured_at=doc.captured_at,
            ),
            country_code="EG",
        )
        is None
    )


async def test_source_registration_and_year_overrides_leave_other_sources_unchanged() -> None:
    settings = Settings(
        sources={SOURCE: SourceSettings(params={"from_year": 2024, "to_year": 2025})}
    )
    registry = register_default_sources(DataSourceRegistry(settings, NullLogProvider()))
    descriptor = registry.descriptor(SOURCE)
    assert descriptor.implemented and not descriptor.enabled and descriptor.country_code == "EG"
    archive = registry.create(SOURCE)
    assert isinstance(archive, DubizzleEgWaybackDataSource)
    assert (
        archive.archive_scope().domain,
        archive.archive_scope().from_year,
        archive.archive_scope().to_year,
    ) == ("dubizzle.com.eg", 2024, 2025)
    olx = registry.create("olx_eg_wayback")
    assert olx.archive_scope().domain == "olx.com.eg"
    assert registry.descriptor("dubizzle_eg").implemented
    await archive.aclose()
    await olx.aclose()
    default = DubizzleEgWaybackDataSource(
        log=NullLogProvider(),
        frontier=InMemoryFrontier(),
        graphs=InMemoryGraphs(),
        engine=ENGINE,
    )
    assert (default.archive_scope().from_year, default.archive_scope().to_year) == (2023, 2026)
    await default.aclose()


async def test_seeding_is_idempotent_and_preserves_existing_graphs_in_each_domain() -> None:
    graphs = InMemoryGraphs()
    service = InitialRuleSeedService(graphs=graphs, seeds=SEEDS)
    other = await graphs.save_version(seed_navigation_graph("olx_eg_wayback"))
    first = await service.seed(SOURCE)
    assert first.installed == (RuleDomain.NAVIGATION, RuleDomain.EXTRACTION)
    original = tuple(graphs.saved)
    again = await service.seed(SOURCE)
    assert not again.installed and len(again.retained) == 2
    assert tuple(graphs.saved) == original
    assert await graphs.active("olx_eg_wayback", RuleDomain.NAVIGATION) == other
    for domain in RuleDomain:
        existing = InMemoryGraphs()
        operator_graph = await existing.save_version(
            SEEDS.load(SOURCE)[0 if domain is RuleDomain.NAVIGATION else 1].extended(
                notes="operator rules"
            )
        )
        report = await InitialRuleSeedService(graphs=existing, seeds=SEEDS).seed(SOURCE)
        assert domain in report.retained
        assert await existing.active(SOURCE, domain) == operator_graph
        assert len(existing.saved) == 2


@pytest.mark.parametrize("mutation", ["source", "condition", "field", "filter", "duplicate"])
def test_invalid_packaged_graphs_are_rejected(mutation: str) -> None:
    data = seed_data()
    if mutation == "source":
        data["source_key"] = "olx_eg_wayback"
    elif mutation == "condition":
        data["extraction"][0]["conditions"] = [{"type": "unknown"}]
    elif mutation == "field":
        data["extraction"][0]["fields"]["typo"] = [{"const": "bad"}]
    elif mutation == "filter":
        data["extraction"][0]["items"]["filters"][0]["pattern"] = "["
    else:
        data["extraction"].append(deepcopy(data["extraction"][0]))
    with pytest.raises((ValueError, ValidationError)):
        compile_seed_bundle(data, source_key=SOURCE)


@pytest.mark.parametrize(
    ("url", "evidence", "expected"),
    [
        (
            "https://www.dubizzle.com.eg/properties/apartments-duplex-for-rent/cairo/?page=2",
            {},
            Decision.FETCH,
        ),
        ("https://dubizzle.com.eg/en/properties/apartments-duplex-for-sale/", {}, Decision.FETCH),
        ("https://www.dubizzle.com.eg/ad/example-ID196521164.html", {}, Decision.DEFER),
        (
            "https://www.dubizzle.com.eg/ad/example-ID196521164.html",
            {"linked_as": ["DETAIL"]},
            Decision.FETCH,
        ),
        (
            "https://evil.example/ad/example-ID196521164.html",
            {"linked_as": ["DETAIL"]},
            Decision.SKIP,
        ),
        (
            "https://dubizzle.com.eg.evil.example/properties/",
            {"linked_as": ["LIST"]},
            Decision.SKIP,
        ),
        ("https://images.dubizzle.com.eg/example.jpg", {}, Decision.SKIP),
        ("https://dubizzle.com.eg/vehicles/cars/", {}, Decision.SKIP),
    ],
)
def test_navigation_host_scope_property_paths_and_link_evidence(
    url: str,
    evidence: dict,
    expected: Decision,
) -> None:
    graph = SEEDS.load(SOURCE)[0]
    assert graph.has_node(EVIDENCE_NODE)
    outcome = ENGINE.route(graph, url, evidence)
    assert outcome is not None and outcome.decision is expected


async def test_explicit_enumeration_uses_source_scope_and_independent_cursors() -> None:
    graphs, frontier, cursors = InMemoryGraphs(), InMemoryFrontier(), InMemoryCursors()
    meta = INDEX["2023_rent"]
    capture = Capture(surt_key(meta["url"]), meta["served_timestamp"], meta["url"], "test-digest")
    index = FixtureIndex([capture])
    registry = register_default_sources(
        DataSourceRegistry(
            Settings(
                sources={
                    SOURCE: SourceSettings(params={"from_year": 2023, "to_year": 2023}),
                }
            ),
            NullLogProvider(),
        )
    )
    await cursors.save("olx_eg_wayback", "cdx:2023", resume_key="42", done=False)
    await frontier.add_captures("olx_eg_wayback", [capture])
    await InitialRuleSeedService(graphs=graphs, seeds=SEEDS).seed(SOURCE)
    crawler = ArchiveCrawlService(
        registry=registry,
        graphs=graphs,
        frontier=frontier,
        cursors=cursors,
        index=index,
        engine=ENGINE,
        ingestion=Mock(),
        induction=Mock(),
        log=NullLogProvider(),
    )
    report = await crawler.enumerate(SOURCE)
    assert report.years_completed == [2023] and report.added == 1
    assert index.requests == [("dubizzle.com.eg", 2023, None)]
    assert await cursors.get("olx_eg_wayback", "cdx:2023") == ("42", False)
    assert (await crawler.enumerate(SOURCE)).pages == 0
    assert (await crawler.route(SOURCE)).queued == 1
    assert {row.source_key for row in frontier.rows.values()} == {SOURCE, "olx_eg_wayback"}


@pytest.mark.parametrize("name", ["2023_rent", "2023_sale", "2023_detail"])
async def test_offline_parse_is_pinned_repeatable_and_unknown_layout_raises(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient, "request", AsyncMock(side_effect=AssertionError("network"))
    )
    graphs = InMemoryGraphs()
    await InitialRuleSeedService(graphs=graphs, seeds=SEEDS).seed(SOURCE)
    source = DubizzleEgWaybackDataSource(
        log=NullLogProvider(),
        frontier=InMemoryFrontier(),
        graphs=graphs,
        engine=ENGINE,
    )
    doc = fixture_document(name)
    payload = RawPayload(
        content=doc.content,
        kind=RawDocumentKind.HTML,
        content_type="text/html",
        source_url=doc.url,
        meta={"timestamp": INDEX[name]["served_timestamp"]},
    )
    first = await source.parse(payload)
    graph = await graphs.active(SOURCE, RuleDomain.EXTRACTION)
    await graphs.save_version(graph.extended(notes="later operator version"))
    repeated = await source.parse(
        RawPayload(
            content=doc.content,
            kind=RawDocumentKind.HTML,
            content_type="text/html",
            source_url=doc.url,
            meta=payload.meta,
        )
    )
    assert first == repeated
    assert first[0].attributes["_extraction"]["graph_version"] == 1
    broken = fixture_document("challenge")
    with pytest.raises(UnrecognisedDocumentError):
        await source.parse(
            RawPayload(
                content=broken.content,
                kind=RawDocumentKind.HTML,
                content_type="text/html",
                source_url=broken.url,
                meta={"timestamp": INDEX["2023_sale"]["served_timestamp"]},
            )
        )
    await source.aclose()


async def test_seed_cli_installs_without_using_llm_or_archive(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    container = Container(Settings())
    container.__dict__.update(log=NullLogProvider(), rule_graphs=InMemoryGraphs())
    monkeypatch.setattr(cli, "Container", lambda _: container)
    monkeypatch.setattr(container, "init_db", AsyncMock())
    monkeypatch.setattr(container, "aclose", AsyncMock())
    dry_args = cli.build_parser().parse_args(["rules", "seed", "--source", SOURCE, "--dry-run"])
    assert await cli.run(dry_args) == 0
    assert "would install: NAVIGATION, EXTRACTION" in capsys.readouterr().out
    assert container.rule_graphs.saved == []
    args = cli.build_parser().parse_args(["rules", "seed", "--source", SOURCE])
    assert await cli.run(args) == 0
    assert "installed: NAVIGATION, EXTRACTION" in capsys.readouterr().out
    assert "llm" not in container.__dict__ and "wayback" not in container.__dict__
    assert await cli.run(args) == 0
    assert "installed: none" in capsys.readouterr().out


def test_proposal_preserves_conservative_options_and_legacy_defaults() -> None:
    data = seed_data()["extraction"][0]
    proposal = TemplateProposal.model_validate(data)
    assert proposal.action()["default_rental_price_type"] == "UNKNOWN"
    assert proposal.action()["strict_classification"] is True
    legacy = TemplateProposal(name="legacy", page_kind="LIST", conditions=[{"type": "always"}])
    assert legacy.action()["default_rental_price_type"] == "PER_MONTH"
    assert not legacy.action()["strict_classification"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2000 EGP", PriceType.UNKNOWN),
        ("2000 EGP per day", PriceType.PER_NIGHT),
        ("2000 EGP per month", PriceType.PER_MONTH),
    ],
)
def test_conservative_rental_default_preserves_explicit_periods(
    text: str,
    expected: PriceType,
) -> None:
    from realestate.domain.enums import ListingType

    parsed = parse_price(
        text,
        listing_type=ListingType.RENT,
        default_rental_price_type=PriceType.UNKNOWN,
    )
    assert parsed.price_type is expected and parsed.amount is not None


def test_placeholder_amount_cannot_be_replaced_by_a_title_number() -> None:
    graph = SEEDS.load(SOURCE)[1]
    doc = fixture_document("same_ad_list")
    data = ParsedDocument(doc).scripts["state"]
    hit = data["algolia"]["content"]["hits"][0]
    hit["title"] = "3 bedrooms, 90000 EGP per month"
    hit["extraFields"]["price"] = 0
    content = f"<script>window.state = {json.dumps(data)};</script>".encode()
    outcome = ENGINE.extract(
        graph,
        ArchivedDocument(
            content,
            doc.url,
            captured_at=doc.captured_at,
        ),
        country_code="EG",
    )
    assert outcome is not None and len(outcome.drafts) == 1
    assert outcome.drafts[0].price.amount is None
    assert outcome.drafts[0].price.price_type is PriceType.UNKNOWN


def modified_state(name: str, state: dict, *, url: str | None = None) -> ArchivedDocument:
    doc = fixture_document(name)
    return ArchivedDocument(
        f"<script>window.state = {json.dumps(state)};</script>".encode(),
        url or doc.url,
        captured_at=doc.captured_at,
    )


def test_detail_uses_level_selection_and_captured_price_not_jsonld() -> None:
    doc = fixture_document("2023_detail")
    outcome = ENGINE.extract(SEEDS.load(SOURCE)[1], doc, country_code="EG")
    assert outcome is not None and outcome.page_kind is PageKind.DETAIL
    draft = outcome.drafts[0]
    assert draft.price.amount == Decimal("200")
    assert draft.price.price_type is PriceType.PER_NIGHT
    assert draft.location.city == "Alexandria"
    assert draft.attributes["_raw"]["city"] == "الإسكندرية"
    assert draft.location.district == "ميامي"
    assert draft.location.point.latitude == Decimal("31.26527")
    assert draft.location.point.longitude == Decimal("30.00177")
    state = ParsedDocument(doc).scripts["state"]
    state["ad"]["data"]["location"].reverse()
    state["ad"]["data"]["category"].reverse()
    reordered = ENGINE.extract(
        SEEDS.load(SOURCE)[1], modified_state("2023_detail", state), country_code="EG"
    )
    assert reordered.drafts == outcome.drafts


@pytest.mark.parametrize(
    "mutation", ["id", "missing_id", "non_property", "duplicate_level", "home_url"]
)
def test_detail_rejects_unsafe_identity_or_taxonomy(mutation: str) -> None:
    doc = fixture_document("2023_detail")
    state = deepcopy(ParsedDocument(doc).scripts["state"])
    ad = state["ad"]["data"]
    url = doc.url
    if mutation == "id":
        ad["externalID"] = "123"
    elif mutation == "missing_id":
        del ad["externalID"]
    elif mutation == "non_property":
        ad["category"][0]["slug"] = "vehicles"
    elif mutation == "duplicate_level":
        ad["category"].append(deepcopy(ad["category"][1]))
    else:
        url = "https://www.dubizzle.com.eg/"
    assert (
        ENGINE.extract(
            SEEDS.load(SOURCE)[1], modified_state("2023_detail", state, url=url), country_code="EG"
        )
        is None
    )


@pytest.mark.parametrize(
    ("latitude", "longitude"), [(91, 30), (31, 181), ("NaN", 30), (31, "Infinity"), ("bad", 30)]
)
def test_invalid_coordinates_preserve_the_advert_without_a_point(latitude, longitude) -> None:
    state = deepcopy(ParsedDocument(fixture_document("2023_detail")).scripts["state"])
    state["ad"]["data"]["geography"] = {"lat": latitude, "lng": longitude}
    outcome = ENGINE.extract(
        SEEDS.load(SOURCE)[1], modified_state("2023_detail", state), country_code="EG"
    )
    assert outcome is not None and outcome.drafts[0].location.point is None
    assert "invalid_coordinates" in outcome.diagnostics


def test_synthetic_list_detail_pair_and_language_variants_preserve_identity() -> None:
    graph = SEEDS.load(SOURCE)[1]
    listing = ENGINE.extract(graph, fixture_document("same_ad_list"), country_code="EG").drafts[0]
    doc = fixture_document("same_ad_detail")
    detail = ENGINE.extract(graph, doc, country_code="EG").drafts[0]
    english = ENGINE.extract(
        graph,
        ArchivedDocument(
            doc.content, doc.url.replace("/ad/", "/en/ad/"), captured_at=doc.captured_at
        ),
        country_code="EG",
    ).drafts[0]
    for draft in (detail, english):
        assert draft.external_id == listing.external_id
        assert draft.price == listing.price
        assert draft.area_sqm == listing.area_sqm
        assert draft.location.city == listing.location.city
        assert draft.location.district == listing.location.district
        assert draft.observed_at == listing.observed_at


def test_truncated_list_fallback_recovers_all_labelled_cards() -> None:
    outcome = ENGINE.extract(
        SEEDS.load(SOURCE)[1], fixture_document("2023_sale"), country_code="EG"
    )
    assert outcome is not None and outcome.items_total == outcome.items_valid == 45
    assert outcome.problems == []
    assert outcome.diagnostics == ["malformed_json:state", "html"]
    assert all(draft.listed_at is not None for draft in outcome.drafts)
    assert all(
        draft.bedrooms is not None and draft.bathrooms is not None for draft in outcome.drafts
    )


@pytest.mark.parametrize(
    "hits", [[], [{"externalID": "123", "title": "Car", "category.lvl0": {"slug": "vehicles"}}]]
)
def test_decoded_list_taxonomy_cannot_be_overridden_by_html(hits: list) -> None:
    doc = fixture_document("2023_sale")
    content = doc.content.replace(b'window.state = {"algolia":', b'ignored = {"algolia":')
    state = json.dumps({"algolia": {"content": {"hits": hits}}})
    content += f"</script><script>window.state = {state};</script>".encode()
    assert (
        ENGINE.extract(
            SEEDS.load(SOURCE)[1],
            ArchivedDocument(content, doc.url, captured_at=doc.captured_at),
            country_code="EG",
        )
        is None
    )


def test_usable_json_wins_even_when_html_has_more_cards() -> None:
    graph = SEEDS.load(SOURCE)[1]
    doc = fixture_document("2023_sale")
    hit = deepcopy(
        ParsedDocument(fixture_document("2024_sale")).scripts["state"]["algolia"]["content"][
            "hits"
        ][0]
    )
    hit["extraFields"]["price"] = 987654
    state = json.dumps({"algolia": {"content": {"hits": [hit]}}})
    content = doc.content + f"</script><script>window.state = {state};</script>".encode()
    outcome = ENGINE.extract(
        graph, ArchivedDocument(content, doc.url, captured_at=doc.captured_at), country_code="EG"
    )
    assert outcome is not None and len(outcome.drafts) == 1
    assert outcome.template_key == "seed.category_json_en"
    assert outcome.drafts[0].price.amount == Decimal("987654")


def test_empty_results_need_both_decoded_counts_and_explicit_visible_signal() -> None:
    doc = fixture_document("empty_results")
    graph = SEEDS.load(SOURCE)[1]
    outcome = ENGINE.extract(graph, doc, country_code="EG")
    assert outcome is not None and outcome.page_kind is PageKind.OTHER
    assert not outcome.drafts and not outcome.links
    for content in (
        doc.content.replace(b"No results found", b""),
        doc.content.replace(b'"nbHits": 0', b'"nbHits": 1'),
        doc.content.replace(b'"hits": []', b'"other": []'),
    ):
        assert (
            ENGINE.extract(
                graph,
                ArchivedDocument(content, doc.url, captured_at=doc.captured_at),
                country_code="EG",
            )
            is None
        )


async def test_parse_logs_bounded_rejections_and_unknown_basis_without_changing_drafts() -> None:
    log = NullLogProvider()
    graphs = InMemoryGraphs()
    await InitialRuleSeedService(graphs=graphs, seeds=SEEDS).seed(SOURCE)
    source = DubizzleEgWaybackDataSource(
        log=log, frontier=InMemoryFrontier(), graphs=graphs, engine=ENGINE
    )
    doc = fixture_document("mixed_categories")
    payload = RawPayload(
        content=doc.content,
        kind=RawDocumentKind.HTML,
        content_type="text/html",
        source_url=doc.url,
        meta={"timestamp": INDEX["mixed_categories"]["served_timestamp"]},
    )
    assert (
        await source.parse(payload)
        == ENGINE.extract(SEEDS.load(SOURCE)[1], doc, country_code="EG").drafts
    )
    record = next(fields for _, message, fields in log.records if message == "archive extraction")
    assert record["items_total"] == 4 and record["items_valid"] == 2
    assert record["problems_count"] == 1
    assert record["problems"] == ["item 1: item filter rejected"]
    assert record["diagnostics"] == {
        "embedded_json": 1,
        "unknown_rental_basis": 1,
        "duplicate_identity": 1,
        "category_purpose_conflict": 2,
    }
    await source.aclose()


async def test_rejected_detail_reports_identity_problem_in_gap_logs() -> None:
    graphs = InMemoryGraphs()
    await InitialRuleSeedService(graphs=graphs, seeds=SEEDS).seed(SOURCE)
    log = NullLogProvider()
    source = DubizzleEgWaybackDataSource(
        log=log, frontier=InMemoryFrontier(), graphs=graphs, engine=ENGINE
    )
    state = deepcopy(ParsedDocument(fixture_document("2023_detail")).scripts["state"])
    state["ad"]["data"]["externalID"] = "123"
    doc = modified_state("2023_detail", state)
    payload = RawPayload(
        content=doc.content,
        kind=RawDocumentKind.HTML,
        content_type="text/html",
        source_url=doc.url,
        meta={"timestamp": INDEX["2023_detail"]["served_timestamp"]},
    )
    with pytest.raises(UnrecognisedDocumentError, match="URL identity mismatch"):
        await source.parse(payload)
    fields = next(
        fields for _, message, fields in log.records if message == "archive extraction gap"
    )
    assert fields["problems"] == ["seed.detail_json_ar_2023: item 0: URL identity mismatch"]
    await source.aclose()
