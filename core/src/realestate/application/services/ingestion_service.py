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
from collections.abc import Sequence
from datetime import datetime
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
    RawDocument,
    RawPayload,
    ScrapeRun,
    UpsertResult,
)
from realestate.domain.ports.archive import LinkSink
from realestate.domain.ports.blob_provider import BlobProvider
from realestate.domain.ports.data_source import DataSource
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.repositories import (
    ListingRepository,
    RawDocumentRepository,
    ScrapeRunRepository,
)
from realestate.domain.ports.source_registry import SourceRegistry


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
        error_message: str | None = None
        interrupted: asyncio.CancelledError | None = None

        try:
            documents, fetch_errors = await self._fetch_stage(
                source, source_key, run.id, ctx or FetchContext(), log
            )
            fetched = len(documents)
            errors += fetch_errors

            result, parse_errors = await self._parse_documents(source, documents, log)
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
            await source.aclose()

        finished = await self._runs.finish(
            run.id,
            status=status,
            documents_fetched=fetched,
            listings_created=result.created,
            listings_updated=result.updated,
            errors=errors,
            error_message=error_message,
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

    async def _parse_stored(
        self,
        source_key: str | None,
        *,
        statuses: Sequence[RawDocumentStatus],
        limit: int,
        fetched_after: datetime | None = None,
    ) -> UpsertResult:
        """Load documents in the given states and parse them, grouped by source."""
        documents: list[RawDocument] = []
        for status in statuses:
            documents.extend(
                await self._documents.list_by_status(
                    source_key=source_key,
                    status=status,
                    limit=limit,
                    fetched_after=fetched_after,
                )
            )

        by_source: dict[str, list[RawDocument]] = {}
        for document in documents:
            by_source.setdefault(document.source_key, []).append(document)

        total = UpsertResult()
        for key, group in by_source.items():
            log = self._log.bind(source_key=key)
            if not self._registry.has(key):
                await log.warning(
                    "skipping documents for an unregistered source", documents=len(group)
                )
                continue
            source = self._registry.create(key)
            try:
                result, _ = await self._parse_documents(source, group, log)
            finally:
                await source.aclose()
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

        Returns recorded documents plus handled collection/archive failures;
        one failed capture or bad payload must not abandon the rest of the run.
        """
        documents: list[RawDocument] = []
        errors = 0
        failures_before = ctx.progress.failures
        attempts_before = ctx.progress.attempts

        async for payload in source.fetch(ctx):
            try:
                documents.append(await self._archive(source_key, payload, run_id))
            except Exception as exc:
                errors += 1
                await log.exception("failed to archive payload", exc, url=payload.source_url)

        errors += ctx.progress.failures - failures_before
        await log.info(
            "fetch stage complete",
            documents=len(documents),
            errors=errors,
            fetch_attempts=ctx.progress.attempts - attempts_before,
            fetch_failures=ctx.progress.failures - failures_before,
        )
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
    ) -> tuple[UpsertResult, int]:
        """Parse archived documents into listings, marking each one's outcome."""
        total = UpsertResult()
        errors = 0
        unrecognised = 0

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
                result = await self._listings.upsert_many(
                    drafts, source_key=document.source_key, raw_document_id=document.id
                )
            except UnrecognisedDocumentError as exc:
                # Not a failure: rule induction will learn this page design.
                unrecognised += 1
                await self._documents.mark_unrecognised(document.id, str(exc))
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
                await self._documents.mark_failed(document.id, f"{type(exc).__name__}: {exc}")
                await log.exception("failed to parse document", exc, document_id=str(document.id))
                continue

            await self._documents.mark_parsed(document.id)
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
        )
        return total, errors

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
                matched = await self._links.offer(document.source_key, links)
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
