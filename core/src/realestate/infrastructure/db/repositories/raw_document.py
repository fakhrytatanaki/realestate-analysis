"""Tortoise-backed raw document repository."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from tortoise.expressions import Q

from realestate.domain.enums import RawDocumentStatus
from realestate.domain.models import BlobRef, RawDocument, RawPayload
from realestate.domain.ports.repositories import RawDocumentRepository
from realestate.infrastructure.db.mappers import to_raw_document
from realestate.infrastructure.db.models import RawDocumentModel


class TortoiseRawDocumentRepository(RawDocumentRepository):
    """Tracks archived payloads and their parse state."""

    async def create(
        self,
        *,
        source_key: str,
        payload: RawPayload,
        blob: BlobRef,
        scrape_run_id: UUID | None = None,
    ) -> RawDocument:
        row = await RawDocumentModel.create(
            id=uuid4(),
            source_key=source_key,
            external_id=payload.external_id,
            kind=payload.kind,
            status=RawDocumentStatus.PENDING,
            blob_key=blob.key,
            blob_uri=blob.uri,
            content_type=blob.content_type,
            size_bytes=blob.size_bytes,
            sha256=blob.sha256,
            source_url=payload.source_url,
            meta=payload.meta,
            scrape_run_id=scrape_run_id,
        )
        return to_raw_document(row)

    async def get(self, document_id: UUID) -> RawDocument | None:
        row = await RawDocumentModel.get_or_none(id=document_id)
        return to_raw_document(row) if row else None

    async def list_by_status(
        self,
        *,
        source_key: str | None = None,
        status: RawDocumentStatus = RawDocumentStatus.PENDING,
        limit: int = 100,
        fetched_after: datetime | None = None,
        graph_version_below: int | None = None,
    ) -> list[RawDocument]:
        queryset = RawDocumentModel.filter(status=status)
        if source_key is not None:
            queryset = queryset.filter(source_key=source_key)
        if fetched_after is not None:
            queryset = queryset.filter(fetched_at__gte=fetched_after)
        if graph_version_below is not None:
            queryset = queryset.filter(
                Q(graph_version__lt=graph_version_below) | Q(graph_version__isnull=True)
            )
        rows = await queryset.order_by("fetched_at").limit(limit)
        return [to_raw_document(row) for row in rows]

    async def mark_parsed(
        self,
        document_id: UUID,
        *,
        graph_version: int | None = None,
        report: Mapping[str, Any] | None = None,
    ) -> None:
        await RawDocumentModel.filter(id=document_id).update(
            status=RawDocumentStatus.PARSED,
            parsed_at=datetime.now(UTC),
            parse_error=None,
            graph_version=graph_version,
            parse_report=dict(report) if report is not None else None,
        )

    async def mark_failed(self, document_id: UUID, error: str) -> None:
        row = await RawDocumentModel.get_or_none(id=document_id)
        if row is None:
            return
        row.status = RawDocumentStatus.FAILED
        row.parse_error = error[:8000]
        row.attempts += 1
        await row.save()

    async def mark_unrecognised(
        self,
        document_id: UUID,
        reason: str,
        *,
        graph_version: int | None = None,
        report: Mapping[str, Any] | None = None,
    ) -> None:
        await RawDocumentModel.filter(id=document_id).update(
            status=RawDocumentStatus.UNRECOGNISED,
            parsed_at=datetime.now(UTC),
            parse_error=reason[:8000],
            graph_version=graph_version,
            parse_report=dict(report) if report is not None else None,
        )

    async def reset_status(
        self,
        source_key: str,
        *,
        from_statuses: Sequence[RawDocumentStatus],
        to_status: RawDocumentStatus = RawDocumentStatus.PENDING,
    ) -> int:
        return await RawDocumentModel.filter(
            source_key=source_key, status__in=list(from_statuses)
        ).update(status=to_status, parse_error=None)

    async def find_by_sha256(self, source_key: str, sha256: str) -> RawDocument | None:
        row = (
            await RawDocumentModel.filter(source_key=source_key, sha256=sha256)
            .order_by("-fetched_at")
            .first()
        )
        return to_raw_document(row) if row else None
