"""Induction must judge rules the way they will run, on current evidence.

Each test reproduces a hole found in the rule induction review
(docs/rule-induction-review.md, Phase 0): a rule or template that passed
validation yet changed nothing, changed the wrong thing, or hid inputs from
induction for good.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from realestate.application.rules.proposals import VocabEntry, bound_latin_words
from realestate.application.rules.seeds import seed_navigation_graph
from realestate.application.services import ingestion_service
from realestate.application.services.rule_induction_service import (
    InductionReport,
    InductionSettings,
)
from realestate.domain.archive import Capture, surt_key, url_shape
from realestate.domain.enums import (
    CrawlStatus,
    GapStatus,
    NodeKind,
    RawDocumentStatus,
    RuleDomain,
    RuleOrigin,
)
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode
from realestate.infrastructure.sources.olx_eg_wayback.source import OLX_EG_IDENTITY
from tests.archive_fakes import TEMPLATES, InMemoryGaps, ScriptedLlm
from tests.test_quality_pipeline import archive_bytes, detail_proposal, samples_of, validate
from tests.test_rule_induction_and_crawl import SOURCE, Harness, captures

NAV = RuleDomain.NAVIGATION
EXTRACTION = RuleDomain.EXTRACTION


def nav_rule(group: int, pattern: str, decision: str, page_kind: str = "OTHER") -> dict[str, Any]:
    return {
        "group": group,
        "pattern": pattern,
        "decision": decision,
        "page_kind": page_kind,
        "priority": 50,
    }


async def unrouted(
    harness: Harness, urls: list[str], *, status: CrawlStatus = CrawlStatus.UNROUTED
) -> None:
    """Put ``urls`` in the frontier with ``status``."""
    await harness.frontier.add_captures(
        SOURCE, [Capture(surt_key(url), "20130301000000", url, f"D{url}") for url in urls]
    )
    ids = [entry.id for entry in harness.frontier.rows.values() if entry.original_url in urls]
    await harness.frontier.set_route(ids, status=status)


def prompt_of(llm: ScriptedLlm) -> str:
    return "\n".join(m.content for _, messages in llm.calls for m in messages if m.role == "user")


# -- navigation: fresh samples (0.1) ---------------------------------------------------


async def test_navigation_asks_only_about_urls_still_unrouted(tmp_path: Path) -> None:
    routed = ["http://www.olx.com.eg/cars-cat-362", "http://www.olx.com.eg/jobs-cat-363"]
    missed = "http://www.olx.com.eg/shops-for-rent-sale-cat-415"
    llm = ScriptedLlm(
        by_tool={"submit_rules": [{"rules": [nav_rule(1, r"-cat-415", "FETCH", "LIST")]}]}
    )
    harness = Harness(tmp_path, llm, captures())
    await harness.graphs.save_version(
        seed_navigation_graph(SOURCE).extended(
            nodes=[RuleNode("old", NodeKind.ROUTE, {"decision": "SKIP"}, RuleOrigin.LLM)],
            edges=[RuleEdge(ROOT_KEY, "old", {"type": "url_regex", "pattern": r"-cat-36\d"})],
        )
    )
    await unrouted(harness, routed, status=CrawlStatus.SKIPPED)
    await unrouted(harness, [missed])
    # The gap was first seen when the routed URLs were its misses.
    await harness.gaps.record(SOURCE, NAV, url_shape(missed), samples=routed, occurrences=1)

    report = await harness.induction.induce(SOURCE, domain=NAV, max_calls=1)

    prompt = prompt_of(llm)
    assert missed in prompt and not any(url in prompt for url in routed)
    assert report.accepted == 1
    graph = await harness.graphs.active(SOURCE, NAV)
    assert graph is not None
    outcome = harness.engine.route(graph, missed, {})
    assert outcome is not None and outcome.decision.value == "FETCH"


async def test_a_gap_whose_urls_are_all_routed_by_now_is_not_asked_about(tmp_path: Path) -> None:
    llm = ScriptedLlm()
    harness = Harness(tmp_path, llm, captures())
    url = "http://www.olx.com.eg/cars-cat-362"
    await harness.graphs.save_version(
        seed_navigation_graph(SOURCE).extended(
            nodes=[RuleNode("cars", NodeKind.ROUTE, {"decision": "SKIP"}, RuleOrigin.LLM)],
            edges=[RuleEdge(ROOT_KEY, "cars", {"type": "url_regex", "pattern": r"-cat-362"})],
        )
    )
    await unrouted(harness, [url])  # left UNROUTED by a routing pass before "cars" existed
    await harness.gaps.record(SOURCE, NAV, url_shape(url), samples=[url], occurrences=1)

    report = await harness.induction.induce(SOURCE, domain=NAV, max_calls=1)

    assert llm.calls == [] and report.llm_calls == 0


def test_gap_samples_put_current_inputs_first() -> None:
    async def run() -> list[str]:
        gaps = InMemoryGaps()
        await gaps.record(SOURCE, NAV, "shape", samples=["a", "b"], max_samples=3)
        gap = await gaps.record(SOURCE, NAV, "shape", samples=["c", "d"], max_samples=3)
        return gap.samples

    assert asyncio.run(run()) == ["c", "d", "a"]


# -- navigation: rules judged as they run (0.2) ----------------------------------------

SALE_LIST = "http://www.olx.com.eg/houses-apartments-for-sale-cat-367-p-2"
JUNK = ["http://www.olx.com.eg/vehicles-cat-362", "http://www.olx.com.eg/jobs-cat-25"]


async def test_a_rule_overriding_another_groups_rule_is_rejected(tmp_path: Path) -> None:
    answer = {
        "rules": [
            nav_rule(1, r"-cat-\d+", "SKIP"),  # would also SKIP group 2's property list
            nav_rule(2, r"-cat-367", "FETCH", "LIST"),
        ]
    }
    llm = ScriptedLlm(by_tool={"submit_rules": [answer]})
    harness = Harness(tmp_path, llm, captures())
    await unrouted(harness, [*JUNK, SALE_LIST])
    await harness.induction.collect_navigation_gaps(SOURCE)

    report = await harness.induction.induce(SOURCE, domain=NAV, max_calls=1)

    graph = await harness.graphs.active(SOURCE, NAV)
    assert graph is not None
    outcome = harness.engine.route(graph, SALE_LIST, {})
    assert outcome is not None and outcome.decision.value == "FETCH"
    assert harness.engine.route(graph, JUNK[0], {}) is None
    assert report.accepted == 1
    statuses = {gap.fingerprint: gap.status for gap in harness.gaps.gaps.values()}
    assert statuses[url_shape(SALE_LIST)] is GapStatus.RESOLVED
    assert statuses[url_shape(JUNK[0])] is GapStatus.OPEN  # counted as a failed attempt
    failed = next(g for g in harness.gaps.gaps.values() if g.fingerprint == url_shape(JUNK[0]))
    assert failed.attempts == 1 and "says FETCH" in (failed.last_error or "")


async def test_the_same_rule_given_for_several_groups_is_saved_once(tmp_path: Path) -> None:
    urls = [
        "http://www.olx.com.eg/vehicles/cars/toyota/",
        "http://www.olx.com.eg/en/vehicles/bikes/honda/",
    ]
    assert url_shape(urls[0]) != url_shape(urls[1])
    answer = {"rules": [nav_rule(1, "/vehicles/", "SKIP"), nav_rule(2, "/vehicles/", "SKIP")]}
    harness = Harness(tmp_path, ScriptedLlm(by_tool={"submit_rules": [answer]}), captures())
    await unrouted(harness, urls)
    await harness.induction.collect_navigation_gaps(SOURCE)

    report = await harness.induction.induce(SOURCE, domain=NAV, max_calls=1)

    assert report.accepted == 1
    assert all(gap.status is GapStatus.RESOLVED for gap in harness.gaps.gaps.values())


async def test_a_rule_reaching_across_the_sites_url_shapes_is_rejected(tmp_path: Path) -> None:
    landing = "http://alexandria.olx.com.eg/?v=1"
    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike".split()
    words += "november oscar papa quebec romeo sierra tango uniform victor whiskey".split()
    site = [f"http://www.olx.com.eg/{word}/listing/page" for word in words]
    catch_all = nav_rule(1, r"\.(olx\.com\.eg)", "DEFER", "LIST")  # nav.v42.11, live
    llm = ScriptedLlm(by_tool={"submit_rules": [{"rules": [catch_all]}]})
    harness = Harness(tmp_path, llm, captures())
    await unrouted(harness, site, status=CrawlStatus.SKIPPED)
    await unrouted(harness, [landing])
    await harness.induction.collect_navigation_gaps(SOURCE)

    report = await harness.induction.induce(SOURCE, domain=NAV, max_calls=1)

    assert report.accepted == 0
    gap = next(g for g in harness.gaps.gaps.values() if g.fingerprint == url_shape(landing))
    assert "URL shapes" in (gap.last_error or "")


# -- extraction: candidates judged inside the graph (0.3) ------------------------------

LIST_URL = "http://www.olx.com.eg/flats-for-sale-cat-367"


def list_page(third_id: str) -> bytes:
    rows = "".join(
        f'<div class="ad"><a class="t" href="/flat-for-sale-iid-{iid}">Flat for sale {iid}</a>'
        f'<span class="id">{shown}</span></div>'
        for iid, shown in (("101", "101"), ("102", "102"), ("103", third_id))
    )
    return f"<html><body><div id='rows'>{rows}</div></body></html>".encode()


def curated_list_graph() -> RuleGraph:
    template = {
        "page_kind": "LIST",
        "items": {"css": "#rows .ad"},
        "fields": {
            "title": [{"css": "a.t"}],
            "url": [{"css": "a.t", "attr": "href"}],
            "external_id": [{"css": ".id"}],
        },
    }
    return RuleGraph.empty(SOURCE, EXTRACTION).extended(
        nodes=[RuleNode("olx.rows", NodeKind.TEMPLATE, template, RuleOrigin.HUMAN)],
        edges=[RuleEdge(ROOT_KEY, "olx.rows", {"type": "dom_css", "css": "#rows .ad"})],
        vocab=TEMPLATES["vocab"],
    )


async def parsed_with_report(harness: Harness, content: bytes, report: dict[str, Any]) -> UUID:
    document_id = UUID(await archive_bytes(harness, content, LIST_URL, "20130301000000"))
    await harness.documents.mark_parsed(document_id, graph_version=1, report=report)
    return document_id


async def test_a_gap_a_curated_template_wins_is_left_to_a_human(tmp_path: Path) -> None:
    llm = ScriptedLlm()
    harness = Harness(tmp_path, llm, captures())
    await harness.graphs.save_version(curated_list_graph())
    # The curated template wins but drops the third advert as an identity conflict.
    flagged = {
        "template_key": "olx.rows",
        "items_total": 3,
        "items_valid": 2,
        "identity_problems": 1,
    }
    await parsed_with_report(harness, list_page("999"), flagged)
    assert await harness.induction.collect_extraction_gaps(SOURCE) == 1

    report = await harness.induction.induce(SOURCE, domain=EXTRACTION, max_calls=3)

    assert llm.calls == [] and report.versions == []
    (gap,) = harness.gaps.gaps.values()
    assert gap.status is GapStatus.NEEDS_HUMAN and "olx.rows" in (gap.last_error or "")
    graph = await harness.graphs.active(SOURCE, EXTRACTION)
    assert graph is not None and graph.version == 1


async def test_a_gap_the_current_rules_handle_by_now_costs_no_call(tmp_path: Path) -> None:
    llm = ScriptedLlm()
    harness = Harness(tmp_path, llm, captures())
    await harness.graphs.save_version(curated_list_graph())
    # Stored when an older graph malfunctioned; the current one parses it cleanly.
    stale = {"template_key": "old", "items_total": 3, "items_valid": 0, "identity_problems": 3}
    await parsed_with_report(harness, list_page("103"), stale)
    await harness.induction.collect_extraction_gaps(SOURCE)

    report = await harness.induction.induce(SOURCE, domain=EXTRACTION, max_calls=3)

    assert llm.calls == [] and report.versions == []
    (gap,) = harness.gaps.gaps.values()
    assert gap.status is GapStatus.OPEN


# -- extraction: OTHER needs evidence of no adverts (0.4) ------------------------------

OTHER_ON_THE_LIST = {
    "name": "nothing_here",
    "page_kind": "OTHER",
    "conditions": [{"type": "dom_css", "css": "#the-list"}],
    "fields": {},
}


async def test_other_is_refused_for_a_page_linking_to_adverts(tmp_path: Path) -> None:
    llm = ScriptedLlm(by_tool={"submit_template": [OTHER_ON_THE_LIST] * 3})
    harness = Harness(tmp_path, llm, captures())
    await harness.archive("a2_2013_list")
    await harness.induction.collect_extraction_gaps(SOURCE)

    report = await harness.induction.induce(SOURCE, domain=EXTRACTION, max_calls=3)

    assert report.versions == [] and report.rejected == 1
    (gap,) = harness.gaps.gaps.values()
    assert gap.status is GapStatus.OPEN and "links to" in (gap.last_error or "")


async def test_other_is_accepted_for_a_page_without_adverts(tmp_path: Path) -> None:
    empty = {
        "name": "no_results",
        "page_kind": "OTHER",
        "conditions": [{"type": "dom_css", "css": "#noresults"}],
        "fields": {},
    }
    harness = Harness(tmp_path, ScriptedLlm(by_tool={"submit_template": [empty]}), captures())
    page = b"<html><body><div id='noresults'>We are sorry, no results</div></body></html>"
    await archive_bytes(
        harness,
        page,
        "http://www.olx.com.eg/housing-swap-cat-567",
        "20130301000000",
        status=RawDocumentStatus.UNRECOGNISED,
    )
    await harness.induction.collect_extraction_gaps(SOURCE)

    report = await harness.induction.induce(SOURCE, domain=EXTRACTION, max_calls=1)

    assert report.accepted == 1


async def test_parse_reports_count_advert_links_on_other_pages(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    other = RuleGraph.empty(SOURCE, EXTRACTION).extended(
        nodes=[RuleNode("other", NodeKind.TEMPLATE, {"page_kind": "OTHER"}, RuleOrigin.LLM)],
        edges=[RuleEdge(ROOT_KEY, "other", {"type": "dom_css", "css": "#the-list"})],
    )
    await harness.graphs.save_version(other)
    await harness.archive("a2_2013_list", status=RawDocumentStatus.PENDING)

    await harness.ingestion.parse_pending(SOURCE)

    (document,) = harness.documents.documents.values()
    report = document.parse_report or {}  # type: ignore[attr-defined]
    assert report["page_kind"] == "OTHER" and report["advert_links"] >= 3
    # ... which makes it a gap rather than a recognised page.
    assert await harness.induction.collect_extraction_gaps(SOURCE) == 1


async def test_a_page_no_template_recognises_stores_its_fingerprint(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    await harness.archive("a2_2013_list", status=RawDocumentStatus.PENDING)

    await harness.ingestion.parse_pending(SOURCE)

    (document,) = harness.documents.documents.values()
    assert document.status is RawDocumentStatus.UNRECOGNISED  # type: ignore[attr-defined]
    assert len(document.parse_report["fingerprint"]) == 16  # type: ignore[attr-defined]


def test_advert_links_count_untrusted_advert_urls_too() -> None:
    i2 = "https://www.olx.com.eg/ad/flat-in-maadi-IDa2hfM.html"
    numeric = "https://www.olx.com.eg/ad/flat-ID196340922.html"
    assert OLX_EG_IDENTITY.canonical_id(i2) is None
    assert OLX_EG_IDENTITY.advert_key(i2) == "a2hfM"
    assert OLX_EG_IDENTITY.advert_key(numeric) == OLX_EG_IDENTITY.canonical_id(numeric)
    assert OLX_EG_IDENTITY.advert_key("http://www.olx.com.eg/") is None


# -- normalising: vocabulary guards (0.5) ----------------------------------------------


@pytest.mark.parametrize("pattern", [".", "e", "[a-z]+", ".{3}", r"\w+"])
def test_vocabulary_that_matches_anything_is_refused(pattern: str) -> None:
    with pytest.raises(ValueError):
        VocabEntry(pattern=pattern, value="SALE")


def test_latin_vocabulary_words_start_at_a_word_boundary() -> None:
    assert bound_latin_words("rent|rental|للايجار") == r"\brent|\brental|للايجار"
    assert bound_latin_words("for[- ]sale|بيع") == r"\bfor[- ]sale|بيع"
    assert bound_latin_words("(villa|فيلا)") == "(villa|فيلا)"
    entry = VocabEntry(pattern="rent", value="RENT")
    assert re.search(entry.pattern, "Rentals in Gouna", re.IGNORECASE)
    assert not re.search(entry.pattern, "in a different compound", re.IGNORECASE)


async def test_a_catch_all_vocabulary_entry_sends_the_template_back(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    answer = detail_proposal()
    answer["vocab"] = {"listing_type": [{"pattern": ".", "value": "SALE"}]}

    errors = await validate(harness, answer, samples_of("1", "2"))

    assert any("too short" in error for error in errors), errors


# -- gap lifecycle: FAILED gaps keep counting (0.6) ------------------------------------


def test_a_failed_gap_keeps_counting_and_reopens_once_it_outgrows_its_failure() -> None:
    async def run() -> None:
        gaps = InMemoryGaps()
        gap = await gaps.record(SOURCE, EXTRACTION, "fp", samples=["a"], occurrences=2)
        for _ in range(3):
            gap = await gaps.record_failure(gap.id, "no", max_attempts=3, attempted_with="m|p")
        assert gap.status is GapStatus.FAILED and gap.failed_occurrences == 2

        await gaps.reset_counts(SOURCE, EXTRACTION)
        gap = await gaps.record(SOURCE, EXTRACTION, "fp", samples=["b"], occurrences=5)
        assert gap.status is GapStatus.FAILED and gap.occurrences == 5
        assert gap.samples == ["b", "a"]
        gap = await gaps.record(SOURCE, EXTRACTION, "fp", samples=["c"], occurrences=3)
        assert gap.status is GapStatus.OPEN and gap.attempts == 0

    asyncio.run(run())


async def test_a_failed_design_still_counts_its_pages_and_retries_for_a_new_prompt(
    tmp_path: Path,
) -> None:
    llm = ScriptedLlm(by_tool={"submit_template": [None] * 9})
    harness = Harness(tmp_path, llm, captures())
    await harness.archive("a2_2013_list")
    for _ in range(3):
        await harness.induction.collect_extraction_gaps(SOURCE)
        await harness.induction.induce(SOURCE, domain=EXTRACTION, max_calls=3)
    (gap,) = harness.gaps.gaps.values()
    assert gap.status is GapStatus.FAILED and gap.attempted_with == "scripted|tpl-3"

    await harness.archive("a2_2013_list_alex")  # the same design, captured again
    await harness.induction.collect_extraction_gaps(SOURCE)
    (gap,) = harness.gaps.gaps.values()
    assert gap.status is GapStatus.FAILED and gap.occurrences == 2

    calls = len(llm.calls)
    assert (await harness.induction.induce(SOURCE, domain=EXTRACTION, max_calls=3)).llm_calls == 0
    # A different prompt or model gets a fresh try at it.
    harness.gaps.gaps[gap.id] = replace(gap, attempted_with="scripted|tpl-2")
    await harness.induction.induce(SOURCE, domain=EXTRACTION, max_calls=1)
    assert len(llm.calls) == calls + 1


# -- the crawl loop: progress means effect (0.7) ---------------------------------------


async def test_crawl_until_idle_stops_when_a_new_version_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    rounds = 0

    async def dead_versions(
        source_key: str, *, domain: RuleDomain | None = None, **_: Any
    ) -> InductionReport:
        nonlocal rounds
        if domain is not EXTRACTION:
            return InductionReport()
        rounds += 1
        # The in-memory loop never yields, so a timeout could not stop it.
        assert rounds < 5, "the crawl kept going on versions that changed nothing"
        return InductionReport(versions=[7])

    monkeypatch.setattr(harness.induction, "induce", dead_versions)

    report = await harness.crawler.crawl(SOURCE, rounds=0, max_fetches=10, max_llm_calls=5)

    assert len(report.rounds) == 1 and report.stopped.startswith("no progress")


async def test_gaps_are_counted_even_without_an_llm_budget(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())

    await harness.crawler.crawl(SOURCE, rounds=1, max_fetches=10, max_llm_calls=0)

    assert any(gap.domain is NAV and gap.occurrences for gap in harness.gaps.gaps.values())


# -- full scans instead of oldest windows (0.8) ----------------------------------------


async def test_gap_collection_reaches_documents_past_its_query_batch(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    harness.induction._settings = InductionSettings(extraction_scan_batch=2)
    for _ in range(3):
        await harness.archive("a2_2013_list")  # one design, oldest
    await harness.archive("a2_2013_detail")  # another design, newest

    assert await harness.induction.collect_extraction_gaps(SOURCE) == 2
    assert sorted(gap.occurrences for gap in harness.gaps.gaps.values()) == [1, 3]


async def test_an_unbounded_stale_replay_reaches_every_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingestion_service, "_STALE_BATCH", 1)
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    detail = TEMPLATES["a2_2013_detail"]
    first = RuleGraph.empty(SOURCE, EXTRACTION).extended(
        nodes=[RuleNode("detail", NodeKind.TEMPLATE, detail["template"], RuleOrigin.HUMAN)],
        edges=[RuleEdge(ROOT_KEY, "detail", detail["condition"])],
        vocab=TEMPLATES["vocab"],
    )
    await harness.graphs.save_version(first)
    upgraded = await harness.graphs.save_version(first.extended(notes="v2"))
    for _ in range(3):
        await harness.archive("a2_2013_detail", status=RawDocumentStatus.PENDING)
    await harness.ingestion.parse_pending(SOURCE)  # parsed by v2 ...
    for document in list(harness.documents.documents.values()):
        await harness.documents.mark_parsed(document.id, graph_version=1)  # ... pretend v1

    replay = await harness.ingestion.reparse_stale(
        SOURCE, graph_version=upgraded.version, limit=None
    )

    assert replay.documents == 3
    assert all(d.graph_version == upgraded.version for d in harness.documents.documents.values())  # type: ignore[attr-defined]


async def test_bounded_unrecognised_retries_rotate(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    await harness.archive("a2_2013_list")
    await harness.archive("a2_2013_detail")
    order: list[UUID] = []
    for _ in range(2):
        before = {d.id: d.parsed_at for d in harness.documents.documents.values()}  # type: ignore[attr-defined]
        await harness.ingestion.reparse_unrecognised(SOURCE, limit=1)
        order += [
            d.id  # type: ignore[attr-defined]
            for d in harness.documents.documents.values()
            if d.parsed_at != before[d.id]  # type: ignore[attr-defined]
        ]
    assert len(order) == 2 and order[0] != order[1]
