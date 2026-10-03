"""The ingestion pipeline.

Two stages, deliberately separate:

1. **fetch** -- ask the source for raw payloads, archive each one to the blob
   store, and record a ``PENDING`` :class:`RawDocument`. Nothing is interpreted.
2. **parse** -- read archived payloads back, turn them into drafts, and upsert.

Because stage 2 only ever reads from the blob store, a parser bug can be fixed
and replayed over months of stored payloads without touching the network again.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from realestate.domain.blob_keys import build_blob_key
from realestate.domain.enums import RawDocumentStatus, RunStatus, RunTrigger
from realestate.domain.exceptions import (
    DataSourceDisabledError,
    DataSourceNotImplementedError,
    UnknownDataSourceError,
    UnrecognisedDocumentError,
)
from realestate.domain.models import (
    FetchContext,
    ParseReport,
    RawDocument,
    RawPayload,
    ScrapeRun,
    UpsertResult,
)
from realestate.domain.ports.archive import LinkSink
from realestate.domain.ports.blob_provider import BlobProvider
from realestate.domain.ports.data_source import ArchiveDataSource, DataSource
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.repositories import (
    ListingRepository,
    RawDocumentRepository,
    ScrapeRunRepository,
)
from realestate.domain.ports.source_registry import SourceRegistry


@dataclass(slots=True)
class ParseStats:
    """Quality counters for one batch of parsed documents.

    ``PARSED`` only says a rule matched; these say what came of it, so a run
    that "succeeded" while recognising nothing useful is visible as such.
    """

    documents: int = 0
    with_listings: int = 0
    unrecognised: int = 0
    failed: int = 0
    page_kinds: Counter[str] = field(default_factory=Counter)
    hint_mismatch: int = 0
    items_total: int = 0
    items_valid: int = 0
    identity_problems: int = 0

    def add(self, report: ParseReport | None, drafts: int) -> None:
        self.documents += 1
        self.with_listings += 1 if drafts else 0
        if report is None:
            return
        self.page_kinds[report.page_kind or "NONE"] += 1
        self.hint_mismatch += 1 if report.hint_mismatch else 0
        self.items_total += report.items_total
        self.items_valid += report.items_valid
        self.identity_problems += report.identity_problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "with_listings": self.with_listings,
            "unrecognised": self.unrecognised,
            "parse_failed": self.failed,
            "page_kinds": dict(self.page_kinds),
            "hint_mismatch": self.hint_mismatch,
            "items_total": self.items_total,
            "items_valid": self.items_valid,
            "items_dropped": self.items_total - self.items_valid,
            "identity_problems": self.identity_problems,
        }


class IngestionService:
    """Orchestrates collection, archiving and normalisation for one source."""

    def __init__(
        self,
        *,
        registry: SourceRegistry,
        blob: BlobProvider,
        listings: ListingRepository,
        documents: RawDocumentRepository,
        runs: ScrapeRunRepository,
        log: LogProvider,
        links: LinkSink | None = None,
    ) -> None:
        self._registry = registry
        self._links = links
        self._blob = blob
        self._listings = listings
        self._documents = documents
        self._runs = runs
        self._log = log

    async def start_run(
        self, source_key: str, *, trigger: RunTrigger = RunTrigger.MANUAL
    ) -> ScrapeRun:
        """Validate the source and open a ``RUNNING`` record.

        Lets an HTTP caller be handed a run id immediately and have the work
        happen afterwards, while still failing fast on an unknown or
        unimplemented source.
        """
        self._ensure_runnable(source_key, trigger)
        return await self._runs.start(source_key, trigger)

    async def ingest(
        self,
        source_key: str,
        *,
        trigger: RunTrigger = RunTrigger.MANUAL,
        ctx: FetchContext | None = None,
        run: ScrapeRun | None = None,
    ) -> ScrapeRun:
        """Run both stages for one source and return the completed run record.

        ``run`` reuses a record already opened by :meth:`start_run` instead of
        starting a new one.
        """
        if run is None:
            run = await self.start_run(source_key, trigger=trigger)
        log = self._log.bind(source_key=source_key, run_id=str(run.id))
        await log.info("ingestion started", trigger=str(trigger))

        source = self._registry.create(source_key)
        fetched = 0
        errors = 0
        result = UpsertResult()
        stats = ParseStats()
        error_message: str | None = None
        interrupted: asyncio.CancelledError | None = None

        try:
            documents, fetch_errors = await self._fetch_stage(
                source, source_key, run.id, ctx or FetchContext(), log
            )
            fetched = len(documents)
            errors += fetch_errors

            result, parse_errors, stats = await self._parse_documents(source, documents, log)
            errors += parse_errors
        except asyncio.CancelledError as exc:
            # Ctrl-C / shutdown: close the run record honestly, then let the
            # cancellation continue. Archived payloads stay PENDING for replay.
            status = RunStatus.FAILED
            error_message = "interrupted before completion"
            interrupted = exc
            await log.warning("ingestion interrupted", documents_fetched=fetched)
        except Exception as exc:
            status = RunStatus.FAILED
            error_message = f"{type(exc).__name__}: {exc}"
            await log.exception("ingestion failed", exc)
        else:
            status = RunStatus.PARTIAL if errors else RunStatus.SUCCESS
        finally:
            fetch_stats = source.fetch_stats()
            await source.aclose()

        finished = await self._runs.finish(
            run.id,
            status=status,
            documents_fetched=fetched,
            listings_created=result.created,
            listings_updated=result.updated,
            errors=errors,
            error_message=error_message,
            stats={**stats.as_dict(), **fetch_stats},
        )
        await log.info(
            "ingestion finished",
            status=str(status),
            documents_fetched=fetched,
            created=result.created,
            updated=result.updated,
            unchanged=result.unchanged,
            errors=errors,
        )
        if interrupted is not None:
            raise interrupted
        return finished

    async def parse_pending(
        self,
        source_key: str | None = None,
        *,
        limit: int = 200,
    ) -> UpsertResult:
        """Parse archived payloads still waiting to be interpreted.

        Used by the standalone parse job, and as a retry path when a fetch run
        succeeded but parsing was interrupted.
        """
        return await self._parse_stored(
            source_key, statuses=(RawDocumentStatus.PENDING,), limit=limit
        )

    async def reparse(
        self,
        source_key: str,
        *,
        since: datetime | None = None,
        limit: int = 500,
        include_failed: bool = True,
    ) -> UpsertResult:
        """Re-run parsing over already-processed payloads.

        This is what the two-stage split buys: fix a mapping, replay history, no
        network involved.
        """
        statuses: tuple[RawDocumentStatus, ...] = (RawDocumentStatus.PARSED,)
        if include_failed:
            statuses += (RawDocumentStatus.FAILED,)
        return await self._parse_stored(
            source_key, statuses=statuses, limit=limit, fetched_after=since
        )

    async def reparse_unrecognised(self, source_key: str, *, limit: int = 500) -> UpsertResult:
        """Re-try documents no rule recognised, typically after rule induction."""
        return await self._parse_stored(
            source_key, statuses=(RawDocumentStatus.UNRECOGNISED,), limit=limit
        )

    async def reparse_stale(
        self, source_key: str, *, graph_version: int, limit: int = 10_000
    ) -> UpsertResult:
        """Re-parse documents parsed by an extraction graph older than ``graph_version``.

        A rule upgrade is not a data migration until this has run: documents
        already ``PARSED`` otherwise keep their old interpretation forever.
        """
        return await self._parse_stored(
            source_key,
            statuses=(RawDocumentStatus.PARSED, RawDocumentStatus.UNRECOGNISED),
            limit=limit,
            graph_version_below=graph_version,
        )

    async def rebuild(self, source_key: str) -> UpsertResult:
        """Delete a source's listings and re-parse every archived payload, in capture order.

        For archive sources, whose payloads are the source of truth: repairs
        rows that ordinary replays cannot (merged identities, false histories),
        because a replay only adds and updates. Safe to re-run if interrupted --
        documents left ``PENDING`` are parsed by the next run or crawl.
        """
        source = self._registry.create(source_key)
        try:
            if not isinstance(source, ArchiveDataSource):
                raise ValueError(f"source '{source_key}' is not an archive source")
        finally:
            await source.aclose()
        log = self._log.bind(source_key=source_key)
        deleted = await self._listings.delete_source(source_key)
        reset = await self._documents.reset_status(
            source_key,
            from_statuses=(
                RawDocumentStatus.PARSED,
                RawDocumentStatus.UNRECOGNISED,
                RawDocumentStatus.FAILED,
            ),
        )
        await log.info("rebuild: listings deleted, documents reset", deleted=deleted, reset=reset)
        return await self.parse_pending(source_key, limit=1_000_000)

    async def _parse_stored(
        self,
        source_key: str | None,
        *,
        statuses: Sequence[RawDocumentStatus],
        limit: int,
        fetched_after: datetime | None = None,
        graph_version_below: int | None = None,
    ) -> UpsertResult:
        """Load documents in the given states and parse them, grouped by source.

        Each source's documents are parsed in capture order (fetch order for
        live sources), so merged listing state never depends on which
        documents happened to be stored first.
        """
        documents: list[RawDocument] = []
        for status in statuses:
            documents.extend(
                await self._documents.list_by_status(
                    source_key=source_key,
                    status=status,
                    limit=limit,
                    fetched_after=fetched_after,
                    graph_version_below=graph_version_below,
                )
            )

        by_source: dict[str, list[RawDocument]] = {}
        for document in sorted(documents, key=_capture_order):
            by_source.setdefault(document.source_key, []).append(document)

        total = UpsertResult()
        for key, group in by_source.items():
            log = self._log.bind(source_key=key)
            if not self._registry.has(key):
                await log.warning(
                    "skipping documents for an unregistered source", documents=len(group)
                )
                continue
            # Replays are recorded as runs too, so their contributions are
            # accounted for alongside fetching runs.
            run = await self._runs.start(key, RunTrigger.REPLAY)
            source = self._registry.create(key)
            errors = 0
            stats = ParseStats()
            result = UpsertResult()
            try:
                result, errors, stats = await self._parse_documents(source, group, log)
            finally:
                await source.aclose()
                await self._runs.finish(
                    run.id,
                    status=RunStatus.PARTIAL if errors else RunStatus.SUCCESS,
                    listings_created=result.created,
                    listings_updated=result.updated,
                    errors=errors,
                    stats=stats.as_dict(),
                )
            total = total + result
        return total

    async def _fetch_stage(
        self,
        source: DataSource,
        source_key: str,
        run_id: UUID,
        ctx: FetchContext,
        log: LogProvider,
    ) -> tuple[list[RawDocument], int]:
        """Collect payloads and archive each one.

        Returns the recorded documents plus a count of payloads that could not
        be archived; one bad payload must not abandon the rest of the run.
        """
        documents: list[RawDocument] = []
        errors = 0

        async for payload in source.fetch(ctx):
            try:
                documents.append(await self._archive(source_key, payload, run_id))
            except Exception as exc:
                errors += 1
                await log.exception("failed to archive payload", exc, url=payload.source_url)
                await source.acknowledge(payload, error=f"archive failed: {exc}")
                continue
            await source.acknowledge(payload)

        await log.info("fetch stage complete", documents=len(documents), errors=errors)
        return documents, errors

    async def _archive(
        self, source_key: str, payload: RawPayload, run_id: UUID | None
    ) -> RawDocument:
        """Write one payload to the blob store and record it as PENDING."""
        key = build_blob_key(source_key, payload.kind)
        blob = await self._blob.put(key, payload.content, content_type=payload.content_type)
        return await self._documents.create(
            source_key=source_key, payload=payload, blob=blob, scrape_run_id=run_id
        )

    async def _parse_documents(
        self,
        source: DataSource,
        documents: Sequence[RawDocument],
        log: LogProvider,
    ) -> tuple[UpsertResult, int, ParseStats]:
        """Parse archived documents into listings, marking each one's outcome."""
        total = UpsertResult()
        errors = 0
        unrecognised = 0
        stats = ParseStats()

        for document in documents:
            try:
                content = await self._blob.get(document.blob_key)
                payload = RawPayload(
                    content=content,
                    kind=document.kind,
                    content_type=document.content_type,
                    source_url=document.source_url,
                    external_id=document.external_id,
                    meta=document.meta,
                )
                drafts = await source.parse(payload)
                report = await _parse_report(source, payload)
                result = await self._listings.upsert_many(
                    drafts, source_key=document.source_key, raw_document_id=document.id
                )
            except UnrecognisedDocumentError as exc:
                # Not a failure: rule induction will learn this page design.
                unrecognised += 1
                stats.unrecognised += 1
                missed = await _parse_report(source, payload)
                await self._documents.mark_unrecognised(
                    document.id,
                    str(exc),
                    graph_version=missed.graph_version if missed else None,
                    report=missed.as_dict() if missed else None,
                )
                await log.debug(
                    "document unrecognised",
                    document_id=str(document.id),
                    url=document.source_url,
                    reason=str(exc),
                )
                continue
            except Exception as exc:
                # The payload is safe in the blob store, so a failure here is
                # recoverable: fix the parser and replay.
                errors += 1
                stats.failed += 1
                await self._documents.mark_failed(document.id, f"{type(exc).__name__}: {exc}")
                await log.exception("failed to parse document", exc, document_id=str(document.id))
                continue

            await self._documents.mark_parsed(
                document.id,
                graph_version=report.graph_version if report else None,
                report=report.as_dict() if report else None,
            )
            stats.add(report, len(drafts))
            total = total + result
            if self._links is not None:
                await self._offer_links(source, payload, document, log)
            await log.debug(
                "parsed document",
                document_id=str(document.id),
                url=document.source_url,
                drafts=len(drafts),
                created=result.created,
                updated=result.updated,
                unchanged=result.unchanged,
            )

        await log.info(
            "parse stage complete",
            documents=len(documents),
            unrecognised=unrecognised,
            created=total.created,
            updated=total.updated,
            unchanged=total.unchanged,
            errors=errors,
            hint_mismatch=stats.hint_mismatch,
            items_dropped=stats.items_total - stats.items_valid,
        )
        return total, errors, stats

    async def _offer_links(
        self,
        source: DataSource,
        payload: RawPayload,
        document: RawDocument,
        log: LogProvider,
    ) -> None:
        """Hand links found on a parsed page to the crawl frontier.

        Discovery reads the same archived bytes as parsing, so it is as
        replay-safe; a failure here never un-parses the document.
        """
        assert self._links is not None
        try:
            links = await source.discover_links(payload)
            if links:
                timestamp = document.meta.get("timestamp") if document.meta else None
                matched = await self._links.offer(
                    document.source_key,
                    links,
                    parent_timestamp=str(timestamp) if timestamp else None,
                )
                await log.debug(
                    "links offered to the frontier",
                    document_id=str(document.id),
                    links=len(links),
                    matched_urls=matched,
                )
        except Exception as exc:
            await log.exception("link discovery failed", exc, document_id=str(document.id))

    def _ensure_runnable(self, source_key: str, trigger: RunTrigger) -> None:
        """Guard against running something that cannot work.

        ``enabled`` governs *scheduling* only, so a manual run of a
        configured-off source is allowed -- that is how a new adapter gets tried
        out. A stub with no implementation is refused whatever the trigger.
        """
        if not self._registry.has(source_key):
            raise UnknownDataSourceError(source_key)
        descriptor = self._registry.descriptor(source_key)
        if not descriptor.implemented:
            raise DataSourceNotImplementedError(source_key)
        if trigger is RunTrigger.SCHEDULED and not descriptor.enabled:
            raise DataSourceDisabledError(source_key)


async def _parse_report(source: DataSource, payload: RawPayload) -> ParseReport | None:
    return await source.parse_report(payload) if isinstance(source, ArchiveDataSource) else None


def _capture_order(document: RawDocument) -> tuple[datetime, str]:
    return (document.captured_at or document.fetched_at, str(document.id))
