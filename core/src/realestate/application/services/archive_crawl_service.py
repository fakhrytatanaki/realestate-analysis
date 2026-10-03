"""Crawling a web archive with rule graphs and an LLM fallback.

One round of :meth:`ArchiveCrawlService.crawl`:

1. **route** every newly discovered capture through the navigation graph
   (rule hits decide FETCH / SKIP / DEFER; misses become ``UNROUTED``);
2. **induce** navigation rules for the most common unrouted URL shapes, then
   route again;
3. **ingest**: fetch queued captures, archive them, parse them with the
   extraction graph, and feed links from recognised pages back as evidence;
4. **induce** extraction templates for clusters of unrecognised pages, and
   re-parse those pages with the new rules;
5. **look up** linked URLs the frontier had no capture of, one exact-URL index
   query each near the linking page's capture time (bounded per round).

Every LLM answer is compiled into rules, so each round needs fewer calls than
the last. Budgets (fetches, LLM calls) are explicit because the archive and the
model are both shared, rate-limited services.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from realestate.application.rules.seeds import seed_navigation_graph
from realestate.application.services.ingestion_service import IngestionService, ReplayResult
from realestate.application.services.rule_induction_service import (
    InductionReport,
    RuleInductionService,
)
from realestate.domain.archive import EXPLORE_NODE, ArchiveScope, FrontierEntry, capture_year
from realestate.domain.enums import (
    CrawlStatus,
    LinkRequestStatus,
    PageKind,
    RouteDecision,
    RuleDomain,
    RunTrigger,
)
from realestate.domain.exceptions import ConfigurationError, FetchError
from realestate.domain.models import FetchContext, ScrapeRun
from realestate.domain.ports.archive import (
    ArchiveIndex,
    CrawlCursorRepository,
    CrawlFrontierRepository,
    LinkRequestRepository,
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
    #: Distinct-content captures fetched per URL *per capture year*, by page
    #: kind: a lifetime cap lets whichever years were enumerated first use up
    #: every slot. List pages are snapshots of what was on offer, so several a
    #: year are worth having.
    max_captures_list: int = 2
    max_captures_detail: int = 1
    max_captures_other: int = 1
    route_batch: int = 2000
    cdx_page_size: int = 5000
    #: Index pages enumerated per crawl across years not yet complete.
    enumeration_pages_per_crawl: int = 20
    #: FETCHING claims older than this belong to an interrupted run.
    claim_timeout: timedelta = timedelta(minutes=15)
    #: Exact-URL index lookups of unmatched links, per round.
    link_lookups_per_round: int = 10
    #: Captures of a linked URL accepted per lookup, and how far from the
    #: linking page's capture time they may be.
    link_lookup_captures: int = 2
    link_lookup_window_days: int = 183
    #: Failed lookups of one URL before it is given up.
    link_lookup_attempts: int = 3


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
class LinkLookupReport:
    looked_up: int = 0
    found: int = 0
    captures_added: int = 0
    not_archived: int = 0
    failed: int = 0


@dataclass(slots=True)
class ExplorationReport:
    #: year -> captures sent to the fetch queue as exploration samples.
    queued: dict[int, int] = field(default_factory=dict)


@dataclass(slots=True)
class CrawlRound:
    number: int
    routed: RouteReport
    navigation: InductionReport
    run: ScrapeRun | None
    extraction: InductionReport
    reparsed_created: int = 0
    links: LinkLookupReport = field(default_factory=LinkLookupReport)
    fetch_attempts: int = 0
    fetch_failures: int = 0


@dataclass(slots=True)
class CrawlReport:
    enumeration: EnumerationReport | None = None
    rounds: list[CrawlRound] = field(default_factory=list)
    #: Why the loop ended: rounds done, a budget spent, or no progress.
    stopped: str = ""
    incomplete_years: list[int] = field(default_factory=list)

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
        links: LinkRequestRepository | None = None,
    ) -> None:
        self._links = links
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
            cursor_scope = cursor_key(scope.domain, year)
            resume_key, done = await self._cursor(source_key, scope.domain, year)
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

    async def _cursor(self, source_key: str, domain: str, year: int) -> tuple[str | None, bool]:
        """A year's resume point, adopting a pre-domain ``cdx:{year}`` cursor once."""
        state = await self._cursors.get(source_key, cursor_key(domain, year))
        if state != (None, False):
            return state
        legacy = await self._cursors.get(source_key, f"cdx:{year}")
        if legacy != (None, False):
            resume_key, done = legacy
            await self._cursors.save(
                source_key, cursor_key(domain, year), resume_key=resume_key, done=done
            )
        return legacy

    async def coverage(self, source_key: str) -> list[YearCoverage]:
        """Per capture year: enumeration state and what became of its captures."""
        scope = self.scope(source_key)
        by_quarter = await self._frontier.status_by_quarter(source_key)
        years: list[YearCoverage] = []
        for year in range(scope.from_year, scope.to_year + 1):
            resume_key, done = await self._cursor(source_key, scope.domain, year)
            enumeration = (
                "done" if done else "in progress" if resume_key else "not enumerated"
            )
            quarters: dict[str, dict[str, int]] = {}
            for (quarter, status), count in sorted(by_quarter.items()):
                if quarter.startswith(str(year)):
                    quarters.setdefault(quarter, {})[status.value] = count
            years.append(YearCoverage(year=year, enumeration=enumeration, quarters=quarters))
        return years

    # -- linked URLs and exploration ---------------------------------------

    async def resolve_links(self, source_key: str, *, limit: int) -> LinkLookupReport:
        """Look up linked URLs no enumerated capture matched, one index query each.

        Found captures join the frontier carrying the link as evidence, so the
        next routing pass fetches them through the evidence rule.
        """
        report = LinkLookupReport()
        if self._links is None or limit <= 0:
            return report
        domain = self.scope(source_key).domain
        for request in await self._links.pending(source_key, limit=limit):
            assert request.id is not None
            if not _within_domain(request.url, domain):
                await self._links.resolve(
                    request.id, status=LinkRequestStatus.NONE, error="outside the archived domain"
                )
                report.not_archived += 1
                continue
            report.looked_up += 1
            try:
                captures = await self._index.lookup(
                    request.url,
                    around=request.parent_timestamp,
                    window_days=self._settings.link_lookup_window_days,
                    limit=self._settings.link_lookup_captures,
                )
            except FetchError as exc:
                report.failed += 1
                exhausted = request.attempts + 1 >= self._settings.link_lookup_attempts
                await self._links.resolve(
                    request.id,
                    status=LinkRequestStatus.FAILED if exhausted else LinkRequestStatus.PENDING,
                    error=str(exc),
                )
                continue
            if not captures:
                report.not_archived += 1
                await self._links.resolve(request.id, status=LinkRequestStatus.NONE)
                continue
            report.found += 1
            report.captures_added += await self._frontier.add_captures(source_key, captures)
            await self._frontier.add_evidence(
                source_key, {capture.url_key: request.rel for capture in captures}
            )
            await self._links.resolve(
                request.id, status=LinkRequestStatus.FOUND, captures_found=len(captures)
            )
        if report.looked_up:
            await self._log.info(
                "linked urls looked up",
                source_key=source_key,
                looked_up=report.looked_up,
                found=report.found,
                captures_added=report.captures_added,
                not_archived=report.not_archived,
                failed=report.failed,
            )
        return report

    async def explore(
        self, source_key: str, *, per_year: int, seed: str = "explore"
    ) -> ExplorationReport:
        """Queue a reproducible random sample of skipped/deferred/unrouted captures.

        Routing decisions are never checked against the pages they turned
        away; fetching a few per year (route node ``explore``, low priority)
        lets the audit estimate how often SKIP/DEFER/miss threw away adverts.
        """
        report = ExplorationReport()
        rejected = await self._frontier.captures_with_status(
            source_key, [CrawlStatus.SKIPPED, CrawlStatus.DEFERRED, CrawlStatus.UNROUTED]
        )
        by_year: dict[int, list[tuple[str, int]]] = defaultdict(list)
        for entry_id, timestamp in rejected:
            draw = hashlib.sha1(f"{seed}:{entry_id}".encode()).hexdigest()
            by_year[capture_year(timestamp)].append((draw, entry_id))
        chosen: list[int] = []
        for year, entries in sorted(by_year.items()):
            picked = [entry_id for _, entry_id in sorted(entries)[:per_year]]
            report.queued[year] = len(picked)
            chosen.extend(picked)
        if chosen:
            await self._frontier.set_route(
                chosen, status=CrawlStatus.QUEUED, route_node=EXPLORE_NODE, priority=1
            )
        await self._log.info(
            "exploration samples queued", source_key=source_key, per_year=report.queued
        )
        return report
    async def incomplete_years(self, source_key: str) -> list[int]:
        """Configured years whose CDX enumeration has not completed."""
        scope = self.scope(source_key)
        missing = []
        for year in range(scope.from_year, scope.to_year + 1):
            _, done = await self._cursor(source_key, scope.domain, year)
            if not done:
                missing.append(year)
        return missing

    # -- routing ----------------------------------------------------------

    async def navigation_graph(self, source_key: str) -> RuleGraph:
        graph = await self._graphs.active(source_key, RuleDomain.NAVIGATION)
        if graph is None:
            graph = await self._graphs.save_version(seed_navigation_graph(source_key))
        return graph

    async def route(
        self, source_key: str, *, retry_unrouted: bool = True, reopen_capped: bool = False
    ) -> RouteReport:
        """Decide what to do with every discovered capture.

        ``reopen_capped`` first sends captures skipped by the per-URL capture
        cap back to routing, e.g. after the cap policy changed.
        """
        graph = await self.navigation_graph(source_key)
        if reopen_capped:
            reopened = await self._frontier.reopen_capped(source_key)
            await self._log.info("capture-capped captures reopened", count=reopened)
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
        fetches_per_round: int | None = None,
        enumerate_missing: bool = True,
        max_enumeration_pages: int | None = None,
        max_link_lookups: int | None = None,
        require_complete_enumeration: bool = False,
    ) -> CrawlReport:
        """Run rounds until ``rounds`` (0 = no limit), a budget, or no progress.

        Safe to interrupt and re-run: frontier state, rule graphs and archived
        payloads are all persisted, payloads archived but not yet parsed (an
        interrupted run) are parsed first, and captures claimed by a run that
        died are released back to the queue.

        Every year in the source's range whose index is not fully enumerated
        gets enumerated (within ``max_enumeration_pages``), not only when the
        frontier is empty -- otherwise the first year ever enumerated would be
        the only one.

        ``max_fetches`` counts selected capture attempts including failures,
        across all rounds. The optional enumeration gate checks every configured
        source year before parsing leftovers, routing, induction or replay fetches.
        """
        if rounds < 0 or max_fetches < 0 or max_llm_calls < 0:
            raise ConfigurationError("rounds and crawl budgets must be nonnegative")
        if fetches_per_round is not None and fetches_per_round <= 0:
            raise ConfigurationError("fetches_per_round must be positive")
        if max_enumeration_pages is not None and max_enumeration_pages <= 0:
            raise ConfigurationError("max_enumeration_pages must be positive")
        report = CrawlReport()
        log = self._log.bind(source_key=source_key)
        released = await self._frontier.release_stale_claims(
            source_key, claimed_before=datetime.now(UTC) - self._settings.claim_timeout
        )
        if released:
            await log.warning("released captures claimed by an interrupted run", count=released)
        if enumerate_missing:
            report.enumeration = await self.enumerate(
                source_key,
                max_pages=max_enumeration_pages or self._settings.enumeration_pages_per_crawl,
            )
            if report.enumeration.added:
                await log.info(
                    "enumerated the archive index",
                    added=report.enumeration.added,
                    years_completed=report.enumeration.years_completed,
                )
        counts = await self._frontier.counts(source_key)

        if require_complete_enumeration:
            report.incomplete_years = await self.incomplete_years(source_key)
            if report.incomplete_years:
                report.stopped = "enumeration incomplete"
                await log.warning(
                    "crawl held until enumeration completes", years=report.incomplete_years
                )
                return report

        leftover = await self._ingestion.parse_pending(source_key, limit=10_000)
        if leftover.created or leftover.updated or leftover.unchanged:
            await log.info(
                "parsed payloads left pending by an earlier run",
                created=leftover.created,
                updated=leftover.updated,
            )

        domain = self.scope(source_key).domain
        llm_left = max_llm_calls
        lookups_left = (
            max_link_lookups
            if max_link_lookups is not None
            else self._settings.link_lookups_per_round * (rounds if rounds > 0 else 10**6)
        )
        fetches_left = max_fetches
        per_round = fetches_per_round or (
            max(1, max_fetches // rounds) if rounds > 0 else min(max_fetches, 50)
        )
        await log.info(
            "crawl started",
            rounds=rounds or "until idle",
            max_fetches=max_fetches,
            fetches_per_round=per_round,
            max_llm_calls=max_llm_calls,
            frontier=_format_counts(counts),
        )
        number = 0
        while rounds <= 0 or number < rounds:
            number += 1
            if fetches_left <= 0:
                report.stopped = "fetch budget spent"
                break
            await log.info(
                "crawl round started", round=number, fetches_left=fetches_left, llm_left=llm_left
            )
            routed = await self.route(source_key)

            # Counted every round, budget or not, so gap counts are the backlog.
            await self._induction.collect_navigation_gaps(source_key)
            navigation = InductionReport()
            rerouted = RouteReport()
            if llm_left > 0 and routed.unrouted:
                navigation = await self._induction.induce(
                    source_key, domain=RuleDomain.NAVIGATION, max_calls=llm_left, domain_name=domain
                )
                llm_left -= navigation.llm_calls
                if navigation.versions:
                    rerouted = await self.route(source_key)
                    routed.add(rerouted)

            fetch_ctx = FetchContext(max_items=min(per_round, fetches_left))
            run = await self._ingestion.ingest(
                source_key,
                trigger=RunTrigger.BACKFILL,
                ctx=fetch_ctx,
            )
            fetch_attempts = max(
                fetch_ctx.progress.attempts,
                int(run.stats.get("fetch_attempts", run.documents_fetched)),
            )
            fetches_left -= fetch_attempts

            await self._induction.collect_extraction_gaps(source_key)
            extraction = InductionReport()
            replay = ReplayResult()
            if llm_left > 0:
                extraction = await self._induction.induce(
                    source_key, domain=RuleDomain.EXTRACTION, max_calls=llm_left
                )
                llm_left -= extraction.llm_calls
                if extraction.versions:
                    # A new graph version is not applied until every unrecognised
                    # and older-parsed document is re-read with it.
                    replay = await self._ingestion.reparse_stale(
                        source_key, graph_version=max(extraction.versions), limit=None
                    )
            reparsed = replay.created

            links = await self.resolve_links(
                source_key, limit=min(self._settings.link_lookups_per_round, lookups_left)
            )
            lookups_left -= links.looked_up

            report.rounds.append(
                CrawlRound(
                    number=number,
                    routed=routed,
                    navigation=navigation,
                    run=run,
                    extraction=extraction,
                    reparsed_created=reparsed,
                    links=links,
                    fetch_attempts=fetch_attempts,
                    fetch_failures=fetch_ctx.progress.failures,
                )
            )
            await log.info(
                "crawl round finished",
                round=number,
                queued=routed.queued,
                unrouted=routed.unrouted,
                nav_rules=navigation.accepted,
                fetched=run.documents_fetched,
                fetch_attempts=fetch_attempts,
                fetch_failures=fetch_ctx.progress.failures,
                created=run.listings_created + reparsed,
                updated=run.listings_updated,
                templates=extraction.accepted,
                links_found=links.found,
                llm_calls=navigation.llm_calls + extraction.llm_calls,
                llm_left=llm_left,
                fetches_left=fetches_left,
            )
            for note in navigation.notes + extraction.notes:
                await log.warning("induction note", note=note)
            # A saved version counts only if it changed something: a rule that
            # never wins must not keep an unbounded crawl alive.
            rules_took_effect = (
                rerouted.queued
                + rerouted.skipped
                + rerouted.deferred
                + replay.changed
                + replay.created
                + replay.updated
            )
            progressed = fetch_attempts or links.captures_added or rules_took_effect
            if not progressed:
                report.stopped = (
                    "no progress (nothing queued to fetch and no new rules)"
                    if llm_left > 0
                    else "no progress and the LLM budget is spent"
                )
                break
        else:
            report.stopped = f"{rounds} rounds done"
        if not report.stopped:
            report.stopped = "fetch budget spent"
        await log.info(
            "crawl finished",
            reason=report.stopped,
            rounds=len(report.rounds),
            llm_calls=report.llm_calls,
            frontier=_format_counts(await self._frontier.counts(source_key)),
        )
        return report


def _format_counts(counts: dict[CrawlStatus, int] | dict[str, int]) -> str:
    shown = (f"{str(status).lower()}:{count}" for status, count in counts.items() if count)
    return ",".join(shown) or "empty"


def _within_domain(url: str, domain: str) -> bool:
    host = (urlsplit(url if "://" in url else "http://" + url).hostname or "").lower()
    domain = domain.lower().removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def cursor_key(domain: str, year: int) -> str:
    """Enumeration cursor scope: a completion marker is only valid for its domain."""
    return f"cdx:{domain}:{year}"


@dataclass(slots=True)
class YearCoverage:
    year: int
    #: "done", "in progress" or "not enumerated".
    enumeration: str
    #: quarter -> frontier status -> captures.
    quarters: dict[str, dict[str, int]] = field(default_factory=dict)


_TAKEN = (CrawlStatus.FETCHED, CrawlStatus.FETCHING, CrawlStatus.QUEUED)


def select_captures(captures: Sequence[FrontierEntry], cap: int) -> set[int]:
    """Which captures of one URL to fetch: distinct content, spread over time.

    ``cap`` applies per capture year, so every year a URL was archived in can
    contribute, whatever order years were enumerated in. Within a year,
    captures with a digest already fetched (or queued) are redundant; of the
    rest, the first and last are kept and the remainder evenly spaced. The
    same content in another year is still worth a fetch: it shows the advert
    was still up.
    """
    by_year: dict[int, list[FrontierEntry]] = defaultdict(list)
    for entry in captures:
        by_year[capture_year(entry.timestamp)].append(entry)
    chosen: set[int] = set()
    for entries in by_year.values():
        chosen |= _select_in_year(entries, cap)
    return chosen


def _select_in_year(captures: Sequence[FrontierEntry], cap: int) -> set[int]:
    taken_digests = {entry.digest for entry in captures if entry.status in _TAKEN}
    budget = cap - len(taken_digests)
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
