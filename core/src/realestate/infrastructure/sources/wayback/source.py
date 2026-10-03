"""Generic web-archive data source driven by rule graphs.

Nothing here knows about any particular site. Collection drains the crawl
frontier (which the navigation graph has already routed); parsing walks the
extraction graph. Graphs can be installed explicitly from reviewed seeds or grown
by rule induction; an archived portal is a subclass naming a domain and year range.

The pipeline invariants hold:

* ``fetch`` produces bytes only -- the frontier says *what* to fetch.
* ``parse`` and ``discover_links`` read archived bytes and the extraction graph
  version pinned when the instance was first used; no network, no model. The
  same bytes and version give the same drafts on every replay.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, ClassVar

from realestate.domain.archive import ArchivedDocument, ArchiveScope, DiscoveredLink
from realestate.domain.enums import RawDocumentKind, RuleDomain
from realestate.domain.exceptions import FetchError, UnrecognisedDocumentError
from realestate.domain.models import FetchContext, ListingDraft, RawPayload
from realestate.domain.ports.archive import CrawlFrontierRepository
from realestate.domain.ports.data_source import ArchiveDataSource
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.rules import RuleEngine, RuleGraphRepository
from realestate.domain.rules import ExtractionOutcome, RuleGraph
from realestate.infrastructure.archive.wayback import WaybackClient, WaybackSettings

#: Frontier rows pulled per query while fetching.
_FETCH_BATCH = 25


class WaybackDataSource(ArchiveDataSource):
    """Replays captures of one domain from the Wayback Machine."""

    #: Subclasses set these; ``params`` (``domain``, ``from_year``, ``to_year``) override.
    default_domain: ClassVar[str]
    default_from_year: ClassVar[int] = 2005
    default_to_year: ClassVar[int] = 2025
    default_max_fetches: ClassVar[int] = 200

    def __init__(
        self,
        *,
        log: LogProvider,
        params: Mapping[str, Any] | None = None,
        frontier: CrawlFrontierRepository,
        graphs: RuleGraphRepository,
        engine: RuleEngine,
        client: WaybackClient | None = None,
    ) -> None:
        self._log = log.bind(source_key=self.key)
        self._params = dict(params or {})
        self._frontier = frontier
        self._graphs = graphs
        self._engine = engine
        self._client = client or WaybackClient(
            WaybackSettings.from_params(self._params), log=self._log
        )
        self._graph: RuleGraph | None = None
        #: parse() and discover_links() see the same payload back to back.
        # Retain the object itself: id(payload) can be reused as soon as the
        # caller releases it, making a different page look like a cache hit.
        self._last: tuple[RawPayload, ExtractionOutcome | None] | None = None

    def archive_scope(self) -> ArchiveScope:
        return ArchiveScope(
            domain=str(self._params.get("domain", self.default_domain)),
            from_year=int(self._params.get("from_year", self.default_from_year)),
            to_year=int(self._params.get("to_year", self.default_to_year)),
        )

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
        budget = (
            ctx.max_items
            or ctx.page_limit
            or int(self._params.get("max_fetches_per_run", self.default_max_fetches))
        )
        fetched = 0
        attempted: set[int] = set()
        while fetched < budget:
            batch = [
                entry
                for entry in await self._frontier.next_queued(self.key, limit=_FETCH_BATCH)
                if entry.id not in attempted
            ]
            if not batch:
                return
            for entry in batch:
                if fetched >= budget:
                    return
                attempted.add(entry.id)
                try:
                    archived = await self._client.fetch_capture(entry.timestamp, entry.original_url)
                except FetchError as exc:
                    await self._frontier.mark_failed(entry.id, str(exc))
                    await self._log.warning(
                        "capture fetch failed", url=entry.original_url, error=str(exc)
                    )
                    continue
                yield RawPayload(
                    content=archived.content,
                    kind=RawDocumentKind.HTML,
                    content_type=archived.content_type,
                    source_url=entry.original_url,
                    meta={
                        "frontier_id": entry.id,
                        "url_key": entry.url_key,
                        "timestamp": archived.timestamp,
                        "captured_at": archived.captured_at.isoformat(),
                        "original_url": entry.original_url,
                        "replay_url": archived.replay_url,
                        "digest": entry.digest,
                        "page_kind_hint": entry.page_kind.value if entry.page_kind else None,
                        "route_node": entry.route_node,
                    },
                )
                # Resumed means the pipeline has archived the payload.
                await self._frontier.mark_fetched(entry.id)
                fetched += 1
                await self._log.info(
                    "capture fetched",
                    progress=f"{fetched}/{budget}",
                    url=entry.original_url,
                    captured=archived.timestamp,
                    bytes=len(archived.content),
                    kind=entry.page_kind.value if entry.page_kind else "?",
                    rule=entry.route_node,
                )

    async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]:
        graph = await self._extraction_graph()
        outcome = self._extract(payload, graph)
        if outcome is None:
            fingerprint = self._engine.fingerprint(_document(payload))
            raise UnrecognisedDocumentError(
                f"no extraction rule matched (graph v{graph.version}, fingerprint {fingerprint})"
            )
        return outcome.drafts

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
        outcome = self._engine.extract(graph, _document(payload), country_code=self.country_code)
        self._last = (payload, outcome)
        return outcome


def _document(payload: RawPayload) -> ArchivedDocument:
    return ArchivedDocument.from_payload(
        payload.content,
        content_type=payload.content_type,
        source_url=payload.source_url,
        meta=payload.meta,
    )
