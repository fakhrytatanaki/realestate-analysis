"""Scheduled ingestion jobs.

Kept deliberately thin: a job binds a source key to a service call and swallows
nothing. The scheduler only needs a zero-argument coroutine, which
:func:`make_scrape_job` produces.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from realestate.application.services.ingestion_service import IngestionService
from realestate.domain.enums import RunTrigger
from realestate.domain.models import FetchContext
from realestate.domain.ports.log_provider import LogProvider


def make_scrape_job(
    service: IngestionService,
    source_key: str,
    *,
    log: LogProvider,
    max_items: int | None = None,
    params: dict[str, object] | None = None,
) -> Callable[[], Awaitable[None]]:
    """Build the coroutine the scheduler will call for one source."""

    async def run() -> None:
        try:
            await service.ingest(
                source_key,
                trigger=RunTrigger.SCHEDULED,
                ctx=FetchContext(max_items=max_items, params=dict(params or {})),
            )
        except Exception as exc:
            # The run record already carries the detail; this keeps a failing
            # job from killing the scheduler thread.
            await log.exception("scheduled scrape failed", exc, source_key=source_key)

    run.__name__ = f"scrape_{source_key}"
    return run
