"""Port for structured, contextual logging."""

from __future__ import annotations

import traceback
from abc import ABC, abstractmethod
from typing import Any

from realestate.domain.enums import LogLevel


class LogProvider(ABC):
    """Structured logger.

    Only :meth:`log` and :meth:`bind` need implementing; the severity helpers are
    provided here so every backend exposes the same surface.
    """

    @abstractmethod
    def bind(self, **context: Any) -> LogProvider:
        """Return a child logger that stamps ``context`` onto every record.

        Used to attach ``source_key`` / ``run_id`` once at the top of a scrape
        instead of threading them through every call site.
        """

    @abstractmethod
    async def log(self, level: LogLevel, message: str, **fields: Any) -> None:
        """Emit one record."""

    async def debug(self, message: str, **fields: Any) -> None:
        await self.log(LogLevel.DEBUG, message, **fields)

    async def info(self, message: str, **fields: Any) -> None:
        await self.log(LogLevel.INFO, message, **fields)

    async def warning(self, message: str, **fields: Any) -> None:
        await self.log(LogLevel.WARNING, message, **fields)

    async def error(self, message: str, **fields: Any) -> None:
        await self.log(LogLevel.ERROR, message, **fields)

    async def critical(self, message: str, **fields: Any) -> None:
        await self.log(LogLevel.CRITICAL, message, **fields)

    async def exception(self, message: str, exc: BaseException, **fields: Any) -> None:
        """Log ``exc`` at ERROR with its type and formatted traceback attached."""
        await self.log(
            LogLevel.ERROR,
            message,
            error_type=type(exc).__name__,
            error=str(exc),
            traceback="".join(traceback.format_exception(exc)),
            **fields,
        )

    async def aclose(self) -> None:  # noqa: B027 - optional hook, not a requirement
        """Flush and release sinks. Safe to call more than once."""
