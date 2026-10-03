"""Repairing stored graphs: compaction, rerouting, curated vocabulary, replay diffs.

Phase 1 of docs/rule-induction-review.md. Induction left rules that never
decide anything, a catch-all that parks whole years, an ``OTHER`` template
hiding real list pages and an order-dependent global vocabulary. Each test
here covers one of the tools that remove them without changing anything else,
and the replay diff that shows it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from realestate import cli
from realestate.application.rules.seeds import EVIDENCE_NODE, seed_navigation_graph
from realestate.application.services.navigation_compaction_service import (
    NavigationCompactionService,
)
from realestate.bootstrap import Container
from realestate.config.settings import Settings
from realestate.domain.archive import CAPTURE_CAP, EXPLORE_NODE, ArchivedDocument, Capture, surt_key
from realestate.domain.enums import (
    CrawlStatus,
    ListingType,
    NodeKind,
    PageKind,
    PropertyType,
    RawDocumentStatus,
    RuleDomain,
    RuleOrigin,
)
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode, is_curated_vocab
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.sources.olx_eg_wayback.source import OLX_EG_IDENTITY
from tests.archive_fakes import TEMPLATES, ScriptedLlm, fixture_document
from tests.conftest import NullLogProvider
from tests.test_quality_pipeline import audit_service, seeder
from tests.test_rule_induction_and_crawl import SOURCE, Harness

NAV = RuleDomain.NAVIGATION
EXTRACTION = RuleDomain.EXTRACTION

PROPERTIES = "http://www.olx.com.eg/properties/apartments-for-sale/"
CARS = "http://www.olx.com.eg/cars/kia/"
JOBS = "http://www.olx.com.eg/jobs/"
LINKED_AD = "http://www.olx.com.eg/ad/-IDa2hfM.html"


def route(key: str, decision: str, *, origin: RuleOrigin = RuleOrigin.LLM) -> RuleNode:
    return RuleNode(key, NodeKind.ROUTE, {"decision": decision, "page_kind": "OTHER"}, origin)


def on_url(key: str, pattern: str, priority: int) -> RuleEdge:
    return RuleEdge(ROOT_KEY, key, {"type": "url_regex", "pattern": pattern}, priority)


def navigation_graph() -> RuleGraph:
    """Induction's habits in miniature.

    ``props_again`` repeats ``props``; ``catch_all`` parks every www URL no
    earlier rule takes, so ``cars`` after it is dead only because of it; the
    curated ``reviewed`` rule is never the first match either.
    """
    return seed_navigation_graph(SOURCE).extended(
        nodes=[
            route("props", "FETCH"),
            route("props_again", "FETCH"),
            route("catch_all", "DEFER"),
            route("cars", "SKIP"),
            route("reviewed", "SKIP", origin=RuleOrigin.HUMAN),
        ],
        edges=[
            on_url("props", r"/properties/", 100),
            on_url("props_again", r"/properties/", 110),
            on_url("catch_all", r"\.(olx\.com\.eg)", 120),
            on_url("cars", r"/cars/", 130),
            on_url("reviewed", r"/never-seen/", 140),
        ],
    )


async def frontier_routed_by(harness: Harness, rows: list[tuple[str, CrawlStatus, str]]) -> None:
    """One capture per ``(url, status, route_node)``."""
    for index, (url, status, node) in enumerate(rows):
        timestamp = f"201301{index + 1:02d}000000"
        await harness.frontier.add_captures(
            SOURCE, [Capture(surt_key(url), timestamp, url, f"D{index}")]
        )
        entry = next(
            e
            for e in harness.frontier.rows.values()
            if e.original_url == url and e.timestamp == timestamp
        )
        await harness.frontier.set_route([entry.id], status=status, route_node=node)


def compactor(harness: Harness, *, batch: int = 2) -> NavigationCompactionService:
    return NavigationCompactionService(
        frontier=harness.frontier,
        graphs=harness.graphs,
        engine=harness.engine,
        log=NullLogProvider(),
        batch=batch,
    )


def status_of(harness: Harness, url: str) -> tuple[CrawlStatus, str | None]:
    entry = max(
        (e for e in harness.frontier.rows.values() if e.original_url == url),
        key=lambda e: e.timestamp,
    )
    return entry.status, entry.route_node


# -- navigation compaction (1.1) -------------------------------------------------------


async def test_compaction_drops_dead_rules_and_reroutes_only_what_the_named_rule_decided(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    await harness.graphs.save_version(navigation_graph())
    await frontier_routed_by(
        harness,
        [
            (PROPERTIES, CrawlStatus.QUEUED, "props"),
            (CARS, CrawlStatus.DEFERRED, "catch_all"),
            (JOBS, CrawlStatus.DEFERRED, "catch_all"),
            (JOBS, CrawlStatus.SKIPPED, f"catch_all{CAPTURE_CAP}"),
            (JOBS, CrawlStatus.FETCHED, "catch_all"),  # fetched work keeps its history
            ("http://www.olx.com.eg/misc/", CrawlStatus.QUEUED, EXPLORE_NODE),
        ],
    )
    # Evidence decides before any URL rule, and the plan must route with it.
    await frontier_routed_by(harness, [(LINKED_AD, CrawlStatus.QUEUED, EVIDENCE_NODE)])
    linked = next(e for e in harness.frontier.rows.values() if e.original_url == LINKED_AD)
    harness.frontier.rows[linked.id] = replace(linked, evidence={"linked_as": ["DETAIL"]})

    plan = await compactor(harness).plan(SOURCE, remove=["catch_all"])

    # cars only looked dead because catch_all shadowed it; the curated rule stays.
    assert plan.removed == ["catch_all"] and plan.dead == ["props_again"]
    assert plan.url_keys == 5 and plan.unexpected == 0
    assert plan.changes == {
        "catch_all -> cars (SKIP)": 1,
        "catch_all -> UNROUTED": 2,  # jobs/ and misc/
    }
    candidate = plan.candidate
    assert not candidate.has_node("catch_all") and not candidate.has_node("props_again")
    assert candidate.has_node("cars") and candidate.has_node("reviewed")
    assert candidate.notes is not None and "routing diff over 5 url keys" in candidate.notes
    assert plan.live_routes == 4 and "4 routes left" in candidate.notes
    assert plan.wins == {"props": 1, "cars": 1, EVIDENCE_NODE: 1}

    saved = await compactor(harness).apply(plan)
    routed = await harness.crawler.route(SOURCE, reopen_removed=True)

    assert saved.version == plan.current.version + 1
    assert routed.reopened == 3  # two deferred, one capture-capped
    assert status_of(harness, CARS) == (CrawlStatus.SKIPPED, "cars")
    assert status_of(harness, PROPERTIES) == (CrawlStatus.QUEUED, "props")
    jobs = [
        (e.status, e.route_node) for e in harness.frontier.rows.values() if e.original_url == JOBS
    ]
    assert jobs.count((CrawlStatus.UNROUTED, None)) == 2
    assert (CrawlStatus.FETCHED, "catch_all") in jobs
    assert status_of(harness, "http://www.olx.com.eg/misc/") == (CrawlStatus.QUEUED, EXPLORE_NODE)


async def test_a_graph_with_no_dead_rules_is_left_alone(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    graph = seed_navigation_graph(SOURCE).extended(
        nodes=[route("props", "FETCH")], edges=[on_url("props", r"/properties/", 100)]
    )
    await harness.graphs.save_version(graph)
    await frontier_routed_by(harness, [(PROPERTIES, CrawlStatus.QUEUED, "props")])

    plan = await compactor(harness).plan(SOURCE)

    assert not plan.changed and plan.dead == [] and plan.candidate is plan.current
    assert await compactor(harness).apply(plan) is plan.current
    assert len(harness.graphs.saved) == 1


async def test_compaction_refuses_unknown_rules_and_unexpected_route_changes(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    with pytest.raises(ConfigurationError):
        await compactor(harness).plan(SOURCE)  # no navigation graph yet
    await harness.graphs.save_version(navigation_graph())
    with pytest.raises(ConfigurationError):
        await compactor(harness).plan(SOURCE, remove=["no.such.rule"])
    with pytest.raises(ConfigurationError):
        await compactor(harness).plan(SOURCE, remove=[ROOT_KEY])

    plan = await compactor(harness).plan(SOURCE, remove=["catch_all"])
    plan.unexpected = 1
    with pytest.raises(ConfigurationError):
        await compactor(harness).apply(plan)
    assert len(harness.graphs.saved) == 1


async def test_rules_compact_cli_reports_dry_runs_and_reroutes_on_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    await harness.graphs.save_version(navigation_graph())
    await frontier_routed_by(harness, [(CARS, CrawlStatus.DEFERRED, "catch_all")])
    container = Container(Settings())
    container.__dict__.update(
        log=NullLogProvider(), compactor=compactor(harness), crawler=harness.crawler
    )
    monkeypatch.setattr(cli, "Container", lambda _: container)
    monkeypatch.setattr(container, "init_db", AsyncMock())
    monkeypatch.setattr(container, "aclose", AsyncMock())
    command = ["rules", "compact", "--source", SOURCE, "--remove", "catch_all"]

    dry = [*command, "--dry-run", "--out", str(tmp_path / "dry.json")]
    assert await cli.run(cli.build_parser().parse_args(dry)) == 0
    assert "catch_all -> cars (SKIP): 1" in capsys.readouterr().out
    assert len(harness.graphs.saved) == 1 and (tmp_path / "dry.json").exists()

    applied = [*command, "--out", str(tmp_path / "applied.json")]
    assert await cli.run(cli.build_parser().parse_args(applied)) == 0
    output = capsys.readouterr().out
    assert "saved navigation graph" in output and "reopened 1 captures" in output
    assert status_of(harness, CARS) == (CrawlStatus.SKIPPED, "cars")


async def test_frontier_pages_through_every_url_key(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    urls = [f"http://www.olx.com.eg/page-{n}/" for n in range(5)]
    await frontier_routed_by(harness, [(url, CrawlStatus.FETCHED, "x") for url in urls])
    seen: list[str] = []
    after: str | None = None
    while page := await harness.frontier.url_keys_page(SOURCE, after=after, limit=2):
        seen.extend(page)
        after = page[-1]
    assert sorted(seen) == sorted(surt_key(url) for url in urls) and len(seen) == 5


# -- curated vocabulary first (1.3) ----------------------------------------------------


def shipped(kind: str) -> list[dict[str, Any]]:
    return list(TEMPLATES["vocab"][kind])


async def test_seed_puts_curated_vocabulary_first_and_replaces_what_it_installed_before(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    sale = shipped("listing_type")[1]
    await harness.graphs.save_version(
        RuleGraph.empty(SOURCE, EXTRACTION).extended(
            vocab={
                "listing_type": [
                    {"pattern": "sale|بيع", "value": "SALE"},  # induced
                    {"pattern": "for-sale-v1", "value": "SALE", "origin": "HUMAN"},  # old seed
                    dict(sale),  # an untagged copy of a shipped entry
                ]
            }
        )
    )

    plan = await seeder(harness).plan(SOURCE)

    listing = plan.candidate.vocab["listing_type"]
    curated = [entry for entry in listing if is_curated_vocab(entry)]
    assert [e["pattern"] for e in curated] == [e["pattern"] for e in shipped("listing_type")]
    assert listing[: len(curated)] == curated  # curated before induced
    assert listing[len(curated) :] == [{"pattern": "sale|بيع", "value": "SALE"}]
    assert plan.vocab_retired == ["listing_type: for-sale-v1 -> SALE"]
    assert f"listing_type: {sale['pattern']} -> SALE" not in plan.vocab_added
    assert plan.candidate.vocab["property_type"] == [
        {**entry, "origin": "HUMAN"} for entry in shipped("property_type")
    ]

    await seeder(harness).apply(plan)
    again = await seeder(harness).plan(SOURCE)
    assert not again.vocab_changed and again.vocab_added == again.vocab_retired == []

    retired = await seeder(harness).plan(SOURCE, retire_vocab=[("listing_type", "sale|بيع")])
    assert retired.vocab_retired == ["listing_type: sale|بيع -> SALE"]
    assert all(is_curated_vocab(e) for e in retired.candidate.vocab["listing_type"])
    with pytest.raises(ConfigurationError):
        await seeder(harness).plan(SOURCE, retire_vocab=[("listing_type", sale["pattern"])])
    with pytest.raises(ConfigurationError):
        await seeder(harness).plan(SOURCE, retire_vocab=[("listing_type", "nope")])


def test_vocab_keys_parse_kind_then_pattern() -> None:
    assert cli._vocab_key("listing_type=sale|بيع") == ("listing_type", "sale|بيع")
    assert cli._vocab_key("property_type=(?=x)a=b") == ("property_type", "(?=x)a=b")
    with pytest.raises(ConfigurationError):
        cli._vocab_key("sale|بيع")


def classify(title: str, category: str) -> ListingType | None:
    """The curated vocabulary alone, through a one-advert list template."""
    graph = RuleGraph.empty(SOURCE, EXTRACTION).extended(
        nodes=[
            RuleNode(
                "list",
                NodeKind.TEMPLATE,
                {
                    "page_kind": "LIST",
                    "items": {"css": "li"},
                    "fields": {
                        "title": [{"css": "h3 a"}],
                        "url": [{"css": "h3 a", "attr": "href"}],
                        "category": [{"css": ".cat"}],
                    },
                },
                RuleOrigin.HUMAN,
            )
        ],
        edges=[RuleEdge(ROOT_KEY, "list", {"type": "dom_css", "css": "li h3 a"})],
        vocab=TEMPLATES["vocab"],
    )
    html = (
        f'<ul><li><h3><a href="http://cairo.olx.com.eg/shop-iid-1">{title}</a></h3>'
        f'<span class="cat">{category}</span></li></ul>'
    )
    outcome = HtmlRuleEngine().extract(
        graph,
        ArchivedDocument(html.encode(), "http://cairo.olx.com.eg/real-estate-cat-16"),
        country_code="EG",
        identity=OLX_EG_IDENTITY,
    )
    return outcome.drafts[0].listing_type if outcome and outcome.drafts else None


def test_a_rent_and_sale_category_says_nothing_so_the_title_decides() -> None:
    category = "Shops for Rent - Sale - Cairo"
    assert classify("Shop for sale in Maadi", category) is ListingType.SALE
    assert classify("Shop for rent in Maadi", category) is ListingType.RENT
    assert classify("Apartment in Maadi", "Houses - Apartments for Sale") is ListingType.SALE
    assert classify("Land for Development in Giza, Ref# 1616164", "Land - Gizeh") is (
        ListingType.SALE
    )
    assert classify("Wholesale stock of shoes", "Business - Industrial") is None


# -- the 2013 gallery variant and what tpl.v5 hid (1.2) ---------------------------------


async def curated_graph(harness: Harness) -> RuleGraph:
    return (await seeder(harness).plan(SOURCE)).candidate


async def test_the_class_based_gallery_yields_its_adverts(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    graph = await curated_graph(harness)

    outcome = harness.engine.extract(
        graph, fixture_document("a2_2013_gallery_ig"), country_code="EG", identity=OLX_EG_IDENTITY
    )

    assert outcome is not None and outcome.template_key == "olx.a2_2013_gallery"
    assert outcome.page_kind is PageKind.LIST and outcome.items_valid == 18
    assert {d.listing_type for d in outcome.drafts} == {ListingType.SALE, ListingType.RENT}
    assert all(d.external_id.isdigit() for d in outcome.drafts)
    assert sum(d.price.amount is not None for d in outcome.drafts) == 17  # one has no price
    assert PropertyType.SHOP in {d.property_type for d in outcome.drafts}


@pytest.mark.parametrize("name", ["a2_2013_sitemap_calendar", "a2_2013_mobile_menu"])
async def test_navigation_pages_without_adverts_are_curated_other(
    tmp_path: Path, name: str
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    graph = await curated_graph(harness)
    document = fixture_document(name)

    outcome = harness.engine.extract(graph, document, country_code="EG", identity=OLX_EG_IDENTITY)

    assert outcome is not None and outcome.template_key == f"olx.{name}"
    assert outcome.page_kind is PageKind.OTHER
    assert harness.engine.advert_links(document, identity=OLX_EG_IDENTITY) == 0


async def test_curated_other_templates_never_take_a_page_with_adverts(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    graph = await curated_graph(harness)
    calendar = fixture_document("a2_2013_sitemap_calendar")
    with_advert = replace(
        calendar,
        content=calendar.content.replace(
            b"</body>", b'<a href="http://www.olx.com.eg/flat-iid-123">flat</a></body>'
        ),
    )
    menu = fixture_document("a2_2013_mobile_menu")
    with_mobile_advert = replace(
        menu, content=menu.content.replace(b"</body>", b'<a href="/item/show/42">x</a></body>')
    )
    for document in (with_advert, with_mobile_advert):
        outcome = harness.engine.extract(
            graph, document, country_code="EG", identity=OLX_EG_IDENTITY
        )
        assert outcome is None


# -- replay diffs between graph versions -----------------------------------------------


async def test_graph_diff_names_every_change_a_candidate_makes(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    await harness.archive("a2_2013_list", status=RawDocumentStatus.PARSED)
    await harness.archive("a2_2013_gallery_ig", status=RawDocumentStatus.PARSED)
    baseline = await curated_graph(harness)
    audit = audit_service(harness)

    same = await audit.compare(SOURCE, baseline=baseline, candidate=baseline)
    assert same.documents == 2 and same.changed_documents == 0
    assert same.outcomes == {} and same.fields == {} and same.drafts_added == 0

    # Swap sale and rent: every advert keeps its id but flips its type.
    swapped = {
        **baseline.vocab,
        "listing_type": [
            {**entry, "value": "RENT" if entry["value"] == "SALE" else "SALE"}
            for entry in baseline.vocab["listing_type"]
        ],
    }
    flipped = await audit.compare(
        SOURCE,
        baseline=baseline,
        candidate=baseline.revised(
            remove=["olx.a2_2013_gallery"], vocab=swapped, replace_vocab=True
        ),
    )

    assert flipped.changed_documents == 2
    assert flipped.outcomes == {"olx.a2_2013_gallery (LIST) -> UNRECOGNISED": 1}
    assert flipped.drafts_lost == 18 and flipped.drafts_added == 0
    assert flipped.fields["listing_type SALE -> RENT"] > 0
    assert flipped.examples["advert lost"] and flipped.examples["listing_type SALE -> RENT"]
    assert "outcomes olx.a2_2013_gallery (LIST) -> UNRECOGNISED 1" in flipped.summary()
    assert flipped.as_dict()["drafts_lost"] == 18


async def test_seeding_stores_its_replay_diff_with_the_version(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    plan = await seeder(harness).plan(SOURCE)

    saved = await seeder(harness).apply(plan, evidence="vs v0: 1 of 1 documents change")

    assert saved.notes is not None and saved.notes.endswith("; vs v0: 1 of 1 documents change")
