"""Liveness and readiness."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from tortoise import Tortoise

from realestate.api.deps import ContainerDep

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    app: str
    env: str


class ReadinessResponse(BaseModel):
    status: str
    database: bool
    blob: bool


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(container: ContainerDep) -> HealthResponse:
    """Whether the process is up. Touches no dependencies."""
    return HealthResponse(
        status="ok", app=container.settings.app.name, env=container.settings.app.env
    )


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(container: ContainerDep) -> ReadinessResponse:
    """Whether the dependencies this service needs are actually reachable."""
    database = False
    try:
        await Tortoise.get_connection("default").execute_query("SELECT 1")
        database = True
    except Exception:  # any failure to reach the database means "not ready"
        database = False

    blob = await container.blob.healthcheck()
    return ReadinessResponse(
        status="ok" if database and blob else "degraded", database=database, blob=blob
    )
