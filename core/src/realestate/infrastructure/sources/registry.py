"""The data source registry.

Which sources *exist* is decided here, in code; which are *switched on* is
decided in ``etc/settings.toml``. Keeping the two separate means there is no
database table to drift out of sync with the implementations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from realestate.config.settings import Settings, SourceSettings
from realestate.domain.exceptions import UnknownDataSourceError
from realestate.domain.models import SourceDescriptor
from realestate.domain.ports.data_source import DataSource
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.source_registry import SourceRegistry


@dataclass(frozen=True, slots=True)
class SourceContext:
    """Everything a source factory is handed at construction time."""

    key: str
    settings: SourceSettings
    log: LogProvider

    @property
    def params(self) -> dict[str, Any]:
        return self.settings.params


#: A factory turns configuration into a live source instance.
SourceFactory = Callable[[SourceContext], DataSource]


@dataclass(frozen=True, slots=True)
class _Registration:
    key: str
    display_name: str
    country_code: str
    factory: SourceFactory
    implemented: bool


class DataSourceRegistry(SourceRegistry):
    """Maps source keys to factories.

    Adding a portal is: write the class, then one :meth:`register` call.
    """

    def __init__(self, settings: Settings, log: LogProvider) -> None:
        self._settings = settings
        self._log = log
        self._registrations: dict[str, _Registration] = {}

    def register(
        self,
        *,
        key: str,
        display_name: str,
        country_code: str,
        factory: SourceFactory,
        implemented: bool = True,
    ) -> None:
        """Make a source available under ``key``.

        ``implemented=False`` marks a placeholder: it still appears in
        ``/sources`` so the roadmap is visible, but running it fails loudly.
        """
        self._registrations[key] = _Registration(
            key=key,
            display_name=display_name,
            country_code=country_code,
            factory=factory,
            implemented=implemented,
        )

    def keys(self) -> list[str]:
        return sorted(self._registrations)

    def has(self, key: str) -> bool:
        return key in self._registrations

    def create(self, key: str) -> DataSource:
        """Instantiate a source from its configuration.

        Raises:
            UnknownDataSourceError: if nothing is registered under ``key``.
        """
        registration = self._registrations.get(key)
        if registration is None:
            raise UnknownDataSourceError(key)
        context = SourceContext(
            key=key, settings=self._settings.source(key), log=self._log
        )
        return registration.factory(context)

    def descriptor(self, key: str) -> SourceDescriptor:
        registration = self._registrations.get(key)
        if registration is None:
            raise UnknownDataSourceError(key)
        config = self._settings.source(key)
        return SourceDescriptor(
            key=registration.key,
            display_name=registration.display_name,
            country_code=registration.country_code,
            enabled=config.enabled and registration.implemented,
            implemented=registration.implemented,
            interval_minutes=config.interval_minutes,
            crontab=config.crontab,
        )

    def descriptors(self) -> list[SourceDescriptor]:
        return [self.descriptor(key) for key in self.keys()]

    def enabled_keys(self) -> list[str]:
        """Sources that are both implemented and switched on in configuration."""
        return [key for key in self.keys() if self.descriptor(key).enabled]
