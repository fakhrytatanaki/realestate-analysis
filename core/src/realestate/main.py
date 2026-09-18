"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from realestate.api.errors import register_exception_handlers
from realestate.api.v1.router import api_router
from realestate.bootstrap import Container
from realestate.config.settings import Settings

DESCRIPTION = """
Aggregates real-estate classifieds from multiple sources into one queryable
dataset.

Collection runs in two stages: payloads are archived to a blob store first, then
parsed into listings, so parsers can be fixed and replayed over stored data
without re-scraping.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application.

    A factory rather than a module-level singleton so tests can build an app
    against their own settings without touching global state.
    """
    container = Container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await container.init_db()
        await container.log.info(
            "api starting", env=container.settings.app.env, sources=container.registry.keys()
        )

        if container.settings.scheduler.enabled and container.settings.scheduler.run_in_api:
            jobs = container.register_scheduled_jobs()
            await container.scheduler.start()
            await container.log.info("scheduler started in api process", jobs=jobs)

        try:
            yield
        finally:
            await container.log.info("api shutting down")
            await container.aclose()

    app = FastAPI(
        title=container.settings.app.name,
        description=DESCRIPTION,
        version="0.1.0",
        debug=container.settings.app.debug,
        lifespan=lifespan,
    )
    # Dependencies read the container from app.state rather than closing over
    # it, so tests can swap it per-app.
    app.state.container = container

    register_exception_handlers(app)
    app.include_router(api_router)
    return app
