"""Crawling a web archive with rule graphs and an LLM fallback.

One round of :meth:`ArchiveCrawlService.crawl`:

1. **route** every newly discovered capture through the navigation graph
   (rule hits decide FETCH / SKIP / DEFER; misses become ``UNROUTED``);
2. **induce** navigation rules for the most common unrouted URL shapes, then
   route again;
3. **ingest**: fetch queued captures, archive them, parse them with the
   extraction graph, and feed links from recognised pages back as evidence;
4. **induce** extraction templates for clusters of unrecognised pages, and
   re-parse those pages with the new rules.

Every LLM answer is compiled into rules, so each round needs fewer calls than
the last. Budgets (fetches, LLM calls) are explicit because the archive and the
model are both shared, rate-limited services.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from realestate.application.rules.seeds import seed_navigation_graph
from realestate.application.services.ingestion_service import IngestionService
from realestate.application.services.rule_induction_service import (
    InductionReport,
    RuleInductionService,
)
from realestate.domain.archive import ArchiveScope, FrontierEntry
from realestate.domain.enums import (
    CrawlStatus,
    PageKind,
    RouteDecision,
    RuleDomain,
    RunTrigger,
)
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.models import FetchContext, ScrapeRun
from realestate.domain.ports.archive import (
    ArchiveIndex,
    CrawlCursorRepository,
    CrawlFrontierRepository,
)
from realestate.domain.ports.data_source import ArchiveDataSource
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.rules import RuleEngine, RuleGraphRepository
from realestate.domain.ports.source_registry import SourceRegistry
from realestate.domain.rules import RuleGraph

#: (status, route node, priority, page kind): captures sharing an outcome.
_RouteKey = tuple[CrawlStatus, str | None, int, PageKind | None]


@dataclass(frozen=True, slots=True)
class CrawlSettings:
    #: Distinct-content captures fetched per URL, by page kind: list pages are
    #: snapshots of what was on offer, so several over time are worth having.
    max_captures_list: int = 4
    max_captures_detail: int = 2
    max_captures_other: int = 1
    route_batch: int = 2000
    cdx_page_size: int = 5000


@dataclass(slots=True)
class RouteReport:
    url_keys: int = 0
    queued: int = 0
    skipped: int = 0
    deferred: int = 0
    unrouted: int = 0

    def add(self, other: RouteReport) -> None:
        self.url_keys += other.url_keys
        self.queued += other.queued
        self.skipped += other.skipped
        self.deferred += other.deferred
        self.unrouted += other.unrouted


@dataclass(slots=True)
class EnumerationReport:
    added: int = 0
    pages: int = 0
    years_completed: list[int] = field(default_factory=list)


@dataclass(slots=True)
class CrawlRound:
    number: int
    routed: RouteReport
    navigation: InductionReport
    run: ScrapeRun | None
    extraction: InductionReport
    reparsed_created: int = 0


@dataclass(slots=True)
class CrawlReport:
    enumeration: EnumerationReport | None = None
    rounds: list[CrawlRound] = field(default_factory=list)

    @property
    def llm_calls(self) -> int:
        return sum(r.navigation.llm_calls + r.extraction.llm_calls for r in self.rounds)


class ArchiveCrawlService:
    def __init__(
        self,
        *,
        registry: SourceRegistry,
        frontier: CrawlFrontierRepository,
        cursors: CrawlCursorRepository,
        index: ArchiveIndex,
        graphs: RuleGraphRepository,
        engine: RuleEngine,
        ingestion: IngestionService,
        induction: RuleInductionService,
        log: LogProvider,
        settings: CrawlSettings | None = None,
    ) -> None:
        self._registry = registry
        self._frontier = frontier
        self._cursors = cursors
        self._index = index
        self._graphs = graphs
        self._engine = engine
        self._ingestion = ingestion
        self._induction = induction
        self._log = log
        self._settings = settings or CrawlSettings()

    def scope(self, source_key: str) -> ArchiveScope:
        source = self._registry.create(source_key)
        if not isinstance(source, ArchiveDataSource):
            raise ConfigurationError(f"source '{source_key}' is not an archive source")
        return source.archive_scope()

    # -- enumeration ------------------------------------------------------

    async def enumerate(
        self,
        source_key: str,
        *,
        from_year: int | None = None,
        to_year: int | None = None,
        max_pages: int | None = None,
    ) -> EnumerationReport:
        """Copy the archive index into the frontier; resumable per year."""
        scope = self.scope(source_key)
        report = EnumerationReport()
        for year in range(from_year or scope.from_year, (to_year or scope.to_year) + 1):
            cursor_scope = f"cdx:{year}"
            resume_key, done = await self._cursors.get(source_key, cursor_scope)
            if done:
                continue
            async for page in self._index.iter_captures(
                scope.domain, year, resume_key=resume_key, page_size=self._settings.cdx_page_size
            ):
                report.added += await self._frontier.add_captures(source_key, page.captures)
                report.pages += 1
                finished = page.resume_key is None or not page.captures
                await self._cursors.save(
                    source_key, cursor_scope, resume_key=page.resume_key, done=finished
                )
                await self._log.info(
                    "enumerated captures", year=year, page=report.pages, captures=len(page.captures)
                )
                if finished:
                    report.years_completed.append(year)
                if max_pages is not None and report.pages >= max_pages:
                    return report
        return report

    # -- routing ----------------------------------------------------------

    async def navigation_graph(self, source_key: str) -> RuleGraph:
        graph = await self._graphs.active(source_key, RuleDomain.NAVIGATION)
        if graph is None:
            graph = await self._graphs.save_version(seed_navigation_graph(source_key))
        return graph

    async def route(self, source_key: str, *, retry_unrouted: bool = True) -> RouteReport:
        """Decide what to do with every discovered capture."""
        graph = await self.navigation_graph(source_key)
        if retry_unrouted:
            await self._frontier.reset_status(
                source_key, from_status=CrawlStatus.UNROUTED, to_status=CrawlStatus.DISCOVERED
            )
        report = RouteReport()
        while True:
            keys = await self._frontier.url_keys_with_status(
                source_key, [CrawlStatus.DISCOVERED], limit=self._settings.route_batch
            )
            if not keys:
                break
            entries = await self._frontier.captures_for(source_key, keys)
            by_key: dict[str, list[FrontierEntry]] = defaultdict(list)
            for entry in entries:
                by_key[entry.url_key].append(entry)
            batch = RouteReport()
            updates: dict[_RouteKey, list[int]] = defaultdict(list)
            for captures in by_key.values():
                self._route_url(graph, captures, batch, updates)
            # One UPDATE per distinct outcome rather than per URL.
            for (status, node, priority, page_kind), ids in updates.items():
                await self._frontier.set_route(
                    ids, status=status, route_node=node, priority=priority, page_kind=page_kind
                )
            if batch.url_keys == 0:  # nothing changed state: never spin
                break
            report.add(batch)
        await self._log.info(
            "routing complete",
            graph_version=graph.version,
            url_keys=report.url_keys,
            queued=report.queued,
            skipped=report.skipped,
            deferred=report.deferred,
            unrouted=report.unrouted,
        )
        return report

    def _route_url(
        self,
        graph: RuleGraph,
        captures: Sequence[FrontierEntry],
        report: RouteReport,
        updates: dict[_RouteKey, list[int]],
    ) -> None:
        pending = [entry for entry in captures if entry.status is CrawlStatus.DISCOVERED]
        if not pending:
            return
        report.url_keys += 1
        evidence: dict[str, list[str]] = {}
        for entry in captures:
            for rel in entry.evidence.get("linked_as") or []:
                evidence.setdefault("linked_as", [])
                if rel not in evidence["linked_as"]:
                    evidence["linked_as"].append(rel)
        outcome = self._engine.route(graph, captures[-1].original_url, evidence)
        ids = [entry.id for entry in pending]
        if outcome is None:
            updates[(CrawlStatus.UNROUTED, None, 0, None)] += ids
            report.unrouted += 1
            return
        if outcome.decision is RouteDecision.SKIP:
            updates[(CrawlStatus.SKIPPED, outcome.node_key, 0, None)] += ids
            report.skipped += 1
            return
        if outcome.decision is RouteDecision.DEFER:
            updates[(CrawlStatus.DEFERRED, outcome.node_key, 0, outcome.page_kind)] += ids
            report.deferred += 1
            return

        chosen = select_captures(captures, self._cap(outcome.page_kind))
        queue = [entry.id for entry in pending if entry.id in chosen]
        rest = [entry.id for entry in pending if entry.id not in chosen]
        if queue:
            key = (CrawlStatus.QUEUED, outcome.node_key, outcome.priority, outcome.page_kind)
            updates[key] += queue
            report.queued += 1
        if rest:
            updates[(CrawlStatus.SKIPPED, f"{outcome.node_key}#capture-cap", 0, None)] += rest

    def _cap(self, page_kind: PageKind | None) -> int:
        if page_kind is PageKind.LIST:
            return self._settings.max_captures_list
        if page_kind is PageKind.DETAIL:
            return self._settings.max_captures_detail
        return self._settings.max_captures_other

    # -- the loop ---------------------------------------------------------

    async def crawl(
        self,
        source_key: str,
        *,
        rounds: int = 3,
        max_fetches: int = 100,
        max_llm_calls: int = 10,
        enumerate_if_empty: bool = True,
        max_enumeration_pages: int | None = None,
    ) -> CrawlReport:
        report = CrawlReport()
        counts = await self._frontier.counts(source_key)
        if enumerate_if_empty and sum(counts.values()) == 0:
            report.enumeration = await self.enumerate(source_key, max_pages=max_enumeration_pages)

        domain = self.scope(source_key).domain
        llm_left = max_llm_calls
        fetches_per_round = max(1, max_fetches // max(1, rounds))
        for number in range(1, rounds + 1):
            routed = await self.route(source_key)

            navigation = InductionReport()
            if llm_left > 0 and routed.unrouted:
                await self._induction.collect_navigation_gaps(source_key)
                navigation = await self._induction.induce(
                    source_key, domain=RuleDomain.NAVIGATION, max_calls=llm_left, domain_name=domain
                )
                llm_left -= navigation.llm_calls
                if navigation.versions:
                    routed.add(await self.route(source_key))

            run = await self._ingestion.ingest(
                source_key,
                trigger=RunTrigger.BACKFILL,
                ctx=FetchContext(max_items=fetches_per_round),
            )

            extraction = InductionReport()
            reparsed = 0
            if llm_left > 0:
                await self._induction.collect_extraction_gaps(source_key)
                extraction = await self._induction.induce(
                    source_key, domain=RuleDomain.EXTRACTION, max_calls=llm_left
                )
                llm_left -= extraction.llm_calls
                if extraction.versions:
                    reparsed = (await self._ingestion.reparse_unrecognised(source_key)).created

            report.rounds.append(
                CrawlRound(
                    number=number,
                    routed=routed,
                    navigation=navigation,
                    run=run,
                    extraction=extraction,
                    reparsed_created=reparsed,
                )
            )
            await self._log.info(
                "crawl round finished",
                round=number,
                fetched=run.documents_fetched,
                created=run.listings_created + reparsed,
                llm_calls=navigation.llm_calls + extraction.llm_calls,
            )
            progressed = run.documents_fetched or navigation.versions or extraction.versions
            if not progressed:
                break
        return report


def select_captures(captures: Sequence[FrontierEntry], cap: int) -> set[int]:
    """Which captures of one URL to fetch: distinct content, spread over time.

    Captures with a digest already fetched (or queued) are redundant. Of the
    rest, the first and last are kept and the remainder evenly spaced, so a
    list page yields snapshots across its whole archived life.
    """
    taken_digests = {
        entry.digest
        for entry in captures
        if entry.status in (CrawlStatus.FETCHED, CrawlStatus.QUEUED)
    }
    budget = cap - len(
        {e.digest for e in captures if e.status in (CrawlStatus.FETCHED, CrawlStatus.QUEUED)}
    )
    if budget <= 0:
        return set()
    distinct: list[FrontierEntry] = []
    seen = set(taken_digests)
    for entry in sorted(captures, key=lambda e: e.timestamp):
        if entry.status is CrawlStatus.DISCOVERED and entry.digest not in seen:
            seen.add(entry.digest)
            distinct.append(entry)
    if len(distinct) <= budget:
        return {entry.id for entry in distinct}
    if budget == 1:
        return {distinct[-1].id}
    step = (len(distinct) - 1) / (budget - 1)
    return {distinct[round(index * step)].id for index in range(budget)}
