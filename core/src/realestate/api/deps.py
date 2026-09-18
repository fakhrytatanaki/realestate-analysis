"""FastAPI dependency providers.

Every dependency reads an already-built component off the container stored on
``app.state``. FastAPI therefore never constructs infrastructure itself, and a
test can swap any single collaborator with ``app.dependency_overrides``.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, Request

from realestate.application.services.ingestion_service import IngestionService
from realestate.application.services.listing_query_service import ListingQueryService
from realestate.bootstrap import Container
from realestate.config.settings import Settings
from realestate.domain.exceptions import AuthorizationError
from realestate.domain.ports.job_scheduler import JobScheduler
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.repositories import ScrapeRunRepository
from realestate.infrastructure.sources.registry import DataSourceRegistry


def get_container(request: Request) -> Container:
    """The application graph built during startup."""
    return request.app.state.container  # type: ignore[no-any-return]


ContainerDep = Annotated[Container, Depends(get_container)]


def get_settings(container: ContainerDep) -> Settings:
    return container.settings


def get_queries(container: ContainerDep) -> ListingQueryService:
    return container.queries


def get_ingestion(container: ContainerDep) -> IngestionService:
    return container.ingestion


def get_registry(container: ContainerDep) -> DataSourceRegistry:
    return container.registry


def get_runs(container: ContainerDep) -> ScrapeRunRepository:
    return container.runs


def get_scheduler(container: ContainerDep) -> JobScheduler:
    return container.scheduler


def get_log(container: ContainerDep) -> LogProvider:
    return container.log


SettingsDep = Annotated[Settings, Depends(get_settings)]
QueryServiceDep = Annotated[ListingQueryService, Depends(get_queries)]
IngestionServiceDep = Annotated[IngestionService, Depends(get_ingestion)]
RegistryDep = Annotated[DataSourceRegistry, Depends(get_registry)]
RunRepositoryDep = Annotated[ScrapeRunRepository, Depends(get_runs)]
SchedulerDep = Annotated[JobScheduler, Depends(get_scheduler)]
LogDep = Annotated[LogProvider, Depends(get_log)]


def require_admin(
    settings: SettingsDep,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    """Gate write operations behind a shared secret.

    An unset ``admin_api_key`` closes the endpoint rather than opening it: a
    missing secret must never mean "no authentication required".
    """
    expected = settings.app.admin_api_key
    if not expected:
        raise AuthorizationError(
            "admin_api_key is not configured; privileged endpoints are disabled"
        )
    if not x_admin_key or not secrets.compare_digest(x_admin_key, expected):
        raise AuthorizationError("a valid X-Admin-Key header is required")


AdminDep = Annotated[None, Depends(require_admin)]
