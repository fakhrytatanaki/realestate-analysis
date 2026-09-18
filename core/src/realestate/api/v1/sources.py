"""Data source listing and manual ingestion triggers."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, status

from realestate.api.deps import (
    AdminDep,
    IngestionServiceDep,
    QueryServiceDep,
    RegistryDep,
    RunRepositoryDep,
)
from realestate.application.dto.source import ScrapeRunRead, SourceRead
from realestate.domain.enums import RunTrigger
from realestate.domain.models import FetchContext

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[SourceRead], summary="List registered data sources")
async def list_sources(
    registry: RegistryDep,
    runs: RunRepositoryDep,
    queries: QueryServiceDep,
) -> list[SourceRead]:
    """Every source this build knows about, implemented or not.

    Placeholders are included on purpose, with ``implemented: false``, so the
    roadmap is visible rather than hidden.
    """
    latest = await runs.latest_per_source()
    return [
        SourceRead.from_domain(
            descriptor,
            listing_count=await queries.count(descriptor.key),
            last_run=latest.get(descriptor.key),
        )
        for descriptor in registry.descriptors()
    ]


@router.get("/{source_key}", response_model=SourceRead, summary="Fetch one data source")
async def get_source(
    source_key: str,
    registry: RegistryDep,
    runs: RunRepositoryDep,
    queries: QueryServiceDep,
) -> SourceRead:
    descriptor = registry.descriptor(source_key)
    latest = await runs.latest_per_source()
    return SourceRead.from_domain(
        descriptor,
        listing_count=await queries.count(source_key),
        last_run=latest.get(source_key),
    )


@router.post(
    "/{source_key}/runs",
    response_model=ScrapeRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger an ingestion run",
)
async def trigger_run(
    source_key: str,
    background_tasks: BackgroundTasks,
    ingestion: IngestionServiceDep,
    _: AdminDep,
    max_items: int | None = None,
) -> ScrapeRunRead:
    """Start ingesting a source and return the run record immediately.

    The run is opened synchronously -- so an unknown or unimplemented source
    fails here with 404/501 rather than silently in the background -- and the
    work itself continues after the response is sent. Poll
    ``GET /runs/{id}`` for the outcome.
    """
    run = await ingestion.start_run(source_key, trigger=RunTrigger.MANUAL)
    background_tasks.add_task(
        ingestion.ingest,
        source_key,
        trigger=RunTrigger.MANUAL,
        ctx=FetchContext(max_items=max_items),
        run=run,
    )
    return ScrapeRunRead.from_domain(run)
