"""Generic web-archive data source driven by rule graphs.

Nothing here knows about any particular site. Collection drains the crawl
frontier (which the navigation graph has already routed); parsing walks the
extraction graph. Graphs can be installed explicitly from reviewed seeds or grown
by rule induction; an archived portal is a subclass naming a domain and year range.

The pipeline invariants hold:

* ``fetch`` produces bytes only -- the frontier says *what* to fetch. A capture
  is claimed (``FETCHING``) when fetched and completed only when the pipeline
  acknowledges that its payload is archived, so nothing is marked done that
  was never stored; transient failures go back to the queue with a backoff.
* ``parse`` and ``discover_links`` read archived bytes and the extraction graph
  version pinned when the instance was first used; no network, no model. The
  same bytes and version give the same drafts on every replay.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit

from realestate.domain.archive import (
    ArchivedDocument,
    ArchiveScope,
    DiscoveredLink,
    parse_timestamp,
    surt_key,
)
from realestate.domain.enums import PageKind, RawDocumentKind, RuleDomain
from realestate.domain.exceptions import FetchError, UnrecognisedDocumentError
from realestate.domain.models import FetchContext, ListingDraft, ParseReport, RawPayload
from realestate.domain.ports.archive import CrawlFrontierRepository
from realestate.domain.ports.data_source import ArchiveDataSource
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.rules import RuleEngine, RuleGraphRepository
from realestate.domain.rules import ExtractionOutcome, RuleGraph
from realestate.infrastructure.archive.rate_gate import RateGate
from realestate.infrastructure.archive.wayback import WaybackClient, WaybackSettings

#: Frontier rows claimed per query while fetching.
_FETCH_BATCH = 25
#: First retry delay after a transient failure; doubles per attempt.
_RETRY_BASE = timedelta(minutes=10)


class WaybackDataSource(ArchiveDataSource):
    """Replays captures of one domain from the Wayback Machine."""

    #: Subclasses set these; ``params`` (``domain``, ``from_year``, ``to_year``) override.
    default_domain: ClassVar[str]
    default_from_year: ClassVar[int] = 2005
    default_to_year: ClassVar[int] = 2025
    default_max_fetches: ClassVar[int] = 200
    #: Older portals can explicitly allow the requested in-domain city host.
    allow_requested_subdomains: ClassVar[bool] = False

    def __init__(
        self,
        *,
        log: LogProvider,
        params: Mapping[str, Any] | None = None,
        frontier: CrawlFrontierRepository,
        graphs: RuleGraphRepository,
        engine: RuleEngine,
        client: WaybackClient | None = None,
        gate: RateGate | None = None,
    ) -> None:
        self._log = log.bind(source_key=self.key)
        self._params = dict(params or {})
        self._frontier = frontier
        self._graphs = graphs
        self._engine = engine
        self._client = client or WaybackClient(
            WaybackSettings.from_params(self._params), log=self._log, gate=gate
        )
        self._graph: RuleGraph | None = None
        #: parse() and discover_links() see the same payload back to back.
        # Retain the object itself: id(payload) can be reused as soon as the
        # caller releases it, making a different page look like a cache hit.
        self._last: tuple[RawPayload, ExtractionOutcome | None] | None = None
        self._attempts: dict[int, int] = {}
        self._stats: dict[str, Any] = {}

    def archive_scope(self) -> ArchiveScope:
        return ArchiveScope(
            domain=str(self._params.get("domain", self.default_domain)),
            from_year=int(self._params.get("from_year", self.default_from_year)),
            to_year=int(self._params.get("to_year", self.default_to_year)),
        )

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
        """Claim queued captures and replay them; the budget counts attempts.

        Failed attempts spend budget too: archive time is the scarce resource,
        whether or not a request produced a document.
        """
        budget = ctx.max_items if ctx.max_items is not None else ctx.page_limit
        if budget is None:
            budget = int(self._params.get("max_fetches_per_run", self.default_max_fetches))
        per_quarter = self._params.get("max_fetches_per_quarter")
        attempts = fetched = failures = 0
        started = datetime.now(UTC)
        self._stats = {}
        scope = self.archive_scope()
        domain = scope.domain.lower().removeprefix("www.")
        try:
            while attempts < budget:
                batch = await self._frontier.claim_queued(
                    self.key,
                    limit=min(_FETCH_BATCH, budget - attempts),
                    now=datetime.now(UTC),
                    per_quarter=int(per_quarter) if per_quarter else None,
                )
                if not batch:
                    return
                for entry in batch:
                    attempts += 1
                    ctx.progress.attempts += 1
                    self._attempts[entry.id] = entry.attempts
                    try:
                        hosts = {domain, f"www.{domain}"}
                        if self.allow_requested_subdomains:
                            try:
                                requested_host = urlsplit(entry.original_url).hostname or ""
                            except ValueError as exc:
                                raise FetchError(
                                    f"invalid capture URL: {entry.original_url}", retryable=False
                                ) from exc
                            if requested_host.endswith(f".{domain}"):
                                hosts.add(requested_host)
                        archived = await self._client.fetch_capture(
                            entry.timestamp,
                            entry.original_url,
                            allowed_hosts=hosts,
                        )
                        if not scope.from_year <= archived.captured_at.year <= scope.to_year:
                            raise FetchError(
                                "served capture outside selected years "
                                f"{scope.from_year}-{scope.to_year}: "
                                f"requested {entry.timestamp} {entry.original_url}; "
                                f"served {archived.timestamp} {archived.original_url}; "
                                f"replay {archived.replay_url} (held for review)",
                                retryable=False,
                            )
                    except FetchError as exc:
                        failures += 1
                        ctx.progress.failures += 1
                        await self._fail(entry.id, str(exc), retryable=exc.retryable)
                        await self._log.warning(
                            "capture fetch failed",
                            url=entry.original_url,
                            error=str(exc),
                            retryable=exc.retryable,
                        )
                        continue
                    drift_seconds = (
                        archived.captured_at - parse_timestamp(entry.timestamp)
                    ).total_seconds()
                    fetched += 1
                    await self._log.info(
                        "capture fetched",
                        progress=f"{attempts}/{budget}",
                        url=entry.original_url,
                        captured=archived.timestamp,
                        served_url=archived.original_url,
                        requested=entry.timestamp,
                        capture_drift_seconds=drift_seconds,
                        timestamp_source=archived.timestamp_source,
                        redirects=len(archived.redirect_chain) - 1,
                        bytes=len(archived.content),
                        kind=entry.page_kind.value if entry.page_kind else "?",
                        rule=entry.route_node,
                    )
                    # Completed in acknowledge(), once the pipeline has archived it.
                    yield RawPayload(
                        content=archived.content,
                        kind=RawDocumentKind.HTML,
                        content_type=archived.content_type,
                        source_url=archived.original_url,
                        meta={
                            "frontier_id": entry.id,
                            "url_key": entry.url_key,
                            "timestamp": archived.timestamp,
                            "captured_at": archived.captured_at.isoformat(),
                            "original_url": archived.original_url,
                            "replay_url": archived.replay_url,
                            "requested_timestamp": archived.requested_timestamp,
                            "requested_original_url": archived.requested_original_url,
                            "requested_replay_url": archived.requested_replay_url,
                            "served_timestamp": archived.timestamp,
                            "served_original_url": archived.original_url,
                            "served_url_key": surt_key(archived.original_url),
                            "timestamp_source": archived.timestamp_source,
                            "replay_timestamp": archived.replay_timestamp,
                            "capture_drift_seconds": drift_seconds,
                            "redirect_chain": list(archived.redirect_chain),
                            # Both digest keys describe the requested CDX row, not a
                            # different capture selected by a nearest-capture redirect.
                            "digest": entry.digest,
                            "cdx_digest": entry.digest,
                            "page_kind_hint": entry.page_kind.value if entry.page_kind else None,
                            "route_node": entry.route_node,
                        },
                    )
        finally:
            self._stats = {
                "fetch_attempts": attempts,
                "fetch_failures": failures,
                "fetch_seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
            }

    async def acknowledge(self, payload: RawPayload, *, error: str | None = None) -> None:
        entry_id = payload.meta.get("frontier_id")
        if not isinstance(entry_id, int):
            return
        if error is None:
            await self._frontier.mark_fetched(entry_id)
        else:
            await self._fail(entry_id, error, retryable=True)

    def fetch_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    async def _fail(self, entry_id: int, error: str, *, retryable: bool) -> None:
        """Back to the queue with an exponential delay, or FAILED when hopeless."""
        attempt = self._attempts.get(entry_id, 0) + 1
        limit = int(self._params.get("max_fetch_attempts", 3))
        retry_at = (
            datetime.now(UTC) + _RETRY_BASE * 2 ** (attempt - 1)
            if retryable and attempt < limit
            else None
        )
        await self._frontier.mark_failed(entry_id, error, retry_at=retry_at)

    async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]:
        graph = await self._extraction_graph()
        outcome = self._extract(payload, graph)
        if outcome is None:
            document = _document(payload)
            fingerprint = self._engine.fingerprint(document)
            problems = self._engine.explain_miss(graph, document, country_code=self.country_code)
            await self._log.warning(
                "archive extraction gap",
                url=document.url,
                captured_at=payload.meta.get("timestamp"),
                graph_version=graph.version,
                fingerprint=fingerprint,
                problems=problems,
            )
            detail = f"; {'; '.join(problems[:3])}" if problems else ""
            raise UnrecognisedDocumentError(
                f"no extraction rule matched (graph v{graph.version}, fingerprint {fingerprint})"
                f"{detail}"
            )
        await self._log.info(
            "archive extraction",
            url=payload.source_url,
            captured_at=payload.meta.get("timestamp"),
            graph_version=graph.version,
            template=outcome.template_key,
            items_total=outcome.items_total,
            items_valid=outcome.items_valid,
            problems_count=len(outcome.problems),
            problems=outcome.problems[:20],
            empty_fields=outcome.empty_fields,
            diagnostics=dict(Counter(outcome.diagnostics)),
        )
        return outcome.drafts

    async def parse_report(self, payload: RawPayload) -> ParseReport:
        graph = await self._extraction_graph()
        outcome = self._extract(payload, graph)
        hint = str(payload.meta.get("page_kind_hint") or "").upper()
        routed_as_adverts = hint in (PageKind.LIST.value, PageKind.DETAIL.value)
        if outcome is None:
            return ParseReport(
                graph_version=graph.version,
                hint_mismatch=routed_as_adverts,
                fingerprint=self._engine.fingerprint(_document(payload)),
            )
        origin = (
            graph.node(outcome.template_key).origin
            if graph.has_node(outcome.template_key)
            else None
        )
        return ParseReport(
            graph_version=graph.version,
            template_key=outcome.template_key,
            template_origin=origin.value if origin else None,
            page_kind=outcome.page_kind.value,
            items_total=outcome.items_total,
            items_valid=outcome.items_valid,
            problems=tuple(outcome.problems),
            empty_fields=tuple(outcome.empty_fields),
            hint_mismatch=routed_as_adverts and outcome.page_kind.value != hint,
            advert_links=(
                self._engine.advert_links(_document(payload), identity=self.identity_policy())
                if outcome.page_kind is PageKind.OTHER
                else 0
            ),
        )

    async def discover_links(self, payload: RawPayload) -> Sequence[DiscoveredLink]:
        outcome = self._extract(payload, await self._extraction_graph())
        return outcome.links if outcome is not None else ()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _extraction_graph(self) -> RuleGraph:
        """Loaded once per instance: one run parses with one rule version."""
        if self._graph is None:
            self._graph = await self._graphs.active(
                self.key, RuleDomain.EXTRACTION
            ) or RuleGraph.empty(self.key, RuleDomain.EXTRACTION)
        return self._graph

    def _extract(self, payload: RawPayload, graph: RuleGraph) -> ExtractionOutcome | None:
        if self._last is not None and self._last[0] is payload:
            return self._last[1]
        outcome = self._engine.extract(
            graph,
            _document(payload),
            country_code=self.country_code,
            identity=self.identity_policy(),
        )
        self._last = (payload, outcome)
        return outcome


def _document(payload: RawPayload) -> ArchivedDocument:
    return ArchivedDocument.from_payload(
        payload.content,
        content_type=payload.content_type,
        source_url=payload.source_url,
        meta=payload.meta,
    )
