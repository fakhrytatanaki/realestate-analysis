"""In-memory implementations of the archive and rule ports, plus fixture helpers."""

from __future__ import annotations

import gzip
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from realestate.domain.archive import (
    SERVED_STATUSES,
    ArchivedDocument,
    Capture,
    FrontierEntry,
    LinkRequest,
    capture_month,
    capture_quarter,
    parse_timestamp,
    stratified_order,
    surt_key,
)
from realestate.domain.enums import (
    CrawlStatus,
    GapStatus,
    LinkRel,
    LinkRequestStatus,
    PageKind,
    RuleDomain,
    RuleGraphStatus,
)
from realestate.domain.ports.archive import (
    ArchiveIndex,
    CapturePage,
    CrawlCursorRepository,
    CrawlFrontierRepository,
    LinkRequestRepository,
)
from realestate.domain.ports.llm import LlmMessage, LlmResponse, StructuredLlm
from realestate.domain.ports.rules import (
    LlmDecisionRepository,
    RuleGapRepository,
    RuleGraphRepository,
)
from realestate.domain.rules import LlmDecision, RuleGap, RuleGraph, failed_gap_outgrown

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wayback_olx_eg"
SRC = Path(__file__).resolve().parents[1] / "src"
_INDEX = json.loads((FIXTURES / "index.json").read_text())
TEMPLATES: dict[str, Any] = json.loads(
    (SRC / "realestate/infrastructure/sources/olx_eg_wayback/templates.json").read_text()
)


def fixture_names() -> list[str]:
    return list(_INDEX)


def fixture_bytes(name: str) -> bytes:
    return gzip.decompress((FIXTURES / f"{name}.html.gz").read_bytes())


def fixture_document(name: str) -> ArchivedDocument:
    entry = _INDEX[name]
    return ArchivedDocument(
        content=fixture_bytes(name),
        url=entry["url"],
        captured_at=parse_timestamp(entry["timestamp"]),
    )


def fixture_meta(name: str) -> dict[str, str]:
    return dict(_INDEX[name])


_ROUTED = (CrawlStatus.QUEUED, CrawlStatus.SKIPPED, CrawlStatus.DEFERRED)


class InMemoryFrontier(CrawlFrontierRepository):
    def __init__(self) -> None:
        self.rows: dict[int, FrontierEntry] = {}
        self._next = 1
        self.claimed_at: dict[int, datetime] = {}
        self.retry_at: dict[int, datetime] = {}

    async def add_captures(self, source_key: str, captures: Sequence[Capture]) -> int:
        existing = {(e.source_key, e.url_key, e.timestamp) for e in self.rows.values()}
        added = 0
        for capture in captures:
            if (source_key, capture.url_key, capture.timestamp) in existing:
                continue
            self.rows[self._next] = FrontierEntry(
                id=self._next,
                source_key=source_key,
                url_key=capture.url_key,
                timestamp=capture.timestamp,
                original_url=capture.original_url,
                digest=capture.digest,
                status=CrawlStatus.DISCOVERED,
            )
            existing.add((source_key, capture.url_key, capture.timestamp))
            self._next += 1
            added += 1
        return added

    async def url_keys_with_status(
        self, source_key: str, statuses: Sequence[CrawlStatus], *, limit: int
    ) -> list[str]:
        keys: dict[str, None] = {}
        for entry in self.rows.values():
            if entry.source_key == source_key and entry.status in statuses:
                keys.setdefault(entry.url_key, None)
        return list(keys)[:limit]

    async def captures_for(self, source_key: str, url_keys: Sequence[str]) -> list[FrontierEntry]:
        wanted = set(url_keys)
        return sorted(
            (e for e in self.rows.values() if e.source_key == source_key and e.url_key in wanted),
            key=lambda e: e.timestamp,
        )

    async def set_route(
        self,
        entry_ids: Sequence[int],
        *,
        status: CrawlStatus,
        route_node: str | None = None,
        priority: int = 0,
        page_kind: PageKind | None = None,
    ) -> None:
        for entry_id in entry_ids:
            self.rows[entry_id] = replace(
                self.rows[entry_id],
                status=status,
                route_node=route_node,
                priority=priority,
                page_kind=page_kind,
            )

    async def claim_queued(
        self,
        source_key: str,
        *,
        limit: int,
        now: datetime,
        per_quarter: int | None = None,
    ) -> list[FrontierEntry]:
        queued = [
            (e.id, e.timestamp, e.priority)
            for e in self.rows.values()
            if e.source_key == source_key
            and e.status is CrawlStatus.QUEUED
            and (e.id not in self.retry_at or self.retry_at[e.id] <= now)
        ]
        served: dict[str, int] = {}
        taken: dict[str, int] = {}
        for e in self.rows.values():
            if e.source_key != source_key or e.status not in SERVED_STATUSES:
                continue
            month = capture_month(e.timestamp)
            served[month] = served.get(month, 0) + 1
            if e.status is not CrawlStatus.FAILED:
                quarter = capture_quarter(e.timestamp)
                taken[quarter] = taken.get(quarter, 0) + 1
        ordered = stratified_order(
            queued, served=served, taken=taken, per_stratum=per_quarter
        )[:limit]
        for entry_id in ordered:
            self.rows[entry_id] = replace(self.rows[entry_id], status=CrawlStatus.FETCHING)
            self.claimed_at[entry_id] = now
        return [self.rows[entry_id] for entry_id in ordered]

    async def release_stale_claims(self, source_key: str, *, claimed_before: datetime) -> int:
        released = 0
        for entry_id, entry in list(self.rows.items()):
            if (
                entry.source_key == source_key
                and entry.status is CrawlStatus.FETCHING
                and self.claimed_at.get(entry_id, claimed_before) <= claimed_before
            ):
                self.rows[entry_id] = replace(entry, status=CrawlStatus.QUEUED)
                self.claimed_at.pop(entry_id, None)
                released += 1
        return released

    async def mark_fetched(self, entry_id: int) -> None:
        self.rows[entry_id] = replace(self.rows[entry_id], status=CrawlStatus.FETCHED)
        self.claimed_at.pop(entry_id, None)

    async def mark_failed(
        self, entry_id: int, error: str, *, retry_at: datetime | None = None
    ) -> None:
        entry = self.rows[entry_id]
        self.claimed_at.pop(entry_id, None)
        if retry_at is not None:
            self.retry_at[entry_id] = retry_at
        else:
            self.retry_at.pop(entry_id, None)
        self.rows[entry_id] = replace(
            entry,
            status=CrawlStatus.QUEUED if retry_at is not None else CrawlStatus.FAILED,
            error=error,
            attempts=entry.attempts + 1,
        )

    async def known_url_keys(self, source_key: str, url_keys: Sequence[str]) -> set[str]:
        present = {e.url_key for e in self.rows.values() if e.source_key == source_key}
        return {key for key in url_keys if key in present}

    async def captures_with_status(
        self, source_key: str, statuses: Sequence[CrawlStatus]
    ) -> list[tuple[int, str]]:
        return [
            (e.id, e.timestamp)
            for e in self.rows.values()
            if e.source_key == source_key and e.status in statuses
        ]

    async def reopen_capped(self, source_key: str) -> int:
        reopened = 0
        for entry_id, entry in list(self.rows.items()):
            if (
                entry.source_key == source_key
                and entry.status is CrawlStatus.SKIPPED
                and (entry.route_node or "").endswith("#capture-cap")
            ):
                self.rows[entry_id] = replace(
                    entry, status=CrawlStatus.DISCOVERED, route_node=None
                )
                reopened += 1
        return reopened

    async def routed_nodes(self, source_key: str) -> set[str]:
        return {
            e.route_node
            for e in self.rows.values()
            if e.source_key == source_key and e.status in _ROUTED and e.route_node
        }

    async def reopen_routed_by(self, source_key: str, route_nodes: Sequence[str]) -> int:
        reopened = 0
        for entry_id, entry in list(self.rows.items()):
            if (
                entry.source_key == source_key
                and entry.status in _ROUTED
                and entry.route_node in route_nodes
            ):
                self.rows[entry_id] = replace(
                    entry,
                    status=CrawlStatus.DISCOVERED,
                    route_node=None,
                    priority=0,
                    page_kind=None,
                )
                reopened += 1
        return reopened

    async def url_keys_page(self, source_key: str, *, after: str | None, limit: int) -> list[str]:
        keys = sorted({e.url_key for e in self.rows.values() if e.source_key == source_key})
        return [key for key in keys if after is None or key > after][:limit]

    async def status_by_quarter(self, source_key: str) -> dict[tuple[str, CrawlStatus], int]:
        counts: dict[tuple[str, CrawlStatus], int] = {}
        for entry in self.rows.values():
            if entry.source_key == source_key:
                key = (capture_quarter(entry.timestamp), entry.status)
                counts[key] = counts.get(key, 0) + 1
        return counts

    async def add_evidence(self, source_key: str, rels_by_url_key: Mapping[str, LinkRel]) -> int:
        matched: set[str] = set()
        for entry_id, entry in list(self.rows.items()):
            rel = rels_by_url_key.get(entry.url_key)
            if rel is None or entry.source_key != source_key:
                continue
            matched.add(entry.url_key)
            rels = list(entry.evidence.get("linked_as") or [])
            if rel.value in rels:
                continue
            status = entry.status
            if status in (CrawlStatus.UNROUTED, CrawlStatus.DEFERRED, CrawlStatus.SKIPPED):
                status = CrawlStatus.DISCOVERED
            self.rows[entry_id] = replace(
                entry,
                evidence={**entry.evidence, "linked_as": sorted({*rels, rel.value})},
                status=status,
            )
        return len(matched)

    async def reset_status(
        self, source_key: str, *, from_status: CrawlStatus, to_status: CrawlStatus
    ) -> int:
        moved = 0
        for entry_id, entry in list(self.rows.items()):
            if entry.source_key == source_key and entry.status is from_status:
                self.rows[entry_id] = replace(entry, status=to_status)
                moved += 1
        return moved

    async def counts(self, source_key: str) -> dict[CrawlStatus, int]:
        out = dict.fromkeys(CrawlStatus, 0)
        for entry in self.rows.values():
            if entry.source_key == source_key:
                out[entry.status] += 1
        return out

    async def spread_urls(self, source_key: str, *, limit: int) -> list[str]:
        rows = [e for e in self.rows.values() if e.source_key == source_key]
        step = max(1, len(rows) // limit) if limit > 0 else len(rows) + 1
        return [entry.original_url for entry in rows[::step]][:limit]

    async def sample_urls(self, source_key: str, status: CrawlStatus, *, limit: int) -> list[str]:
        seen: dict[str, str] = {}
        for entry in self.rows.values():
            if entry.source_key == source_key and entry.status is status:
                seen.setdefault(entry.url_key, entry.original_url)
        return list(seen.values())[:limit]

    def by_status(self, status: CrawlStatus) -> list[FrontierEntry]:
        return [e for e in self.rows.values() if e.status is status]


class InMemoryLinkRequests(LinkRequestRepository):
    def __init__(self) -> None:
        self.rows: dict[int, LinkRequest] = {}
        self.found: dict[int, int] = {}

    async def request(self, requests: Sequence[LinkRequest]) -> int:
        known = {(r.source_key, r.url_key) for r in self.rows.values()}
        added = 0
        for request in requests:
            if (request.source_key, request.url_key) in known:
                continue
            request_id = len(self.rows) + 1
            self.rows[request_id] = replace(request, id=request_id)
            known.add((request.source_key, request.url_key))
            added += 1
        return added

    async def pending(self, source_key: str, *, limit: int) -> list[LinkRequest]:
        rank = {LinkRel.DETAIL: 0, LinkRel.LIST: 1, LinkRel.PAGINATION: 2}
        waiting = [
            r
            for r in self.rows.values()
            if r.source_key == source_key and r.status is LinkRequestStatus.PENDING
        ]
        return sorted(waiting, key=lambda r: (rank[r.rel], r.id or 0))[:limit]

    async def resolve(
        self,
        request_id: int,
        *,
        status: LinkRequestStatus,
        captures_found: int = 0,
        error: str | None = None,
    ) -> None:
        row = self.rows[request_id]
        self.rows[request_id] = replace(row, status=status, attempts=row.attempts + 1)
        self.found[request_id] = captures_found

    async def counts(self, source_key: str) -> dict[LinkRequestStatus, int]:
        out = dict.fromkeys(LinkRequestStatus, 0)
        for row in self.rows.values():
            if row.source_key == source_key:
                out[row.status] += 1
        return out


class InMemoryCursors(CrawlCursorRepository):
    def __init__(self) -> None:
        self.state: dict[tuple[str, str], tuple[str | None, bool]] = {}

    async def get(self, source_key: str, scope: str) -> tuple[str | None, bool]:
        return self.state.get((source_key, scope), (None, False))

    async def save(
        self, source_key: str, scope: str, *, resume_key: str | None, done: bool
    ) -> None:
        self.state[(source_key, scope)] = (resume_key, done)


class InMemoryGraphs(RuleGraphRepository):
    def __init__(self) -> None:
        self.saved: list[RuleGraph] = []

    async def active(self, source_key: str, domain: RuleDomain) -> RuleGraph | None:
        for graph in reversed(self.saved):
            if (
                graph.source_key == source_key
                and graph.domain is domain
                and graph.status is RuleGraphStatus.ACTIVE
            ):
                return graph
        return None

    async def get(self, source_key: str, domain: RuleDomain, version: int) -> RuleGraph | None:
        for graph in self.saved:
            if (graph.source_key, graph.domain, graph.version) == (source_key, domain, version):
                return graph
        return None

    async def save_version(self, graph: RuleGraph) -> RuleGraph:
        self.saved = [
            replace(g, status=RuleGraphStatus.RETIRED)
            if g.source_key == graph.source_key and g.domain is graph.domain
            else g
            for g in self.saved
        ]
        stored = replace(graph, id=uuid4(), status=RuleGraphStatus.ACTIVE)
        self.saved.append(stored)
        return stored

    async def versions(self, source_key: str, domain: RuleDomain) -> list[RuleGraph]:
        return sorted(
            (g for g in self.saved if g.source_key == source_key and g.domain is domain),
            key=lambda g: -g.version,
        )


class InMemoryGaps(RuleGapRepository):
    def __init__(self) -> None:
        self.gaps: dict[UUID, RuleGap] = {}

    def _find(self, source_key: str, domain: RuleDomain, fingerprint: str) -> RuleGap | None:
        for gap in self.gaps.values():
            if (gap.source_key, gap.domain, gap.fingerprint) == (source_key, domain, fingerprint):
                return gap
        return None

    async def reset_counts(self, source_key: str, domain: RuleDomain) -> None:
        for gap_id, gap in list(self.gaps.items()):
            if (
                gap.source_key == source_key
                and gap.domain is domain
                and gap.status is not GapStatus.RESOLVED
            ):
                self.gaps[gap_id] = replace(gap, occurrences=0)

    async def record(
        self,
        source_key: str,
        domain: RuleDomain,
        fingerprint: str,
        *,
        samples: Sequence[str],
        occurrences: int = 1,
        max_samples: int = 8,
    ) -> RuleGap:
        gap = self._find(source_key, domain, fingerprint)
        if gap is None:
            gap = RuleGap(
                id=uuid4(),
                source_key=source_key,
                domain=domain,
                fingerprint=fingerprint,
                status=GapStatus.OPEN,
                occurrences=occurrences,
                samples=list(dict.fromkeys(samples))[:max_samples],
            )
        else:
            if gap.status is GapStatus.RESOLVED:
                gap = replace(gap, status=GapStatus.OPEN, occurrences=0)
            gap = replace(
                gap,
                occurrences=gap.occurrences + occurrences,
                samples=list(dict.fromkeys([*samples, *gap.samples]))[:max_samples],
            )
            if gap.status is GapStatus.FAILED and failed_gap_outgrown(
                gap.occurrences, gap.failed_occurrences
            ):
                gap = replace(gap, status=GapStatus.OPEN, attempts=0)
        self.gaps[gap.id] = gap
        return gap

    async def list(
        self,
        source_key: str,
        domain: RuleDomain | None = None,
        *,
        status: GapStatus | None = None,
        limit: int = 100,
    ) -> list[RuleGap]:
        found = [
            gap
            for gap in self.gaps.values()
            if gap.source_key == source_key
            and (domain is None or gap.domain is domain)
            and (status is None or gap.status is status)
        ]
        return sorted(found, key=lambda gap: -gap.occurrences)[:limit]

    async def mark_resolved(self, gap_id: UUID, *, version: int) -> None:
        self.gaps[gap_id] = replace(
            self.gaps[gap_id], status=GapStatus.RESOLVED, resolved_version=version
        )

    async def record_failure(
        self, gap_id: UUID, error: str, *, max_attempts: int, attempted_with: str | None = None
    ) -> RuleGap:
        gap = self.gaps[gap_id]
        attempts = gap.attempts + 1
        failed = attempts >= max_attempts
        gap = replace(
            gap,
            attempts=attempts,
            last_error=error,
            attempted_with=attempted_with if attempted_with is not None else gap.attempted_with,
            status=GapStatus.FAILED if failed else gap.status,
            failed_occurrences=gap.occurrences if failed else gap.failed_occurrences,
        )
        self.gaps[gap_id] = gap
        return gap

    async def mark_needs_human(self, gap_id: UUID, reason: str) -> None:
        self.gaps[gap_id] = replace(
            self.gaps[gap_id], status=GapStatus.NEEDS_HUMAN, last_error=reason
        )

    async def reopen_failed(self, source_key: str, domain: RuleDomain | None = None) -> int:
        reopened = 0
        for gap_id, gap in list(self.gaps.items()):
            if (
                gap.source_key == source_key
                and gap.status in (GapStatus.FAILED, GapStatus.NEEDS_HUMAN)
                and (domain is None or gap.domain is domain)
            ):
                self.gaps[gap_id] = replace(gap, status=GapStatus.OPEN, attempts=0)
                reopened += 1
        return reopened

    async def reopen_superseded(
        self, source_key: str, domain: RuleDomain, *, attempted_with: str
    ) -> int:
        reopened = 0
        for gap_id, gap in list(self.gaps.items()):
            if (
                gap.source_key == source_key
                and gap.domain is domain
                and gap.status is GapStatus.FAILED
                and gap.attempted_with is not None
                and gap.attempted_with != attempted_with
            ):
                self.gaps[gap_id] = replace(gap, status=GapStatus.OPEN, attempts=0)
                reopened += 1
        return reopened


class InMemoryDecisions(LlmDecisionRepository):
    def __init__(self) -> None:
        self.decisions: dict[UUID, LlmDecision] = {}

    async def find_valid(
        self, task: str, input_fp: str, model: str, prompt_version: str
    ) -> LlmDecision | None:
        for decision in self.decisions.values():
            if (decision.task, decision.input_fp, decision.model, decision.prompt_version) == (
                task,
                input_fp,
                model,
                prompt_version,
            ) and decision.valid:
                return decision
        return None

    async def record(self, **kwargs: Any) -> LlmDecision:
        decision = LlmDecision(
            id=uuid4(),
            task=kwargs["task"],
            input_fp=kwargs["input_fp"],
            model=kwargs["model"],
            prompt_version=kwargs["prompt_version"],
            response=dict(kwargs["response"]) if kwargs.get("response") is not None else None,
            valid=kwargs["valid"],
            raw_text=kwargs.get("raw_text", ""),
            error=kwargs.get("error"),
            tokens_in=kwargs.get("tokens_in", 0),
            tokens_out=kwargs.get("tokens_out", 0),
            source_key=kwargs.get("source_key"),
        )
        self.decisions[decision.id] = decision
        return decision

    async def mark_valid(self, decision_id: UUID, valid: bool, error: str | None = None) -> None:
        self.decisions[decision_id] = replace(self.decisions[decision_id], valid=valid, error=error)

    async def totals(
        self, task_prefix: str | None = None, *, source_key: str | None = None
    ) -> dict[str, int]:
        found = [
            d
            for d in self.decisions.values()
            if (not task_prefix or d.task.startswith(task_prefix))
            and (source_key is None or d.source_key == source_key)
        ]
        return {
            "calls": len(found),
            "valid": sum(1 for d in found if d.valid),
            "tokens_in": sum(d.tokens_in for d in found),
            "tokens_out": sum(d.tokens_out for d in found),
        }


class ScriptedLlm(StructuredLlm):
    """Answers from a queue; records every prompt it was shown."""

    def __init__(
        self,
        answers: Sequence[dict[str, Any] | None] = (),
        *,
        by_tool: Mapping[str, Sequence[Any]] | None = None,
    ) -> None:
        self.answers = list(answers)
        self.by_tool = {tool: list(queue) for tool, queue in (by_tool or {}).items()}
        self.calls: list[tuple[str, list[LlmMessage]]] = []

    @property
    def model(self) -> str:
        return "scripted"

    async def complete(
        self,
        messages: Sequence[LlmMessage],
        *,
        schema: Mapping[str, Any],
        tool_name: str,
        tool_description: str,
    ) -> LlmResponse:
        self.calls.append((tool_name, list(messages)))
        queue = self.by_tool.get(tool_name, self.answers)
        data = queue.pop(0) if queue else None
        if callable(data):
            data = data(messages)
        return LlmResponse(
            data=data, raw_text=json.dumps(data), model=self.model, tokens_in=100, tokens_out=50
        )


class FixtureIndex(ArchiveIndex):
    """An archive index serving a fixed list of captures, two pages per year."""

    def __init__(self, captures: Sequence[Capture], hidden: Sequence[Capture] = ()) -> None:
        self.captures = list(captures)
        #: Captures the domain enumeration does not return, found only by exact lookup.
        self.hidden = list(hidden)
        self.requests: list[tuple[str, int, str | None]] = []
        self.lookups: list[tuple[str, str | None]] = []

    async def iter_captures(
        self, domain: str, year: int, *, resume_key: str | None = None, page_size: int = 5000
    ) -> AsyncIterator[CapturePage]:
        rows = [c for c in self.captures if c.timestamp.startswith(str(year))]
        start = int(resume_key or 0)
        while True:
            self.requests.append((domain, year, resume_key))
            page = rows[start : start + page_size]
            start += page_size
            next_key = str(start) if start < len(rows) else None
            yield CapturePage(captures=page, resume_key=next_key)
            if next_key is None:
                return
            resume_key = next_key

    async def lookup(
        self,
        url: str,
        *,
        around: str | None = None,
        window_days: int = 183,
        limit: int = 5,
    ) -> list[Capture]:
        self.lookups.append((url, around))
        key = surt_key(url)
        found = [c for c in self.hidden if c.url_key == key]
        if around:
            centre = parse_timestamp(around)
            found = [
                c
                for c in found
                if abs((parse_timestamp(c.timestamp) - centre).days) <= window_days
            ]
            found.sort(key=lambda c: abs((parse_timestamp(c.timestamp) - centre).total_seconds()))
        return found[:limit]
