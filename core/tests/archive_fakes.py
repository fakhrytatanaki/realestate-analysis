"""In-memory implementations of the archive and rule ports, plus fixture helpers."""

from __future__ import annotations

import gzip
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from realestate.domain.archive import ArchivedDocument, Capture, FrontierEntry, parse_timestamp
from realestate.domain.enums import (
    CrawlStatus,
    GapStatus,
    LinkRel,
    PageKind,
    RuleDomain,
    RuleGraphStatus,
)
from realestate.domain.ports.archive import (
    ArchiveIndex,
    CapturePage,
    CrawlCursorRepository,
    CrawlFrontierRepository,
)
from realestate.domain.ports.llm import LlmMessage, LlmResponse, StructuredLlm
from realestate.domain.ports.rules import (
    LlmDecisionRepository,
    RuleGapRepository,
    RuleGraphRepository,
)
from realestate.domain.rules import LlmDecision, RuleGap, RuleGraph

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wayback_olx_eg"
_INDEX = json.loads((FIXTURES / "index.json").read_text())
TEMPLATES: dict[str, Any] = json.loads((FIXTURES / "templates.json").read_text())


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


class InMemoryFrontier(CrawlFrontierRepository):
    def __init__(self) -> None:
        self.rows: dict[int, FrontierEntry] = {}
        self._next = 1

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

    async def next_queued(self, source_key: str, *, limit: int) -> list[FrontierEntry]:
        queued = [
            e
            for e in self.rows.values()
            if e.source_key == source_key and e.status is CrawlStatus.QUEUED
        ]
        queued.sort(key=lambda e: (-e.priority, e.timestamp, e.id))
        return queued[:limit]

    async def mark_fetched(self, entry_id: int) -> None:
        self.rows[entry_id] = replace(self.rows[entry_id], status=CrawlStatus.FETCHED)

    async def mark_failed(self, entry_id: int, error: str) -> None:
        entry = self.rows[entry_id]
        self.rows[entry_id] = replace(
            entry, status=CrawlStatus.FAILED, error=error, attempts=entry.attempts + 1
        )

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

    async def sample_urls(self, source_key: str, status: CrawlStatus, *, limit: int) -> list[str]:
        seen: dict[str, str] = {}
        for entry in self.rows.values():
            if entry.source_key == source_key and entry.status is status:
                seen.setdefault(entry.url_key, entry.original_url)
        return list(seen.values())[:limit]

    def by_status(self, status: CrawlStatus) -> list[FrontierEntry]:
        return [e for e in self.rows.values() if e.status is status]


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

    async def reset_open(self, source_key: str, domain: RuleDomain) -> None:
        for gap_id, gap in list(self.gaps.items()):
            if (
                gap.source_key == source_key
                and gap.domain is domain
                and gap.status is GapStatus.OPEN
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
            if gap.status is GapStatus.OPEN:
                gap = replace(
                    gap,
                    occurrences=gap.occurrences + occurrences,
                    samples=list(dict.fromkeys([*gap.samples, *samples]))[:max_samples],
                )
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

    async def record_failure(self, gap_id: UUID, error: str, *, max_attempts: int) -> RuleGap:
        gap = self.gaps[gap_id]
        attempts = gap.attempts + 1
        gap = replace(
            gap,
            attempts=attempts,
            last_error=error,
            status=GapStatus.FAILED if attempts >= max_attempts else gap.status,
        )
        self.gaps[gap_id] = gap
        return gap


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
        )
        self.decisions[decision.id] = decision
        return decision

    async def mark_valid(self, decision_id: UUID, valid: bool, error: str | None = None) -> None:
        self.decisions[decision_id] = replace(self.decisions[decision_id], valid=valid, error=error)

    async def totals(self, task_prefix: str | None = None) -> dict[str, int]:
        found = [
            d for d in self.decisions.values() if not task_prefix or d.task.startswith(task_prefix)
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

    def __init__(self, captures: Sequence[Capture]) -> None:
        self.captures = list(captures)
        self.requests: list[tuple[str, int, str | None]] = []

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
