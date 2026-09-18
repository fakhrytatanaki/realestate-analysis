"""JSON-lines file log sink."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles

from realestate.domain.enums import LogLevel
from realestate.domain.ports.log_provider import LogProvider


class FileLogProvider(LogProvider):
    """Appends one JSON object per line to a log file.

    Records are handed to an :class:`asyncio.Queue` drained by a single writer
    task, so a slow disk never stalls a request or a scrape. Children created by
    :meth:`bind` share the parent's queue and writer task.
    """

    def __init__(
        self,
        path: Path,
        *,
        min_level: LogLevel = LogLevel.INFO,
        context: dict[str, Any] | None = None,
        queue_size: int = 4096,
        _shared: _FileWriter | None = None,
    ) -> None:
        self._min_level = min_level
        self._context = context or {}
        self._writer = _shared or _FileWriter(path, queue_size=queue_size)

    def bind(self, **context: Any) -> FileLogProvider:
        return FileLogProvider(
            self._writer.path,
            min_level=self._min_level,
            context={**self._context, **context},
            _shared=self._writer,
        )

    async def log(self, level: LogLevel, message: str, **fields: Any) -> None:
        if level.severity < self._min_level.severity:
            return
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": level.value,
            "message": message,
            **self._context,
            **fields,
        }
        await self._writer.submit(json.dumps(record, default=str))

    async def aclose(self) -> None:
        await self._writer.aclose()


class _FileWriter:
    """Serialises writes from many loggers onto one background task."""

    def __init__(self, path: Path, *, queue_size: int) -> None:
        self.path = path
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)

    async def submit(self, line: str) -> None:
        if self._closed:
            return
        self._ensure_task()
        try:
            self._queue.put_nowait(line)
        except asyncio.QueueFull:
            # Dropping a log line beats blocking the caller or growing without
            # bound; the console sink still has it.
            pass

    def _ensure_task(self) -> None:
        # Started lazily so the provider can be constructed outside a running loop.
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain(), name="file-log-writer")

    async def _drain(self) -> None:
        while True:
            line = await self._queue.get()
            try:
                if line is None:
                    return
                async with aiofiles.open(self.path, "a", encoding="utf-8") as handle:
                    await handle.write(line + "\n")
            except OSError:
                # A failing log sink must never take the application down.
                pass
            finally:
                self._queue.task_done()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task is None or self._task.done():
            return
        await self._queue.put(None)
        await asyncio.wait_for(self._task, timeout=5)
