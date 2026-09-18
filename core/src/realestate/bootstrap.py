"""Composition root.

The single place allowed to know about every layer at once. Everything else
receives its collaborators through constructor arguments, which is what keeps
the ports swappable and the services testable with fakes.

Components are built lazily and cached, so importing this module is cheap and a
process that only needs the CLI never opens a scheduler.
"""

from __future__ import annotations

from functools import cached_property

from tortoise import Tortoise

from realestate.application.jobs.parse_pending_job import make_parse_pending_job
from realestate.application.jobs.scrape_source_job import make_scrape_job
from realestate.application.services.ingestion_service import IngestionService
from realestate.application.services.listing_query_service import ListingQueryService
from realestate.config.settings import Settings, load_settings
from realestate.config.tortoise import build_tortoise_config
from realestate.domain.ports.blob_provider import BlobProvider
from realestate.domain.ports.job_scheduler import JobScheduler
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.repositories import (
    ListingRepository,
    RawDocumentRepository,
    ScrapeRunRepository,
)
from realestate.infrastructure.blob.factory import BlobProviderFactory
from realestate.infrastructure.db.repositories.listing import TortoiseListingRepository
from realestate.infrastructure.db.repositories.raw_document import TortoiseRawDocumentRepository
from realestate.infrastructure.db.repositories.scrape_run import TortoiseScrapeRunRepository
from realestate.infrastructure.logging.factory import LogProviderFactory
from realestate.infrastructure.scheduling.apscheduler_scheduler import ApSchedulerJobScheduler
from realestate.infrastructure.sources.defaults import register_default_sources
from realestate.infrastructure.sources.registry import DataSourceRegistry

#: Job id for the catch-up parse job.
PARSE_PENDING_JOB_ID = "parse-pending"


class Container:
    """Lazily-built application graph."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._db_ready = False

    # -- infrastructure ---------------------------------------------------

    @cached_property
    def log(self) -> LogProvider:
        return LogProviderFactory.create(self.settings.logging)

    @cached_property
    def blob(self) -> BlobProvider:
        return BlobProviderFactory.create(self.settings.blob)

    @cached_property
    def listings(self) -> ListingRepository:
        return TortoiseListingRepository()

    @cached_property
    def documents(self) -> RawDocumentRepository:
        return TortoiseRawDocumentRepository()

    @cached_property
    def runs(self) -> ScrapeRunRepository:
        return TortoiseScrapeRunRepository()

    @cached_property
    def registry(self) -> DataSourceRegistry:
        return register_default_sources(DataSourceRegistry(self.settings, self.log))

    @cached_property
    def scheduler(self) -> JobScheduler:
        return ApSchedulerJobScheduler(self.settings.scheduler)

    # -- application ------------------------------------------------------

    @cached_property
    def ingestion(self) -> IngestionService:
        return IngestionService(
            registry=self.registry,
            blob=self.blob,
            listings=self.listings,
            documents=self.documents,
            runs=self.runs,
            log=self.log,
        )

    @cached_property
    def queries(self) -> ListingQueryService:
        return ListingQueryService(listings=self.listings, documents=self.documents)

    # -- lifecycle --------------------------------------------------------

    async def init_db(self) -> None:
        """Open the database connections. Idempotent.

        Tortoise 1.x scopes its state to a contextvar. Under an ASGI server the
        lifespan runs in a different task from the request handlers, so without
        ``_enable_global_fallback`` the models would find no active context and
        every query would fail. The fallback is cleared again by
        ``close_connections``, so repeated init/teardown cycles (as in tests)
        stay clean.
        """
        if self._db_ready:
            return
        await Tortoise.init(
            config=build_tortoise_config(self.settings.db.url),
            _enable_global_fallback=True,
        )
        if self.settings.db.generate_schemas:
            # Convenience for tests and throwaway databases; production uses aerich.
            await Tortoise.generate_schemas(safe=True)
        self._db_ready = True

    async def close_db(self) -> None:
        if not self._db_ready:
            return
        await Tortoise.close_connections()
        self._db_ready = False

    def register_scheduled_jobs(self) -> list[str]:
        """Register one scrape job per enabled source, plus the catch-up parser.

        Sources with neither an interval nor a crontab are skipped: being
        ``enabled`` without a schedule means "runnable on demand".
        """
        registered: list[str] = []
        for key in self.registry.enabled_keys():
            config = self.settings.source(key)
            job = make_scrape_job(
                self.ingestion,
                key,
                log=self.log,
                max_items=config.max_items,
                params=config.params,
            )
            job_id = f"scrape-{key}"
            if config.crontab:
                self.scheduler.add_cron_job(job, job_id=job_id, crontab=config.crontab)
            elif config.interval_minutes:
                self.scheduler.add_interval_job(
                    job, job_id=job_id, minutes=config.interval_minutes
                )
            else:
                continue
            registered.append(job_id)

        if registered:
            self.scheduler.add_interval_job(
                make_parse_pending_job(self.ingestion, log=self.log),
                job_id=PARSE_PENDING_JOB_ID,
                minutes=15,
            )
            registered.append(PARSE_PENDING_JOB_ID)
        return registered

    async def aclose(self) -> None:
        """Tear everything down in reverse order of construction."""
        if "scheduler" in self.__dict__:
            await self.scheduler.shutdown()
        await self.close_db()
        if "blob" in self.__dict__:
            await self.blob.aclose()
        if "log" in self.__dict__:
            await self.log.aclose()
