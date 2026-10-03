"""Reaching what enumeration missed, checking what routing rejected, sharing the archive.

* Links to URLs with no enumerated capture become exact-URL lookups.
* Exploration fetches a reproducible sample of rejected captures, and the audit
  says how many of them held adverts.
* Failed gaps can be retried; LLM decisions name their source.
* Request spacing is shared by every client of the archive.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from realestate.domain.archive import EXPLORE_NODE, Capture, DiscoveredLink, surt_key
from realestate.domain.enums import (
    CrawlStatus,
    GapStatus,
    LinkRel,
    LinkRequestStatus,
    RuleDomain,
)
from realestate.domain.exceptions import FetchError
from realestate.infrastructure.archive.rate_gate import LocalRateGate, RateGate
from realestate.infrastructure.archive.wayback import (
    ARCHIVE_GATE,
    WaybackCdxIndex,
    WaybackClient,
    WaybackSettings,
)
from tests.archive_fakes import ScriptedLlm
from tests.conftest import NullLogProvider
from tests.test_quality_pipeline import archive_bytes, audit_service, detail_html, seeder
from tests.test_rule_induction_and_crawl import SOURCE, Harness, captures, nav_answer

LIST_URL = "http://www.olx.com.eg/houses-apartments-for-sale-cat-367"
HIDDEN_URL = "http://cairo.olx.com.eg/flat-iid-123456789"


# -- linked URLs the enumeration missed ---------------------------------------------


async def test_unmatched_links_become_lookups_and_found_captures_get_fetched(
    tmp_path: Path,
) -> None:
    hidden = [Capture(surt_key(HIDDEN_URL), "20130310000000", HIDDEN_URL, "H1")]
    harness = Harness(tmp_path, ScriptedLlm(), captures(), hidden=hidden)
    await harness.crawler.enumerate(SOURCE)
    sink = harness.ingestion._links  # type: ignore[attr-defined]

    matched = await sink.offer(
        SOURCE,
        [
            DiscoveredLink(HIDDEN_URL, LinkRel.DETAIL),
            DiscoveredLink(LIST_URL, LinkRel.PAGINATION),  # enumerated: evidence only
            DiscoveredLink("http://elsewhere.example/x", LinkRel.DETAIL),
        ],
        parent_timestamp="20130305032637",
    )
    assert matched == 1
    pending = await harness.link_requests.pending(SOURCE, limit=10)
    assert [r.url for r in pending] == [HIDDEN_URL, "http://elsewhere.example/x"]
    assert pending[0].parent_timestamp == "20130305032637"
    # Offering the same links again records nothing new.
    await sink.offer(SOURCE, [DiscoveredLink(HIDDEN_URL, LinkRel.DETAIL)])
    assert len(harness.link_requests.rows) == 2

    report = await harness.crawler.resolve_links(SOURCE, limit=10)

    assert (report.looked_up, report.found, report.captures_added, report.not_archived) == (
        1,
        1,
        1,
        1,  # the off-domain link is never looked up
    )
    assert harness.index.lookups == [(HIDDEN_URL, "20130305032637")]
    added = await harness.frontier.captures_for(SOURCE, [surt_key(HIDDEN_URL)])
    assert added[0].evidence == {"linked_as": ["DETAIL"]}
    counts = await harness.link_requests.counts(SOURCE)
    assert (counts[LinkRequestStatus.FOUND], counts[LinkRequestStatus.NONE]) == (1, 1)

    # The evidence seed rule fetches it on the next routing pass.
    routed = await harness.crawler.route(SOURCE)
    assert routed.queued >= 1
    assert harness.frontier.rows[added[0].id].status is CrawlStatus.QUEUED


async def test_lookups_outside_the_window_find_nothing_and_failures_retry(
    tmp_path: Path,
) -> None:
    far = [Capture(surt_key(HIDDEN_URL), "20190101000000", HIDDEN_URL, "H2")]
    harness = Harness(tmp_path, ScriptedLlm(), captures(), hidden=far)
    sink = harness.ingestion._links  # type: ignore[attr-defined]
    await sink.offer(
        SOURCE, [DiscoveredLink(HIDDEN_URL, LinkRel.DETAIL)], parent_timestamp="20130305032637"
    )
    assert (await harness.crawler.resolve_links(SOURCE, limit=5)).not_archived == 1

    harness2 = Harness(tmp_path / "2", ScriptedLlm(), captures())
    await harness2.ingestion._links.offer(  # type: ignore[attr-defined]
        SOURCE, [DiscoveredLink(HIDDEN_URL, LinkRel.DETAIL)]
    )

    async def broken(*args: Any, **kwargs: Any) -> list[Capture]:
        raise FetchError("HTTP 503")

    harness2.index.lookup = broken  # type: ignore[method-assign]
    for _ in range(2):
        assert (await harness2.crawler.resolve_links(SOURCE, limit=5)).failed == 1
        assert (await harness2.link_requests.counts(SOURCE))[LinkRequestStatus.PENDING] == 1
    await harness2.crawler.resolve_links(SOURCE, limit=5)
    assert (await harness2.link_requests.counts(SOURCE))[LinkRequestStatus.FAILED] == 1


async def test_cdx_lookup_asks_for_the_exact_url_closest_to_the_parent() -> None:
    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        return httpx.Response(
            200,
            json=[
                ["urlkey", "timestamp", "original", "digest"],
                [surt_key(HIDDEN_URL), "20130311000000", HIDDEN_URL, "D"],
            ],
        )

    index = WaybackCdxIndex(
        WaybackClient(
            WaybackSettings(min_delay_seconds=0),
            log=NullLogProvider(),
            transport=httpx.MockTransport(handler),
        )
    )
    found = await index.lookup(HIDDEN_URL, around="20130305032637", window_days=30, limit=2)
    assert [c.timestamp for c in found] == ["20130311000000"]
    params = seen[0]
    assert params["matchType"] == "exact" and params["sort"] == "closest"
    assert (params["from"], params["to"]) == ("20130203", "20130404")
    assert params["closest"] == "20130305032637"


# -- exploration of rejected routing decisions ----------------------------------------


async def test_exploration_samples_rejected_captures_per_year_reproducibly(
    tmp_path: Path,
) -> None:
    rejected = [
        Capture(f"eg,com,olx)/x{i}", f"{year}0{i + 1}01000000", f"http://olx.com.eg/x{i}", f"D{i}")
        for year in (2012, 2013)
        for i in range(4)
    ]
    harness = Harness(tmp_path, ScriptedLlm(), [])
    await harness.frontier.add_captures(SOURCE, rejected)
    ids = list(harness.frontier.rows)
    await harness.frontier.set_route(ids[:3], status=CrawlStatus.SKIPPED)
    await harness.frontier.set_route(ids[3:6], status=CrawlStatus.DEFERRED)
    await harness.frontier.set_route(ids[6:], status=CrawlStatus.UNROUTED)

    report = await harness.crawler.explore(SOURCE, per_year=2, seed="s1")

    assert report.queued == {2012: 2, 2013: 2}
    queued = harness.frontier.by_status(CrawlStatus.QUEUED)
    assert {e.route_node for e in queued} == {EXPLORE_NODE} and {e.priority for e in queued} == {1}
    first = {e.id for e in queued}

    again = Harness(tmp_path / "again", ScriptedLlm(), [])
    await again.frontier.add_captures(SOURCE, rejected)
    await again.frontier.set_route(ids[:3], status=CrawlStatus.SKIPPED)
    await again.frontier.set_route(ids[3:6], status=CrawlStatus.DEFERRED)
    await again.frontier.set_route(ids[6:], status=CrawlStatus.UNROUTED)
    await again.crawler.explore(SOURCE, per_year=2, seed="s1")
    assert {e.id for e in again.frontier.by_status(CrawlStatus.QUEUED)} == first


async def test_audit_reports_exploration_hits_and_yield_by_quarter(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    await seeder(harness).apply(await seeder(harness).plan(SOURCE))
    document_id = await archive_bytes(
        harness, detail_html("55"), "http://cairo.olx.com.eg/a-iid-55", "20130501000000"
    )
    raw = next(d for d in harness.documents.documents.values() if str(d.id) == document_id)
    harness.documents.documents[raw.id] = replace(  # type: ignore[index]
        raw, meta={**raw.meta, "route_node": EXPLORE_NODE}
    )
    await archive_bytes(
        harness, detail_html("56"), "http://cairo.olx.com.eg/a-iid-56", "20131101000000"
    )

    report = await audit_service(harness).audit(SOURCE)

    assert report.exploration == {"documents": 1, "with_adverts": 1, "adverts": 1}
    assert report.yield_by_quarter == {
        "2013Q2": {"documents": 1, "with_adverts": 1, "new_ids": 1},
        "2013Q4": {"documents": 1, "with_adverts": 1, "new_ids": 1},
    }
    assert report.as_dict()["yield_by_quarter"]["2013Q2"]["new_ids_per_100_documents"] == 100.0
    assert any("exploration samples" in line for line in report.summary_lines())


# -- gaps and the ledger --------------------------------------------------------------


async def test_failed_gaps_reopen_and_decisions_name_their_source(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(by_tool={"submit_rules": [nav_answer]}), captures())
    await harness.crawler.enumerate(SOURCE)
    await harness.crawler.route(SOURCE)
    await harness.induction.collect_navigation_gaps(SOURCE)
    await harness.induction.induce(SOURCE, domain=RuleDomain.NAVIGATION, max_calls=1)
    assert {d.source_key for d in harness.decisions.decisions.values()} == {SOURCE}
    assert (await harness.decisions.totals(source_key=SOURCE))["calls"] == 1
    assert (await harness.decisions.totals(source_key="other"))["calls"] == 0

    gap = next(iter(harness.gaps.gaps.values()))
    for _ in range(3):
        await harness.gaps.record_failure(gap.id, "no", max_attempts=3)
    assert harness.gaps.gaps[gap.id].status is GapStatus.FAILED
    assert await harness.gaps.reopen_failed(SOURCE, RuleDomain.NAVIGATION) == 1
    reopened = harness.gaps.gaps[gap.id]
    assert (reopened.status, reopened.attempts) == (GapStatus.OPEN, 0)


# -- shared request spacing -----------------------------------------------------------


async def test_local_gate_spaces_reservations_and_holds_on_rate_limits() -> None:
    gate = LocalRateGate()
    waits = [await gate.reserve("a", interval=10) for _ in range(3)]
    assert waits[0] == 0 and 9 < waits[1] <= 10 and 19 < waits[2] <= 20
    assert await gate.reserve("b", interval=10) == 0  # gates are per name
    await gate.hold("b", seconds=100)
    assert 99 < await gate.reserve("b", interval=10) <= 100


class RecordingGate(RateGate):
    def __init__(self) -> None:
        self.reserved: list[tuple[str, float]] = []
        self.held: list[tuple[str, float]] = []

    async def reserve(self, name: str, *, interval: float) -> float:
        self.reserved.append((name, interval))
        return 0.0

    async def hold(self, name: str, *, seconds: float) -> None:
        self.held.append((name, seconds))


async def test_wayback_client_draws_slots_from_the_shared_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def instant(_: float) -> None:
        return None

    from realestate.infrastructure.archive import wayback

    monkeypatch.setattr(wayback.asyncio, "sleep", instant)
    responses = iter([httpx.Response(429), httpx.Response(200, text="ok")])
    gate = RecordingGate()
    client = WaybackClient(
        WaybackSettings(min_delay_seconds=7, rate_limit_cooldown_seconds=30),
        log=NullLogProvider(),
        transport=httpx.MockTransport(lambda request: next(responses)),
        gate=gate,
    )
    response = await client.get("https://web.archive.org/x")
    assert response.status_code == 200
    assert gate.reserved == [(ARCHIVE_GATE, 7), (ARCHIVE_GATE, 7)]
    assert gate.held == [(ARCHIVE_GATE, 30)]
    await client.aclose()


def test_the_gate_wait_is_honoured_not_just_reported() -> None:
    class SlowGate(RecordingGate):
        async def reserve(self, name: str, *, interval: float) -> float:
            return 0.05

    async def go() -> float:
        client = WaybackClient(
            WaybackSettings(min_delay_seconds=0),
            log=NullLogProvider(),
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
            gate=SlowGate(),
        )
        started = time.monotonic()
        await client.get("https://web.archive.org/x")
        await client.aclose()
        return time.monotonic() - started

    assert asyncio.run(go()) >= 0.05
