"""Tortoise-backed ingestion run repository."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from realestate.domain.enums import RunStatus, RunTrigger
from realestate.domain.exceptions import NotFoundError
from realestate.domain.models import Page, ScrapeRun
from realestate.domain.ports.repositories import ScrapeRunRepository
from realestate.infrastructure.db.mappers import to_scrape_run
from realestate.infrastructure.db.models import ScrapeRunModel


class TortoiseScrapeRunRepository(ScrapeRunRepository):
    """Ingestion run history."""

    async def start(self, source_key: str, trigger: RunTrigger) -> ScrapeRun:
        row = await ScrapeRunModel.create(
            id=uuid4(),
            source_key=source_key,
            trigger=trigger,
            status=RunStatus.RUNNING,
        )
        return to_scrape_run(row)

    async def finish(
        self,
        run_id: UUID,
        *,
        status: RunStatus,
        documents_fetched: int = 0,
        listings_created: int = 0,
        listings_updated: int = 0,
        errors: int = 0,
        error_message: str | None = None,
    ) -> ScrapeRun:
        row = await ScrapeRunModel.get_or_none(id=run_id)
        if row is None:
            raise NotFoundError("ScrapeRun", run_id)
        row.status = status
        row.finished_at = datetime.now(UTC)
        row.documents_fetched = documents_fetched
        row.listings_created = listings_created
        row.listings_updated = listings_updated
        row.errors = errors
        # The column is nullable; the field descriptor is typed non-optional.
        row.error_message = error_message[:8000] if error_message else None  # type: ignore[assignment]
        await row.save()
        return to_scrape_run(row)

    async def get(self, run_id: UUID) -> ScrapeRun | None:
        row = await ScrapeRunModel.get_or_none(id=run_id)
        return to_scrape_run(row) if row else None

    async def list(
        self,
        *,
        source_key: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Page[ScrapeRun]:
        queryset = ScrapeRunModel.all()
        if source_key is not None:
            queryset = queryset.filter(source_key=source_key)
        total = await queryset.count()
        rows = await queryset.order_by("-started_at").offset(offset).limit(limit)
        return Page(
            items=[to_scrape_run(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def latest_per_source(self) -> dict[str, ScrapeRun]:
        # Source counts are tiny (one row per portal), so ordering once and
        # keeping the first hit per key is cheaper than a window function.
        latest: dict[str, ScrapeRun] = {}
        for row in await ScrapeRunModel.all().order_by("-started_at"):
            latest.setdefault(row.source_key, to_scrape_run(row))
        return latest
