"""Batch routing, cron failures, and process exclusion without external I/O."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from realestate import ingest
from realestate.application.services.archive_crawl_service import (
    CrawlReport,
    CrawlRound,
    RouteReport,
)
from realestate.application.services.rule_induction_service import InductionReport
from realestate.bootstrap import Container
from realestate.config.settings import Settings, SourceSettings
from realestate.domain.enums import RunStatus, RunTrigger
from realestate.domain.models import ScrapeRun
from realestate.domain.ports.data_source import ArchiveDataSource, DataSource
from realestate.infrastructure.sources.registry import DataSourceRegistry
from tests.conftest import NullLogProvider


def make_run(key: str = "live", status: RunStatus = RunStatus.SUCCESS) -> ScrapeRun:
    return ScrapeRun(
        id=uuid4(),
        source_key=key,
        trigger=RunTrigger.MANUAL,
        status=status,
        started_at=datetime.now(UTC),
    )


@pytest.fixture
def container(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    settings = Settings(
        sources={
            "live": SourceSettings(enabled=True, max_items=7, params={"region": "Cairo"}),
            "archive": SourceSettings(enabled=True),
            "disabled": SourceSettings(enabled=False),
            "stub": SourceSettings(enabled=True),
        }
    )
    registry = DataSourceRegistry(settings, NullLogProvider())
    for key in ("live", "archive", "disabled", "stub"):
        source = MagicMock(spec=ArchiveDataSource if key == "archive" else DataSource)
        source.aclose = AsyncMock()
        registry.register(
            key=key,
            display_name=key,
            country_code="EG",
            factory=lambda ctx, source=source: source,
            implemented=key != "stub",
        )
    fake = MagicMock(spec=Container)
    fake.settings = settings
    fake.registry = registry
    fake.log = NullLogProvider()
    fake.init_db = AsyncMock()
    fake.aclose = AsyncMock()
    fake.ingestion.ingest = AsyncMock(return_value=make_run())
    fake.crawler.crawl = AsyncMock(return_value=CrawlReport(stopped="idle"))
    monkeypatch.setattr(ingest, "Container", lambda: fake)
    return fake


async def test_live_sources_use_configured_caps_and_scheduled_trigger(container: MagicMock) -> None:
    args = ingest.build_parser().parse_args(["--source", "live", "--scheduled"])

    assert await ingest.run(args) == 0

    call = container.ingestion.ingest.call_args
    assert call.args == ("live",)
    assert call.kwargs["trigger"] is RunTrigger.SCHEDULED
    assert call.kwargs["ctx"].max_items == 7
    assert call.kwargs["ctx"].params == {"region": "Cairo"}
    container.aclose.assert_awaited_once()


async def test_enabled_batch_routes_archives_and_overrides_live_cap(container: MagicMock) -> None:
    args = ingest.build_parser().parse_args(
        [
            "--all-enabled",
            "--max-items",
            "2",
            "--max-fetches",
            "4",
            "--max-llm-calls",
            "0",
            "--max-enumeration-pages",
            "1",
            "--max-link-lookups",
            "0",
            "--require-complete-enumeration",
        ]
    )

    assert await ingest.run(args) == 0

    container.crawler.crawl.assert_awaited_once_with(
        "archive",
        rounds=3,
        max_fetches=4,
        max_llm_calls=0,
        max_enumeration_pages=1,
        max_link_lookups=0,
        require_complete_enumeration=True,
    )
    assert container.ingestion.ingest.call_args.kwargs["ctx"].max_items == 2
    assert container.ingestion.ingest.await_count == 1
    container.registry.create("archive").aclose.assert_awaited_once()  # type: ignore[attr-defined]


async def test_dry_run_deduplicates_without_db_or_ingestion(
    container: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = ingest.build_parser().parse_args(
        [
            "--source",
            "live",
            "--source",
            "live",
            "--source",
            "archive",
            "--dry-run",
        ]
    )

    assert await ingest.run(args) == 0

    output = capsys.readouterr().out
    assert output.count("live: scrape") == 1
    assert "archive: crawl" in output
    container.init_db.assert_not_awaited()
    container.ingestion.ingest.assert_not_awaited()
    container.crawler.crawl.assert_not_awaited()
    container.aclose.assert_awaited_once()


@pytest.mark.parametrize("key", ["missing", "stub", "disabled"])
async def test_invalid_scheduled_batch_fails_before_any_work(
    container: MagicMock,
    key: str,
) -> None:
    args = ingest.build_parser().parse_args(
        [
            "--source",
            "live",
            "--source",
            key,
            "--scheduled",
        ]
    )

    assert await ingest.run(args) == 2
    container.init_db.assert_not_awaited()
    container.ingestion.ingest.assert_not_awaited()
    container.aclose.assert_awaited_once()


@pytest.mark.parametrize("options", [[], ["--all-enabled"]])
async def test_empty_enabled_selection_is_configuration_error(
    container: MagicMock,
    options: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    container.registry = DataSourceRegistry(Settings(sources={}), NullLogProvider())
    assert await ingest.run(ingest.build_parser().parse_args(options)) == 2
    assert "enable a source in etc/settings.toml or pass --source KEY" in capsys.readouterr().err
    container.init_db.assert_not_awaited()


@pytest.mark.parametrize("options", [[], ["--dry-run"], ["--scheduled", "--max-items", "2"]])
async def test_omitted_selection_runs_enabled_sources(
    container: MagicMock,
    options: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = ingest.build_parser().parse_args(options)

    assert await ingest.run(args) == 0

    output = capsys.readouterr().out
    assert "archive: crawl" in output
    assert "live: scrape" in output
    assert "disabled:" not in output
    assert "stub:" not in output
    if args.dry_run:
        container.init_db.assert_not_awaited()
        container.crawler.crawl.assert_not_awaited()
        container.ingestion.ingest.assert_not_awaited()
    else:
        container.crawler.crawl.assert_awaited_once()
        container.ingestion.ingest.assert_awaited_once()
    container.aclose.assert_awaited_once()


def test_main_with_no_arguments_runs_enabled_batch(
    container: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["ingest"])
    monkeypatch.setattr(ingest, "VAR_DIR", tmp_path)

    with pytest.raises(SystemExit) as caught:
        ingest.main()

    assert caught.value.code == 0
    container.crawler.crawl.assert_awaited_once()
    container.ingestion.ingest.assert_awaited_once()


def test_explicit_source_and_all_enabled_remain_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as caught:
        ingest.build_parser().parse_args(["--source", "live", "--all-enabled"])
    assert caught.value.code == 2


@pytest.mark.parametrize("failure", ["partial", "failed", "exception"])
async def test_failure_does_not_prevent_remaining_sources(
    container: MagicMock,
    failure: str,
) -> None:
    first = (
        RuntimeError("unreachable")
        if failure == "exception"
        else replace(make_run(status=RunStatus(failure.upper())), errors=1)
    )
    container.ingestion.ingest.side_effect = [first, make_run("disabled")]
    args = ingest.build_parser().parse_args(["--source", "live", "--source", "disabled"])

    assert await ingest.run(args) == 1
    assert [call.args[0] for call in container.ingestion.ingest.await_args_list] == [
        "live",
        "disabled",
    ]
    container.aclose.assert_awaited_once()


async def test_archive_partial_and_incomplete_enumeration_fail_batch(container: MagicMock) -> None:
    round_ = CrawlRound(
        number=1,
        routed=RouteReport(),
        navigation=InductionReport(),
        run=replace(make_run("archive", RunStatus.PARTIAL), errors=1),
        extraction=InductionReport(),
    )
    args = ingest.build_parser().parse_args(["--source", "archive"])
    container.crawler.crawl.return_value = CrawlReport(rounds=[round_])
    assert await ingest.run(args) == 1
    container.crawler.crawl.return_value = CrawlReport(incomplete_years=[2023])
    assert await ingest.run(args) == 1


def test_local_lock_excludes_another_batch_and_releases_after_error(tmp_path: Path) -> None:
    path = tmp_path / "lock" / "batch.lock"
    with pytest.raises(RuntimeError, match="interrupted"):
        with ingest.batch_lock(path):
            with pytest.raises(ingest.BatchAlreadyRunning):
                with ingest.batch_lock(path):
                    pytest.fail("overlapping batch entered")
            raise RuntimeError("interrupted")
    with ingest.batch_lock(path):
        assert path.exists()


def test_main_returns_temporary_failure_when_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "batch.lock"
    monkeypatch.setattr("sys.argv", ["ingest", "--source", "live", "--lock-file", str(path)])
    with ingest.batch_lock(path), pytest.raises(SystemExit) as caught:
        ingest.main()
    assert caught.value.code == 75


@pytest.mark.parametrize("option", ["--max-items", "--max-enumeration-pages"])
def test_zero_positive_budget_is_rejected(option: str) -> None:
    with pytest.raises(SystemExit) as caught:
        ingest.build_parser().parse_args(["--source", "live", option, "0"])
    assert caught.value.code == 2
