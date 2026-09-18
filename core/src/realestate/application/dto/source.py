"""Data source and ingestion run DTOs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from realestate.domain.enums import RawDocumentKind, RawDocumentStatus, RunStatus, RunTrigger
from realestate.domain.models import RawDocument, ScrapeRun, SourceDescriptor


class ScrapeRunRead(BaseModel):
    """One execution of the ingestion pipeline."""

    id: UUID
    source_key: str
    trigger: RunTrigger
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    documents_fetched: int
    listings_created: int
    listings_updated: int
    errors: int
    error_message: str | None = None

    @classmethod
    def from_domain(cls, run: ScrapeRun) -> ScrapeRunRead:
        return cls(
            id=run.id,
            source_key=run.source_key,
            trigger=run.trigger,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            documents_fetched=run.documents_fetched,
            listings_created=run.listings_created,
            listings_updated=run.listings_updated,
            errors=run.errors,
            error_message=run.error_message,
        )


class SourceRead(BaseModel):
    """A registered data source and the state of its most recent run."""

    key: str
    display_name: str
    country_code: str
    enabled: bool = Field(description="Implemented and switched on in configuration")
    implemented: bool = Field(description="False for registered placeholders")
    interval_minutes: float | None = None
    crontab: str | None = None
    listing_count: int = 0
    last_run: ScrapeRunRead | None = None

    @classmethod
    def from_domain(
        cls,
        descriptor: SourceDescriptor,
        *,
        listing_count: int = 0,
        last_run: ScrapeRun | None = None,
    ) -> SourceRead:
        return cls(
            key=descriptor.key,
            display_name=descriptor.display_name,
            country_code=descriptor.country_code,
            enabled=descriptor.enabled,
            implemented=descriptor.implemented,
            interval_minutes=descriptor.interval_minutes,
            crontab=descriptor.crontab,
            listing_count=listing_count,
            last_run=ScrapeRunRead.from_domain(last_run) if last_run else None,
        )


class RawDocumentRead(BaseModel):
    """Metadata for an archived payload. The bytes stay in the blob store."""

    id: UUID
    source_key: str
    external_id: str | None = None
    kind: RawDocumentKind
    status: RawDocumentStatus
    blob_key: str
    blob_uri: str
    content_type: str
    size_bytes: int
    sha256: str
    source_url: str | None = None
    fetched_at: datetime
    parsed_at: datetime | None = None
    parse_error: str | None = None
    attempts: int
    scrape_run_id: UUID | None = None

    @classmethod
    def from_domain(cls, document: RawDocument) -> RawDocumentRead:
        return cls(
            id=document.id,
            source_key=document.source_key,
            external_id=document.external_id,
            kind=document.kind,
            status=document.status,
            blob_key=document.blob_key,
            blob_uri=document.blob_uri,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            source_url=document.source_url,
            fetched_at=document.fetched_at,
            parsed_at=document.parsed_at,
            parse_error=document.parse_error,
            attempts=document.attempts,
            scrape_run_id=document.scrape_run_id,
        )
