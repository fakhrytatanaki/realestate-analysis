"""Bounded archive collection and enumeration gates, without external services."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from realestate import cli
from realestate.application.services.archive_crawl_service import CrawlReport
from realestate.bootstrap import Container
from realestate.config.settings import ArchiveSettings, Settings, SourceSettings
from realestate.domain.archive import ArchiveScope, Capture, capture_quarter, surt_key
from realestate.domain.enums import CrawlStatus, RunStatus
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.models import FetchContext
from realestate.infrastructure.archive.wayback import WaybackClient, WaybackSettings
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.sources.dubizzle_eg_wayback.source import DubizzleEgWaybackDataSource
from tests.archive_fakes import InMemoryFrontier, InMemoryGraphs, ScriptedLlm
from tests.conftest import NullLogProvider
from tests.test_rule_induction_and_crawl import SOURCE, Harness, captures


async def test_failed_captures_spend_budget_across_rounds_and_leave_rest_queued(
    tmp_path: Path,
) -> None:
    rows = [
        Capture(surt_key(url), "20130101000000", url, f"digest-{i}")
        for i in range(55)
        for url in [f"http://www.olx.com.eg/not-found/{i}/"]
    ]
    harness = Harness(tmp_path, ScriptedLlm(), rows)
    await harness.crawler.enumerate(SOURCE)
    await harness.frontier.set_route(list(harness.frontier.rows), status=CrawlStatus.QUEUED)

    report = await harness.crawler.crawl(
        SOURCE, rounds=0, max_fetches=50, fetches_per_round=17, max_llm_calls=0
    )

    assert report.stopped == "fetch budget spent"
    assert [r.fetch_attempts for r in report.rounds] == [17, 17, 16]
    assert sum(r.fetch_failures for r in report.rounds) == 50
    assert len(harness.served) == 50
    assert report.llm_calls == 0 and harness.llm.calls == []
    assert harness.documents.documents == {} and harness.listings.listings == {}
    assert len(harness.frontier.by_status(CrawlStatus.FAILED)) == 50
    assert len(harness.frontier.by_status(CrawlStatus.QUEUED)) == 5
    for round_ in report.rounds:
        assert round_.run is not None
        assert round_.run.status is RunStatus.PARTIAL
        assert round_.run.errors == round_.fetch_failures
        assert round_.run.documents_fetched == 0

    resumed = await harness.crawler.crawl(SOURCE, rounds=0, max_fetches=2, max_llm_calls=0)
    assert sum(r.fetch_attempts for r in resumed.rounds) == 2
    assert len(harness.served) == 52  # failed rows were not automatically retried
    assert len(harness.frontier.by_status(CrawlStatus.QUEUED)) == 3


async def test_mixed_capture_results_count_failures_separately_from_payloads(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    await harness.crawler.enumerate(SOURCE)
    await harness.frontier.set_route(list(harness.frontier.rows), status=CrawlStatus.QUEUED)
    report = await harness.crawler.crawl(
        SOURCE, rounds=0, max_fetches=5, fetches_per_round=2, max_llm_calls=0
    )

    assert report.stopped == "fetch budget spent"
    assert len(harness.served) == sum(r.fetch_attempts for r in report.rounds) == 5
    failed = sum("vehicles" in url for url in harness.served)
    assert failed >= 1
    assert sum(r.fetch_failures for r in report.rounds) == failed
    assert sum(r.run.documents_fetched for r in report.rounds if r.run) == 5 - failed
    assert sum(r.run.errors for r in report.rounds if r.run) == failed
    assert len(harness.frontier.by_status(CrawlStatus.QUEUED)) == 1


@pytest.mark.parametrize("budget", [0, 1, 26])
async def test_dubizzle_attempt_limit_counts_failures_across_frontier_batches(budget: int) -> None:
    frontier, log = InMemoryFrontier(), NullLogProvider()
    rows = [
        Capture(surt_key(url), "20230101000000", url, f"digest-{i}")
        for i in range(30)
        for url in [f"https://www.dubizzle.com.eg/properties/?page={i}"]
    ]
    await frontier.add_captures(DubizzleEgWaybackDataSource.key, rows)
    await frontier.set_route(list(frontier.rows), status=CrawlStatus.QUEUED)
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        return httpx.Response(404)

    source = DubizzleEgWaybackDataSource(
        log=log,
        frontier=frontier,
        graphs=InMemoryGraphs(),
        engine=HtmlRuleEngine(),
        client=WaybackClient(
            WaybackSettings(min_delay_seconds=0, max_retries=1),
            log=log,
            transport=httpx.MockTransport(handler),
        ),
    )
    ctx = FetchContext(max_items=budget, page_limit=30)
    try:
        assert [payload async for payload in source.fetch(ctx)] == []
        assert len(visits) == ctx.progress.attempts == ctx.progress.failures == budget
        assert len(frontier.by_status(CrawlStatus.QUEUED)) == 30 - budget
    finally:
        await source.aclose()


@pytest.mark.parametrize("already_partial", [False, True])
async def test_enumeration_gate_holds_partial_scope_until_explicit_resume(
    tmp_path: Path, already_partial: bool
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    if already_partial:
        await harness.crawler.enumerate(SOURCE, max_pages=1)
    report = await harness.crawler.crawl(
        SOURCE,
        require_complete_enumeration=True,
        max_enumeration_pages=1,
        max_fetches=50,
        max_llm_calls=10,
    )
    assert report.stopped == "enumeration incomplete"
    assert report.incomplete_years == [2013]
    assert report.rounds == [] and report.llm_calls == 0
    assert harness.served == [] and harness.llm.calls == []
    assert harness.graphs.saved == []
    assert harness.documents.documents == {} and harness.listings.listings == {}
    # Main resumes incomplete enumeration even when a partial frontier exists.
    assert len(harness.index.requests) == 1 + int(already_partial)
    assert all(e.status is CrawlStatus.DISCOVERED for e in harness.frontier.rows.values())

    await harness.crawler.enumerate(SOURCE)
    assert await harness.crawler.incomplete_years(SOURCE) == []
    await harness.frontier.set_route(list(harness.frontier.rows), status=CrawlStatus.QUEUED)
    resumed = await harness.crawler.crawl(
        SOURCE, require_complete_enumeration=True, max_fetches=1, max_llm_calls=0
    )
    assert resumed.incomplete_years == []
    assert len(harness.served) == 1


async def test_enumeration_gate_checks_every_configured_year_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), [])
    monkeypatch.setattr(harness.crawler, "scope", lambda _: ArchiveScope("olx.com.eg", 2013, 2015))
    await harness.cursors.save(SOURCE, "cdx:2013", resume_key=None, done=True)
    await harness.cursors.save(SOURCE, "cdx:2014", resume_key="next", done=False)
    await harness.cursors.save("other-source", "cdx:2015", resume_key=None, done=True)
    assert await harness.crawler.incomplete_years(SOURCE) == [2014, 2015]
    report = await harness.crawler.crawl(
        SOURCE, enumerate_missing=False, require_complete_enumeration=True
    )
    assert report.incomplete_years == [2014, 2015]
    assert harness.index.requests == [] and harness.served == []


async def test_zero_crawl_budget_never_fetches(tmp_path: Path) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    report = await harness.crawler.crawl(SOURCE, max_fetches=0, max_llm_calls=0)
    assert report.stopped == "fetch budget spent" and report.rounds == []
    assert harness.served == []


@pytest.mark.parametrize(
    "options",
    [
        {"max_fetches": -1},
        {"max_llm_calls": -1},
        {"rounds": -1},
        {"fetches_per_round": 0},
        {"max_enumeration_pages": 0},
    ],
)
async def test_invalid_crawl_limits_fail_before_any_io(
    tmp_path: Path, options: dict[str, int]
) -> None:
    harness = Harness(tmp_path, ScriptedLlm(), captures())
    with pytest.raises(ConfigurationError):
        await harness.crawler.crawl(SOURCE, **options)
    assert harness.index.requests == [] and harness.served == []


def test_pilot_cli_accepts_enumeration_completion_gate() -> None:
    args = cli.build_parser().parse_args(
        [
            "crawl",
            "--source",
            "dubizzle_eg_wayback",
            "--max-fetches",
            "50",
            "--max-llm-calls",
            "0",
            "--require-complete-enumeration",
        ]
    )
    assert args.require_complete_enumeration and args.max_fetches == 50 and args.max_llm_calls == 0


def test_model_budget_setting_falls_back_from_source_to_archive() -> None:
    assert Settings(sources={}).max_llm_calls("olx_eg_wayback") == 10
    settings = Settings(
        archive=ArchiveSettings(max_llm_calls=4),
        sources={
            "dubizzle_eg_wayback": SourceSettings(max_llm_calls=0),
            "olx_eg_wayback": SourceSettings(enabled=True),
        },
    )
    assert settings.max_llm_calls("dubizzle_eg_wayback") == 0
    assert settings.max_llm_calls("olx_eg_wayback") == 4
    assert settings.max_llm_calls("unconfigured") == 4
    with pytest.raises(ValidationError):
        SourceSettings(max_llm_calls=-1)
    with pytest.raises(ValidationError):
        ArchiveSettings(max_llm_calls=-1)


async def test_crawl_cli_takes_the_model_budget_from_settings_unless_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = Container(
        Settings(
            archive=ArchiveSettings(max_llm_calls=4),
            sources={"dubizzle_eg_wayback": SourceSettings(max_llm_calls=0)},
        )
    )
    crawl = AsyncMock(return_value=CrawlReport(stopped="idle"))
    container.__dict__.update(log=NullLogProvider(), crawler=Mock(crawl=crawl))
    monkeypatch.setattr(cli, "Container", lambda _: container)
    monkeypatch.setattr(cli, "_print_status", AsyncMock())
    monkeypatch.setattr(container, "init_db", AsyncMock())
    monkeypatch.setattr(container, "aclose", AsyncMock())
    cases = [
        ("dubizzle_eg_wayback", [], 0),
        ("olx_eg_wayback", [], 4),
        ("dubizzle_eg_wayback", ["--max-llm-calls", "2"], 2),
    ]
    for source, flag, expected in cases:
        args = cli.build_parser().parse_args(["crawl", "--source", source, *flag])
        assert await cli.run(args) == 0
        assert crawl.call_args.kwargs["max_llm_calls"] == expected


def test_fetch_progress_is_isolated_between_contexts() -> None:
    first, second = FetchContext(), FetchContext()
    first.progress.attempts = 1
    first.progress.failures = 1
    assert second.progress.attempts == second.progress.failures == 0


async def test_successive_crawls_move_on_to_quarters_earlier_crawls_did_not_reach(
    tmp_path: Path,
) -> None:
    # 32 quarters, three captures each, and crawls of 10: every crawl used to
    # take the same ten oldest quarters again.
    rows = [
        Capture(surt_key(url), f"{year}{month:02d}15000000", url, f"digest-{year}{month}-{n}")
        for year in range(2005, 2013)
        for month in (1, 4, 7, 10)
        for n in range(3)
        for url in [f"http://www.olx.com.eg/houses-apartments-for-sale-cat-367-p-{year}{month}{n}"]
    ]
    harness = Harness(tmp_path, ScriptedLlm(), rows, years=(2005, 2012))
    await harness.crawler.enumerate(SOURCE)
    await harness.frontier.set_route(list(harness.frontier.rows), status=CrawlStatus.QUEUED)

    fetched: Counter[str] = Counter()
    for _ in range(3):
        await harness.crawler.crawl(
            SOURCE, rounds=1, max_fetches=10, max_llm_calls=0, enumerate_missing=False
        )
        fetched = Counter(
            capture_quarter(entry.timestamp)
            for entry in harness.frontier.by_status(CrawlStatus.FETCHED)
        )
    assert sum(fetched.values()) == 30
    assert len(fetched) == 30 and set(fetched.values()) == {1}
