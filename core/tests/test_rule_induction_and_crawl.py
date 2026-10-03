"""The LLM fallback loop and the crawl orchestration, with a scripted model."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from realestate.application.rules.seeds import seed_navigation_graph
from realestate.application.services.archive_crawl_service import (
    ArchiveCrawlService,
    CrawlSettings,
    select_captures,
)
from realestate.application.services.frontier_link_sink import FrontierLinkSink
from realestate.application.services.ingestion_service import IngestionService
from realestate.application.services.rule_induction_service import RuleInductionService
from realestate.config.settings import Settings, SourceSettings
from realestate.domain.archive import Capture, FrontierEntry, surt_key
from realestate.domain.enums import (
    CrawlStatus,
    GapStatus,
    RawDocumentKind,
    RawDocumentStatus,
    RuleDomain,
)
from realestate.domain.models import RawPayload
from realestate.domain.ports.llm import LlmMessage
from realestate.infrastructure.archive import wayback
from realestate.infrastructure.archive.wayback import WaybackClient, WaybackSettings
from realestate.infrastructure.blob.local_fs import LocalFsBlobProvider
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.sources.olx_eg_wayback.source import OlxEgWaybackDataSource
from realestate.infrastructure.sources.registry import DataSourceRegistry
from tests.archive_fakes import (
    TEMPLATES,
    FixtureIndex,
    InMemoryCursors,
    InMemoryDecisions,
    InMemoryFrontier,
    InMemoryGaps,
    InMemoryGraphs,
    InMemoryLinkRequests,
    ScriptedLlm,
    fixture_bytes,
    fixture_meta,
)
from tests.conftest import (
    InMemoryListingRepository,
    InMemoryRawDocumentRepository,
    InMemoryScrapeRunRepository,
    NullLogProvider,
)

SOURCE = "olx_eg_wayback"
LIST_URL = "http://www.olx.com.eg/houses-apartments-for-sale-cat-367"
DETAIL_URL = "http://hurgada.olx.com.eg/apartment-for-sale-in-hurghada-al-bahr-al-ahmar-ref-1890653-iid-487590261"
CAR_URL = "http://www.olx.com.eg/vehicles/cars-for-sale/toyota/"
CAR_URL_2 = "http://www.olx.com.eg/vehicles/cars-for-sale/kia/"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(wayback.asyncio, "sleep", instant)


def proposal(name: str, **overrides: Any) -> dict[str, Any]:
    """A fixture template in the shape the model is asked to answer with."""
    template = TEMPLATES[name]["template"]
    return {
        "name": name,
        "page_kind": template["page_kind"],
        "conditions": [TEMPLATES[name]["condition"]],
        "items": template.get("items"),
        "fields": template.get("fields", {}),
        "links": template.get("links", []),
        "default_currency": template.get("default_currency"),
        "vocab": TEMPLATES["vocab"],
        **overrides,
    }


def user_prompt(messages: Sequence[LlmMessage]) -> str:
    return next(m.content for m in messages if m.role == "user")


def nav_answer(messages: Sequence[LlmMessage]) -> dict[str, Any]:
    """Answer each group the way a sensible model would, by reading the prompt."""
    rules = []
    for number, _shape, block in re.findall(
        r"\[(\d+)\] shape (.*?)\n((?:    .*\n?)+)", user_prompt(messages)
    ):
        group = int(number)
        if "-cat-" in block:
            rules.append(
                {
                    "group": group,
                    "pattern": r"-cat-\d+",
                    "decision": "FETCH",
                    "page_kind": "LIST",
                    "priority": 90,
                }
            )
        elif "vehicles" in block:
            rules.append(
                {
                    "group": group,
                    "pattern": r"/vehicles/",
                    "decision": "SKIP",
                    "page_kind": "OTHER",
                    "priority": 0,
                }
            )
        elif "-iid-" in block:
            rules.append(
                {
                    "group": group,
                    "pattern": r"-iid-\d+$",
                    "decision": "DEFER",
                    "page_kind": "DETAIL",
                    "priority": 60,
                }
            )
    rules.append(
        {
            "group": 1,
            "pattern": "([unclosed",
            "decision": "FETCH",
            "page_kind": "LIST",
            "priority": 1,
        }
    )
    return {"rules": rules}


def template_answer(messages: Sequence[LlmMessage]) -> dict[str, Any]:
    if "-iid-" in user_prompt(messages).split("\n")[0]:
        return proposal("a2_2013_detail")
    # The fixture pages show no bathrooms, and induction rejects a field that
    # is empty on every advert of every sample.
    fields = TEMPLATES["a2_2013_list"]["template"]["fields"]
    return proposal(
        "a2_2013_list", fields={k: v for k, v in fields.items() if k != "bathrooms"}
    )


def test_select_captures_prefers_distinct_content_spread_over_time() -> None:
    def entry(i: int, digest: str, status: CrawlStatus = CrawlStatus.DISCOVERED) -> FrontierEntry:
        return FrontierEntry(i, SOURCE, "k", f"2013{i:02d}01000000", "u", digest, status)

    captures = [
        entry(1, "A"),
        entry(2, "A"),
        entry(3, "B"),
        entry(4, "C"),
        entry(5, "D"),
        entry(6, "E"),
    ]
    assert select_captures(captures, 2) == {1, 6}
    assert select_captures(captures, 3) == {1, 4, 6}
    assert select_captures(captures, 10) == {1, 3, 4, 5, 6}  # duplicate digest "A" fetched once
    assert select_captures(
        [entry(1, "A", CrawlStatus.FETCHED), entry(2, "A"), entry(3, "B")], 2
    ) == {3}
    assert select_captures([entry(1, "A", CrawlStatus.FETCHED), entry(2, "B")], 1) == set()


class Harness:
    """The real services wired to in-memory repositories and a scripted model."""

    def __init__(
        self,
        tmp_path: Path,
        llm: ScriptedLlm,
        captures: Sequence[Capture],
        *,
        years: tuple[int, int] = (2013, 2013),
        hidden: Sequence[Capture] = (),
    ) -> None:
        log = NullLogProvider()
        self.frontier = InMemoryFrontier()
        self.link_requests = InMemoryLinkRequests()
        self.graphs = InMemoryGraphs()
        self.gaps = InMemoryGaps()
        self.decisions = InMemoryDecisions()
        self.documents = InMemoryRawDocumentRepository()
        self.listings = InMemoryListingRepository()
        self.blob = LocalFsBlobProvider(tmp_path / "blob")
        self.engine = HtmlRuleEngine()
        self.llm = llm
        self.served: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            self.served.append(url)
            name = (
                "a2_2013_list"
                if "cat-367" in url
                else ("a2_2013_detail" if "-iid-" in url else None)
            )
            if name is None:
                return httpx.Response(404)
            return httpx.Response(
                200, content=fixture_bytes(name), headers={"Content-Type": "text/html"}
            )

        settings = Settings(sources={SOURCE: SourceSettings(enabled=False)})
        registry = DataSourceRegistry(settings, log)
        registry.register(
            key=SOURCE,
            display_name="OLX",
            country_code="EG",
            factory=lambda ctx: OlxEgWaybackDataSource(
                log=ctx.log,
                params={"from_year": years[0], "to_year": years[1]},
                frontier=self.frontier,
                graphs=self.graphs,
                engine=self.engine,
                client=WaybackClient(
                    WaybackSettings(min_delay_seconds=0),
                    log=ctx.log,
                    transport=httpx.MockTransport(handler),
                ),
            ),
        )
        self.ingestion = IngestionService(
            registry=registry,
            blob=self.blob,
            listings=self.listings,
            documents=self.documents,  # type: ignore[arg-type]
            runs=InMemoryScrapeRunRepository(),  # type: ignore[arg-type]
            log=log,
            links=FrontierLinkSink(self.frontier, self.link_requests),
        )
        self.induction = RuleInductionService(
            graphs=self.graphs,
            gaps=self.gaps,
            decisions=self.decisions,
            llm=llm,
            engine=self.engine,
            documents=self.documents,  # type: ignore[arg-type]
            blob=self.blob,
            frontier=self.frontier,
            log=log,
            registry=registry,
        )
        self.registry = registry
        self.index = FixtureIndex(captures, hidden)
        self.cursors = InMemoryCursors()
        self.crawler = ArchiveCrawlService(
            registry=registry,
            frontier=self.frontier,
            cursors=self.cursors,
            index=self.index,
            graphs=self.graphs,
            engine=self.engine,
            ingestion=self.ingestion,
            induction=self.induction,
            log=log,
            settings=CrawlSettings(cdx_page_size=2),
            links=self.link_requests,
        )

    async def archive(
        self, name: str, *, status: RawDocumentStatus = RawDocumentStatus.UNRECOGNISED
    ) -> None:
        meta = fixture_meta(name)
        payload = RawPayload(
            content=fixture_bytes(name),
            kind=RawDocumentKind.HTML,
            content_type="text/html",
            source_url=meta["url"],
            meta={"timestamp": meta["timestamp"], "original_url": meta["url"]},
        )
        blob = await self.blob.put(
            f"{SOURCE}/{name}.html", payload.content, content_type="text/html"
        )
        document = await self.documents.create(source_key=SOURCE, payload=payload, blob=blob)
        if status is RawDocumentStatus.UNRECOGNISED:
            await self.documents.mark_unrecognised(document.id, "test")


def captures() -> list[Capture]:
    return [
        Capture(surt_key(LIST_URL), "20130305032637", LIST_URL, "D1"),
        Capture(surt_key(LIST_URL), "20130401000000", LIST_URL, "D2"),
        Capture(surt_key(LIST_URL), "20130402000000", LIST_URL, "D2"),  # same content as D2
        Capture(surt_key(DETAIL_URL), "20130310000000", DETAIL_URL, "D3"),
        Capture(surt_key(CAR_URL), "20130311000000", CAR_URL, "D4"),
        Capture(surt_key(CAR_URL_2), "20130312000000", CAR_URL_2, "D5"),
    ]


async def test_navigation_induction_compiles_valid_rules_and_rejects_bad_ones(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(by_tool={"submit_rules": [nav_answer]}), captures())
    await harness.crawler.enumerate(SOURCE)
    routed = await harness.crawler.route(SOURCE)
    assert routed.unrouted == 4  # only the seed rule exists: nothing matches yet

    assert await harness.induction.collect_navigation_gaps(SOURCE) == 3
    report = await harness.induction.induce(SOURCE, domain=RuleDomain.NAVIGATION, max_calls=5)

    assert (report.llm_calls, report.accepted) == (1, 3)
    graph = await harness.graphs.active(SOURCE, RuleDomain.NAVIGATION)
    assert graph is not None and graph.version == 2  # seed v1 + induced rules
    assert all(gap.status is GapStatus.RESOLVED for gap in harness.gaps.gaps.values())
    decision = next(iter(harness.decisions.decisions.values()))
    assert decision.valid and "regex does not compile" in (decision.error or "")

    routed = await harness.crawler.route(SOURCE)
    assert (routed.queued, routed.skipped, routed.deferred, routed.unrouted) == (1, 2, 1, 0)
    queued = harness.frontier.by_status(CrawlStatus.QUEUED)
    assert sorted(e.digest for e in queued) == ["D1", "D2"]  # duplicate D2 capture skipped


async def test_navigation_rule_must_cover_its_samples(tmp_path: Path) -> None:
    def too_narrow(messages: Sequence[LlmMessage]) -> dict[str, Any]:
        return {
            "rules": [
                {
                    "group": 1,
                    "pattern": "nothing-like-this",
                    "decision": "SKIP",
                    "page_kind": "OTHER",
                    "priority": 0,
                }
            ]
        }

    harness = Harness(tmp_path, ScriptedLlm(by_tool={"submit_rules": [too_narrow]}), captures()[4:])
    await harness.crawler.enumerate(SOURCE)
    await harness.crawler.route(SOURCE)
    await harness.induction.collect_navigation_gaps(SOURCE)
    report = await harness.induction.induce(SOURCE, domain=RuleDomain.NAVIGATION, max_calls=1)
    assert (report.accepted, report.rejected) == (0, 1)
    gap = next(iter(harness.gaps.gaps.values()))
    assert (
        gap.status is GapStatus.OPEN
        and gap.attempts == 1
        and "matches 0/" in (gap.last_error or "")
    )


async def test_mixed_groups_take_several_ordered_rules(tmp_path: Path) -> None:
    property_cats = [f"http://c{i}.olx.com.eg/houses-apartments-for-sale-cat-367" for i in range(3)]
    job_cats = [f"http://c{i}.olx.com.eg/accounting-jobs-cat-25{i}" for i in range(3)]
    mixed = [
        Capture(surt_key(url), f"2013010{i}000000", url, f"D{i}")
        for i, url in enumerate(property_cats + job_cats)
    ]

    def answer(messages: Sequence[LlmMessage]) -> dict[str, Any]:
        return {
            "rules": [
                {
                    "group": 1,
                    "pattern": r"-cat-(?:16|363|367)$",
                    "decision": "FETCH",
                    "page_kind": "LIST",
                    "priority": 90,
                },
                {
                    "group": 1,
                    "pattern": r"-cat-\d+$",
                    "decision": "SKIP",
                    "page_kind": "OTHER",
                    "priority": 0,
                },
            ]
        }

    harness = Harness(tmp_path, ScriptedLlm(by_tool={"submit_rules": [answer]}), mixed)
    await harness.crawler.enumerate(SOURCE)
    await harness.crawler.route(SOURCE)
    assert await harness.induction.collect_navigation_gaps(SOURCE) == 1  # one shape, two kinds
    report = await harness.induction.induce(SOURCE, domain=RuleDomain.NAVIGATION, max_calls=1)
    assert report.accepted == 2

    routed = await harness.crawler.route(SOURCE)
    assert (routed.queued, routed.skipped) == (3, 3)  # FETCH listed first, so it wins
    assert all("cat-367" in e.original_url for e in harness.frontier.by_status(CrawlStatus.QUEUED))


async def test_template_induction_repairs_after_feedback(tmp_path: Path) -> None:
    broken = proposal("a2_2013_list", items={"css": "ul.not-here > li"})
    harness = Harness(
        tmp_path, ScriptedLlm(by_tool={"submit_template": [broken, proposal("a2_2013_list")]}), []
    )
    await harness.archive("a2_2013_list")
    await harness.archive("a2_2013_list_alex")

    assert await harness.induction.collect_extraction_gaps(SOURCE) == 1  # one design, two pages
    report = await harness.induction.induce(SOURCE, domain=RuleDomain.EXTRACTION, max_calls=5)

    assert (report.llm_calls, report.accepted) == (2, 1)
    feedback = harness.llm.calls[1][1][-1].content
    assert "items selector found 0 element(s)" in feedback
    graph = await harness.graphs.active(SOURCE, RuleDomain.EXTRACTION)
    assert graph is not None and graph.version == 1 and len(graph.terminals()) == 1
    assert graph.vocab["listing_type"]

    reparsed = await harness.ingestion.reparse_unrecognised(SOURCE)
    assert reparsed.created > 30
    assert not await harness.documents.list_by_status(
        source_key=SOURCE, status=RawDocumentStatus.UNRECOGNISED
    )


async def test_field_that_never_fills_is_sent_back(tmp_path: Path) -> None:
    good = proposal("a2_2013_list")
    wrong_area = proposal(
        "a2_2013_list",
        fields={**good["fields"], "area": [{"css": "div", "regex": r"(\d+)\s*sqft-nowhere"}]},
    )
    harness = Harness(tmp_path, ScriptedLlm(by_tool={"submit_template": [wrong_area, good]}), [])
    await harness.archive("a2_2013_list")
    await harness.archive("a2_2013_list_alex")  # two pages: enough evidence
    await harness.induction.collect_extraction_gaps(SOURCE)

    report = await harness.induction.induce(SOURCE, domain=RuleDomain.EXTRACTION, max_calls=5)

    assert (report.llm_calls, report.accepted) == (2, 1)
    assert "field 'area' produced no value" in harness.llm.calls[1][1][-1].content


async def test_template_without_vocabulary_is_sent_back(tmp_path: Path) -> None:
    no_vocab = proposal("a2_2013_list", vocab={})
    harness = Harness(
        tmp_path, ScriptedLlm(by_tool={"submit_template": [no_vocab, no_vocab, no_vocab]}), []
    )
    await harness.archive("a2_2013_list")
    await harness.induction.collect_extraction_gaps(SOURCE)
    report = await harness.induction.induce(SOURCE, domain=RuleDomain.EXTRACTION, max_calls=5)
    assert (report.accepted, report.rejected, report.llm_calls) == (0, 1, 3)
    assert "no listing_type vocab pattern matched" in harness.llm.calls[1][1][-1].content


async def test_valid_answers_are_reused_from_the_ledger(tmp_path: Path) -> None:
    llm = ScriptedLlm(by_tool={"submit_template": [proposal("a2_2013_list")]})
    harness = Harness(tmp_path, llm, [])
    await harness.archive("a2_2013_list")
    await harness.induction.collect_extraction_gaps(SOURCE)
    await harness.induction.induce(SOURCE, domain=RuleDomain.EXTRACTION, max_calls=1)

    # Same question again (a fresh rule store, the same ledger): no model call.
    harness.graphs.saved.clear()
    harness.gaps.gaps.clear()
    await harness.induction.collect_extraction_gaps(SOURCE)
    report = await harness.induction.induce(SOURCE, domain=RuleDomain.EXTRACTION, max_calls=1)
    assert (report.llm_calls, report.cache_hits, report.accepted) == (0, 1, 1)
    assert len(llm.calls) == 1


async def test_crawl_end_to_end_follows_evidence_to_deferred_detail_pages(tmp_path: Path) -> None:
    llm = ScriptedLlm(
        by_tool={
            "submit_rules": [nav_answer],
            "submit_template": [template_answer, template_answer],
        }
    )
    harness = Harness(tmp_path, llm, captures())

    report = await harness.crawler.crawl(SOURCE, rounds=3, max_fetches=9, max_llm_calls=6)

    assert report.enumeration is not None and report.enumeration.added == 6
    first, second = report.rounds[0], report.rounds[1]
    # Round 1: navigation rules induced, list captures fetched, unrecognised,
    # a template induced, the pages re-parsed, and their links recorded.
    assert (
        first.navigation.accepted == 3
        and first.run is not None
        and first.run.documents_fetched == 2
    )
    assert first.extraction.accepted == 1 and first.reparsed_created >= 30
    detail = next(e for e in harness.frontier.rows.values() if e.url_key == surt_key(DETAIL_URL))
    assert detail.evidence == {"linked_as": ["DETAIL"]}
    # Round 2: the deferred detail URL is now fetched thanks to that evidence.
    assert (
        second.routed.queued == 1 and second.run is not None and second.run.documents_fetched == 1
    )
    assert second.extraction.accepted == 1
    assert any("-iid-487590261" in url for url in harness.served)
    assert not any("vehicles" in url for url in harness.served)

    assert report.llm_calls == 3
    statuses = await harness.frontier.counts(SOURCE)
    assert statuses[CrawlStatus.FETCHED] == 3 and statuses[CrawlStatus.UNROUTED] == 0
    assert not await harness.documents.list_by_status(
        source_key=SOURCE, status=RawDocumentStatus.UNRECOGNISED
    )


async def test_crawl_until_idle_stops_by_itself_and_parses_leftovers(tmp_path: Path) -> None:
    llm = ScriptedLlm(
        by_tool={
            "submit_rules": [nav_answer],
            "submit_template": [template_answer, template_answer],
        }
    )
    harness = Harness(tmp_path, llm, captures())
    # A payload archived by an interrupted run and never parsed.
    meta = fixture_meta("a2_2013_list")
    content = fixture_bytes("a2_2013_list")
    blob = await harness.blob.put(f"{SOURCE}/leftover.html", content, content_type="text/html")
    await harness.documents.create(
        source_key=SOURCE,
        payload=RawPayload(
            content=content,
            kind=RawDocumentKind.HTML,
            content_type="text/html",
            source_url=meta["url"],
            meta={"timestamp": meta["timestamp"], "original_url": meta["url"]},
        ),
        blob=blob,
    )

    report = await harness.crawler.crawl(SOURCE, rounds=0, max_fetches=100, max_llm_calls=6)

    assert report.stopped.startswith("no progress")
    assert len(report.rounds) == 3  # the third round found nothing left to do
    for status in (RawDocumentStatus.PENDING, RawDocumentStatus.UNRECOGNISED):
        assert not await harness.documents.list_by_status(source_key=SOURCE, status=status)


async def test_crawl_stops_when_the_fetch_budget_is_spent(tmp_path: Path) -> None:
    llm = ScriptedLlm(by_tool={"submit_rules": [nav_answer], "submit_template": []})
    harness = Harness(tmp_path, llm, captures())

    report = await harness.crawler.crawl(SOURCE, rounds=0, max_fetches=1, max_llm_calls=1)

    assert report.stopped == "fetch budget spent"
    assert sum(r.run.documents_fetched for r in report.rounds if r.run) == 1


async def test_enumeration_resumes_per_year(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    partial = await harness.crawler.enumerate(SOURCE, max_pages=1)
    assert (partial.added, partial.years_completed) == (2, [])
    rest = await harness.crawler.enumerate(SOURCE)
    assert rest.added == 4 and rest.years_completed == [2013]
    assert harness.index.requests[1][2] == "2"  # resumed from the saved key
    assert (await harness.crawler.enumerate(SOURCE)).pages == 0  # year done


def test_seed_graph_is_generic() -> None:
    graph = seed_navigation_graph("anything")
    assert len(graph.terminals()) == 1
    assert graph.terminals()[0].action["page_kind"] == "FROM_EVIDENCE"
