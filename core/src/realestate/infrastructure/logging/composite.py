"""Fan-out log sink."""

from __future__ import annotations

import asyncio
from typing import Any

from realestate.domain.enums import LogLevel
from realestate.domain.ports.log_provider import LogProvider


class CompositeLogProvider(LogProvider):
    """Writes every record to several sinks at once.

    Sinks are isolated from one another: if the file sink raises, the console
    sink still gets the record.
    """

    def __init__(self, providers: list[LogProvider]) -> None:
        self._providers = providers

    def bind(self, **context: Any) -> CompositeLogProvider:
        return CompositeLogProvider([p.bind(**context) for p in self._providers])

    async def log(self, level: LogLevel, message: str, **fields: Any) -> None:
        results = await asyncio.gather(
            *(p.log(level, message, **fields) for p in self._providers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result

    async def aclose(self) -> None:
        await asyncio.gather(
            *(p.aclose() for p in self._providers), return_exceptions=True
        )
