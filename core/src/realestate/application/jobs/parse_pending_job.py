"""Scheduled catch-up parsing.

A safety net for payloads that were archived but never parsed -- for instance
when the process was stopped between the two stages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from realestate.application.services.ingestion_service import IngestionService
from realestate.domain.ports.log_provider import LogProvider


def make_parse_pending_job(
    service: IngestionService,
    *,
    log: LogProvider,
    limit: int = 200,
) -> Callable[[], Awaitable[None]]:
    """Build the coroutine that drains pending raw documents."""

    async def run() -> None:
        try:
            result = await service.parse_pending(limit=limit)
            if result.created or result.updated:
                await log.info(
                    "pending parse catch-up",
                    created=result.created,
                    updated=result.updated,
                    unchanged=result.unchanged,
                )
        except Exception as exc:
            await log.exception("pending parse job failed", exc)

    run.__name__ = "parse_pending"
    return run
