"""Composition root.

The single place allowed to know about every layer at once. Everything else
receives its collaborators through constructor arguments, which is what keeps
the ports swappable and the services testable with fakes.

Components are built lazily and cached, so importing this module is cheap and a
process that only needs the CLI never opens a scheduler.
"""

from __future__ import annotations

import os
from functools import cached_property

from tortoise import Tortoise

from realestate.application.jobs.parse_pending_job import make_parse_pending_job
from realestate.application.jobs.scrape_source_job import make_scrape_job
from realestate.application.services.archive_crawl_service import (
    ArchiveCrawlService,
    CrawlSettings,
)
from realestate.application.services.frontier_link_sink import FrontierLinkSink
from realestate.application.services.ingestion_service import IngestionService
from realestate.application.services.listing_query_service import ListingQueryService
from realestate.application.services.rule_induction_service import (
    InductionSettings,
    RuleInductionService,
)
from realestate.config.settings import Settings, load_settings
from realestate.config.tortoise import build_tortoise_config
from realestate.domain.ports.archive import (
    ArchiveIndex,
    CrawlCursorRepository,
    CrawlFrontierRepository,
)
from realestate.domain.ports.blob_provider import BlobProvider
from realestate.domain.ports.job_scheduler import JobScheduler
from realestate.domain.ports.llm import StructuredLlm
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.repositories import (
    ListingRepository,
    RawDocumentRepository,
    ScrapeRunRepository,
)
from realestate.domain.ports.rules import (
    LlmDecisionRepository,
    RuleEngine,
    RuleGapRepository,
    RuleGraphRepository,
)
from realestate.infrastructure.archive.wayback import WaybackCdxIndex, WaybackClient
from realestate.infrastructure.blob.factory import BlobProviderFactory
from realestate.infrastructure.db.repositories.crawl import (
    TortoiseCrawlCursorRepository,
    TortoiseCrawlFrontierRepository,
)
from realestate.infrastructure.db.repositories.listing import TortoiseListingRepository
from realestate.infrastructure.db.repositories.raw_document import TortoiseRawDocumentRepository
from realestate.infrastructure.db.repositories.rules import (
    TortoiseLlmDecisionRepository,
    TortoiseRuleGapRepository,
    TortoiseRuleGraphRepository,
)
from realestate.infrastructure.db.repositories.scrape_run import TortoiseScrapeRunRepository
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.llm.ollama import OllamaLlm, OllamaSettings
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

    @cached_property
    def frontier(self) -> CrawlFrontierRepository:
        return TortoiseCrawlFrontierRepository()

    @cached_property
    def cursors(self) -> CrawlCursorRepository:
        return TortoiseCrawlCursorRepository()

    @cached_property
    def rule_graphs(self) -> RuleGraphRepository:
        return TortoiseRuleGraphRepository()

    @cached_property
    def rule_gaps(self) -> RuleGapRepository:
        return TortoiseRuleGapRepository()

    @cached_property
    def llm_decisions(self) -> LlmDecisionRepository:
        return TortoiseLlmDecisionRepository()

    @cached_property
    def rule_engine(self) -> RuleEngine:
        return HtmlRuleEngine()

    def llm_api_key_source(self) -> str | None:
        """Where the LLM API key comes from, for diagnostics (never the key)."""
        if os.environ.get("REALESTATE__LLM__API_KEY"):
            return "env REALESTATE__LLM__API_KEY"
        if self.settings.llm.api_key:
            return "[llm] api_key in etc/settings.toml"
        if os.environ.get("OLLAMA_API_KEY"):
            return "env OLLAMA_API_KEY"
        return None

    @cached_property
    def llm(self) -> StructuredLlm:
        config = self.settings.llm
        return OllamaLlm(
            OllamaSettings(
                base_url=config.base_url,
                api_key=config.api_key or os.environ.get("OLLAMA_API_KEY"),
                model=config.model,
                max_concurrency=config.max_concurrency,
                timeout_seconds=config.timeout_seconds,
                temperature=config.temperature,
                seed=config.seed,
                max_retries=config.max_retries,
                use_tools=config.use_tools,
                num_ctx=config.num_ctx,
            ),
            log=self.log,
        )

    @cached_property
    def wayback(self) -> WaybackClient:
        return WaybackClient(log=self.log)

    @cached_property
    def archive_index(self) -> ArchiveIndex:
        return WaybackCdxIndex(self.wayback)

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
            links=FrontierLinkSink(self.frontier),
        )

    @cached_property
    def induction(self) -> RuleInductionService:
        archive = self.settings.archive
        return RuleInductionService(
            graphs=self.rule_graphs,
            gaps=self.rule_gaps,
            decisions=self.llm_decisions,
            llm=self.llm,
            engine=self.rule_engine,
            documents=self.documents,
            blob=self.blob,
            frontier=self.frontier,
            log=self.log,
            settings=InductionSettings(
                nav_batch_size=archive.nav_batch_size,
                max_repairs=archive.max_repairs,
                max_attempts=archive.max_attempts,
                prompt_budget_chars=self.settings.llm.prompt_budget_chars,
            ),
        )

    @cached_property
    def crawler(self) -> ArchiveCrawlService:
        archive = self.settings.archive
        return ArchiveCrawlService(
            registry=self.registry,
            frontier=self.frontier,
            cursors=self.cursors,
            index=self.archive_index,
            graphs=self.rule_graphs,
            engine=self.rule_engine,
            ingestion=self.ingestion,
            induction=self.induction,
            log=self.log,
            settings=CrawlSettings(
                max_captures_list=archive.max_captures_list,
                max_captures_detail=archive.max_captures_detail,
                max_captures_other=archive.max_captures_other,
                cdx_page_size=archive.cdx_page_size,
            ),
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
        if "llm" in self.__dict__:
            close = getattr(self.llm, "aclose", None)
            if close is not None:
                await close()
        if "wayback" in self.__dict__:
            await self.wayback.aclose()
        if "blob" in self.__dict__:
            await self.blob.aclose()
        if "log" in self.__dict__:
            await self.log.aclose()
