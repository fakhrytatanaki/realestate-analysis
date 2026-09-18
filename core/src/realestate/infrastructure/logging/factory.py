"""Log provider assembly."""

from __future__ import annotations

from realestate.config.settings import LoggingSettings
from realestate.domain.enums import LogLevel
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.ports.log_provider import LogProvider
from realestate.infrastructure.logging.composite import CompositeLogProvider
from realestate.infrastructure.logging.file import FileLogProvider
from realestate.infrastructure.logging.std_stream import StdStreamLogProvider


class LogProviderFactory:
    """Builds the configured sink -- console, file, or both composed together."""

    @staticmethod
    def create(settings: LoggingSettings) -> LogProvider:
        try:
            level = LogLevel(settings.level.upper())
        except ValueError as exc:
            raise ConfigurationError(f"unknown log level: {settings.level!r}") from exc

        providers: list[LogProvider] = []
        if settings.stdout_enabled:
            providers.append(
                StdStreamLogProvider(min_level=level, as_json=settings.stdout_json)
            )
        if settings.file_enabled:
            providers.append(
                FileLogProvider(settings.file_path_resolved, min_level=level)
            )

        if not providers:
            # An explicitly silent configuration is legitimate (e.g. tests).
            return CompositeLogProvider([])
        if len(providers) == 1:
            return providers[0]
        return CompositeLogProvider(providers)
