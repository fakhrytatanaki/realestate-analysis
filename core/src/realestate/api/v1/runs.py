"""Ingestion run history."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from realestate.api.deps import RunRepositoryDep
from realestate.application.dto.common import Page
from realestate.application.dto.source import ScrapeRunRead
from realestate.domain.exceptions import NotFoundError

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=Page[ScrapeRunRead], summary="List ingestion runs")
async def list_runs(
    runs: RunRepositoryDep,
    source_key: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Page[ScrapeRunRead]:
    page = await runs.list(source_key=source_key, limit=limit, offset=offset)
    return Page[ScrapeRunRead].build(
        page, [ScrapeRunRead.from_domain(run) for run in page.items]
    )


@router.get("/{run_id}", response_model=ScrapeRunRead, summary="Fetch one ingestion run")
async def get_run(run_id: UUID, runs: RunRepositoryDep) -> ScrapeRunRead:
    run = await runs.get(run_id)
    if run is None:
        raise NotFoundError("ScrapeRun", run_id)
    return ScrapeRunRead.from_domain(run)
