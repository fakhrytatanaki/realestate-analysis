"""The two-stage ingestion pipeline, driven with in-memory collaborators."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from realestate.application.services.ingestion_service import IngestionService
from realestate.config.settings import Settings, SourceSettings
from realestate.domain.enums import (
    RawDocumentStatus,
    RunStatus,
    RunTrigger,
)
from realestate.domain.exceptions import (
    DataSourceNotImplementedError,
    ParseError,
    UnknownDataSourceError,
)
from realestate.domain.models import FetchContext, ListingDraft, RawDocumentKind, RawPayload
from realestate.domain.ports.data_source import DataSource
from realestate.infrastructure.blob.local_fs import LocalFsBlobProvider
from realestate.infrastructure.sources.registry import DataSourceRegistry
from tests.conftest import (
    InMemoryListingRepository,
    InMemoryRawDocumentRepository,
    InMemoryScrapeRunRepository,
    NullLogProvider,
    make_draft,
)


class StubSource(DataSource):
    """A source whose behaviour each test dictates."""

    key: ClassVar[str] = "stub"
    display_name: ClassVar[str] = "Stub"
    country_code: ClassVar[str] = "ZZ"

    def __init__(
        self,
        *,
        payloads: int = 2,
        drafts_per_payload: int = 2,
        fail_parse: bool = False,
        fail_fetch: bool = False,
    ) -> None:
        self.payloads = payloads
        self.drafts_per_payload = drafts_per_payload
        self.fail_parse = fail_parse
        self.fail_fetch = fail_fetch
        self.parse_calls = 0
        self.closed = False

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
        if self.fail_fetch:
            raise RuntimeError("site unreachable")
        for index in range(self.payloads):
            yield RawPayload(
                content=f'{{"page": {index}}}'.encode(),
                kind=RawDocumentKind.JSON,
                content_type="application/json",
                external_id=f"page-{index}",
                meta={"page": index},
            )

    async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]:
        self.parse_calls += 1
        if self.fail_parse:
            raise ParseError("selector broke")
        page = payload.meta.get("page", 0)
        return [
            make_draft(external_id=f"{page}-{n}") for n in range(self.drafts_per_payload)
        ]

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def harness(tmp_path: Path, null_log: NullLogProvider):  # type: ignore[no-untyped-def]
    """Wire the service to in-memory repositories and a real on-disk blob store."""

    def build(source: StubSource, *, implemented: bool = True):  # type: ignore[no-untyped-def]
        settings = Settings(sources={"stub": SourceSettings(enabled=True)})
        registry = DataSourceRegistry(settings, null_log)
        registry.register(
            key="stub",
            display_name="Stub",
            country_code="ZZ",
            factory=lambda ctx: source,
            implemented=implemented,
        )
        listings = InMemoryListingRepository()
        documents = InMemoryRawDocumentRepository()
        runs = InMemoryScrapeRunRepository()
        service = IngestionService(
            registry=registry,
            blob=LocalFsBlobProvider(tmp_path / "blob"),
            listings=listings,
            documents=documents,
            runs=runs,
            log=null_log,
        )
        return service, listings, documents, runs

    return build


async def test_successful_run_archives_then_parses(harness) -> None:  # type: ignore[no-untyped-def]
    source = StubSource(payloads=2, drafts_per_payload=3)
    service, listings, documents, _ = harness(source)

    run = await service.ingest("stub")

    assert run.status is RunStatus.SUCCESS
    assert run.documents_fetched == 2
    assert run.listings_created == 6
    assert run.errors == 0
    assert len(listings.listings) == 6
    assert all(
        document.status is RawDocumentStatus.PARSED for document in documents.documents.values()
    )


async def test_payloads_are_archived_to_the_blob_store(harness, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The raw bytes must survive independently of whether parsing worked."""
    service, _, documents, _ = harness(StubSource(payloads=2, fail_parse=True))

    await service.ingest("stub")

    blobs = list((tmp_path / "blob").rglob("*.json"))
    assert len(blobs) == 2
    assert all(
        document.status is RawDocumentStatus.FAILED for document in documents.documents.values()
    )


async def test_parse_failure_is_partial_not_fatal(harness) -> None:  # type: ignore[no-untyped-def]
    service, listings, documents, _ = harness(StubSource(payloads=2, fail_parse=True))

    run = await service.ingest("stub")

    assert run.status is RunStatus.PARTIAL
    assert run.errors == 2
    assert run.documents_fetched == 2
    assert listings.listings == {}
    failure = next(iter(documents.documents.values()))
    assert "selector broke" in failure.parse_error


async def test_fetch_failure_fails_the_run(harness) -> None:  # type: ignore[no-untyped-def]
    service, _, _, _ = harness(StubSource(fail_fetch=True))

    run = await service.ingest("stub")

    assert run.status is RunStatus.FAILED
    assert run.error_message is not None
    assert "site unreachable" in run.error_message


async def test_handled_fetch_failures_are_counted_once_per_pass(harness) -> None:  # type: ignore[no-untyped-def]
    class ReportingSource(StubSource):
        async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
            ctx.progress.attempts += 2
            ctx.progress.failures += 2
            async for payload in super().fetch(ctx):
                ctx.progress.attempts += 1
                yield payload

    service, _, documents, _ = harness(ReportingSource(payloads=1))
    ctx = FetchContext()
    for _ in range(2):
        run = await service.ingest("stub", ctx=ctx)
        assert run.status is RunStatus.PARTIAL
        assert run.errors == 2
        assert run.documents_fetched == 1
    assert ctx.progress.attempts == 6 and ctx.progress.failures == 4
    assert len(documents.documents) == 2


async def test_source_is_always_closed(harness) -> None:  # type: ignore[no-untyped-def]
    source = StubSource(fail_fetch=True)
    service, _, _, _ = harness(source)

    await service.ingest("stub")

    assert source.closed


async def test_reparse_replays_stored_blobs_without_refetching(harness) -> None:  # type: ignore[no-untyped-def]
    """The payoff of the two-stage split: fix a parser, replay history."""
    source = StubSource(payloads=2, drafts_per_payload=1)
    service, listings, _, _ = harness(source)
    await service.ingest("stub")
    parse_calls_after_ingest = source.parse_calls
    listings.listings.clear()

    result = await service.reparse("stub")

    assert source.parse_calls == parse_calls_after_ingest + 2
    assert result.created == 2
    assert len(listings.listings) == 2


async def test_parse_pending_drains_unparsed_documents(harness, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = StubSource(payloads=2, drafts_per_payload=1, fail_parse=True)
    service, listings, documents, _ = harness(source)
    await service.ingest("stub")
    # Pretend the earlier failures were a transient bug that is now fixed.
    for document_id in list(documents.documents):
        from dataclasses import replace

        documents.documents[document_id] = replace(
            documents.documents[document_id], status=RawDocumentStatus.PENDING
        )
    source.fail_parse = False

    result = await service.parse_pending("stub")

    assert result.created == 2
    assert len(listings.listings) == 2


async def test_unknown_source_is_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    service, _, _, _ = harness(StubSource())

    with pytest.raises(UnknownDataSourceError):
        await service.ingest("nope")


async def test_unimplemented_source_is_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    service, _, _, _ = harness(StubSource(), implemented=False)

    with pytest.raises(DataSourceNotImplementedError):
        await service.ingest("stub")


async def test_start_run_opens_a_running_record(harness) -> None:  # type: ignore[no-untyped-def]
    service, _, _, runs = harness(StubSource())

    run = await service.start_run("stub", trigger=RunTrigger.MANUAL)

    assert run.status is RunStatus.RUNNING
    assert run.id in runs.runs


async def test_ingest_reuses_a_prestarted_run(harness) -> None:  # type: ignore[no-untyped-def]
    """The HTTP trigger hands back an id first, then does the work."""
    service, _, _, runs = harness(StubSource(payloads=1, drafts_per_payload=1))
    started = await service.start_run("stub")

    finished = await service.ingest("stub", run=started)

    assert finished.id == started.id
    assert len(runs.runs) == 1
    assert finished.status is RunStatus.SUCCESS


class HangingSource(StubSource):
    """Yields one payload, then blocks as a slow archive request would."""

    def __init__(self, **kwargs: int) -> None:
        super().__init__(**kwargs)
        self.blocked = asyncio.Event()

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
        async for payload in super().fetch(ctx):
            yield payload
            break
        self.blocked.set()  # the first payload has been archived by now
        await asyncio.Event().wait()


async def test_interrupted_run_is_closed_as_failed(harness) -> None:  # type: ignore[no-untyped-def]
    source = HangingSource(payloads=2)
    service, _, documents, runs = harness(source)

    task = asyncio.create_task(service.ingest("stub"))
    await source.blocked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    (run,) = runs.runs.values()
    assert run.status is RunStatus.FAILED
    assert run.error_message == "interrupted before completion"
    assert run.documents_fetched == 0  # the archived payload stays PENDING for replay
    assert [d.status for d in documents.documents.values()] == [RawDocumentStatus.PENDING]
    assert source.closed
