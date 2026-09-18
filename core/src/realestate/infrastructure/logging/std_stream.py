"""Console log sink."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from realestate.domain.enums import LogLevel
from realestate.domain.ports.log_provider import LogProvider


class StdStreamLogProvider(LogProvider):
    """Writes to stdout, routing warnings and worse to stderr.

    That split is what lets ``realestate ... > out.log`` keep operational noise
    on the terminal while the run's own output is redirected.
    """

    def __init__(
        self,
        *,
        min_level: LogLevel = LogLevel.INFO,
        as_json: bool = False,
        context: dict[str, Any] | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._min_level = min_level
        self._as_json = as_json
        self._context = context or {}
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr

    def bind(self, **context: Any) -> StdStreamLogProvider:
        return StdStreamLogProvider(
            min_level=self._min_level,
            as_json=self._as_json,
            context={**self._context, **context},
            stdout=self._stdout,
            stderr=self._stderr,
        )

    async def log(self, level: LogLevel, message: str, **fields: Any) -> None:
        if level.severity < self._min_level.severity:
            return
        record = {**self._context, **fields}
        stream = self._stderr if level.severity >= LogLevel.WARNING.severity else self._stdout
        stream.write(self._format(level, message, record) + "\n")
        stream.flush()

    def _format(self, level: LogLevel, message: str, record: dict[str, Any]) -> str:
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        if self._as_json:
            return json.dumps(
                {"ts": timestamp, "level": level.value, "message": message, **record},
                default=str,
            )
        suffix = " " + " ".join(f"{k}={v}" for k, v in record.items()) if record else ""
        return f"{timestamp} {level.value:<8} {message}{suffix}"
