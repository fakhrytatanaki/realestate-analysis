"""Port for looking up data sources.

The application layer needs to ask "which sources exist, and give me one", but
it must not depend on how the registry is built. Inverting that here is what
lets :class:`~realestate.application.services.ingestion_service.IngestionService`
be constructed in a test with a two-line fake registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from realestate.domain.models import SourceDescriptor
from realestate.domain.ports.data_source import DataSource


class SourceRegistry(ABC):
    """Read-only view of the available data sources."""

    @abstractmethod
    def keys(self) -> list[str]:
        """Every registered source key, sorted."""

    @abstractmethod
    def has(self, key: str) -> bool:
        """Whether anything is registered under ``key``."""

    @abstractmethod
    def create(self, key: str) -> DataSource:
        """Instantiate a source from its configuration.

        Raises:
            UnknownDataSourceError: if nothing is registered under ``key``.
        """

    @abstractmethod
    def descriptor(self, key: str) -> SourceDescriptor:
        """Metadata for one source.

        Raises:
            UnknownDataSourceError: if nothing is registered under ``key``.
        """

    @abstractmethod
    def descriptors(self) -> list[SourceDescriptor]:
        """Metadata for every registered source."""

    @abstractmethod
    def enabled_keys(self) -> list[str]:
        """Sources that are both implemented and switched on in configuration."""
