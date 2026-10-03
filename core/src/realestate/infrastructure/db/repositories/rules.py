"""Tortoise-backed rule graphs, gaps and the LLM decision ledger."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

from tortoise.transactions import in_transaction

from realestate.domain.enums import GapStatus, RuleDomain, RuleGraphStatus
from realestate.domain.ports.rules import (
    LlmDecisionRepository,
    RuleGapRepository,
    RuleGraphRepository,
)
from realestate.domain.rules import (
    LlmDecision,
    RuleEdge,
    RuleGap,
    RuleGraph,
    RuleNode,
    failed_gap_outgrown,
)
from realestate.infrastructure.db.models import (
    LlmDecisionModel,
    RuleEdgeModel,
    RuleGapModel,
    RuleGraphModel,
    RuleNodeModel,
)


async def _load(row: RuleGraphModel) -> RuleGraph:
    nodes = await RuleNodeModel.filter(graph_id=row.id).order_by("position")
    edges = await RuleEdgeModel.filter(graph_id=row.id).order_by("position")
    return RuleGraph(
        id=row.id,
        source_key=row.source_key,
        domain=row.domain,
        version=row.version,
        status=row.status,
        parent_id=row.parent_id,
        vocab=row.vocab or {},
        notes=row.notes,
        created_at=row.created_at,
        nodes=tuple(
            RuleNode(
                key=node.key,
                kind=node.kind,
                action=node.action or {},
                origin=node.origin,
                llm_decision_id=node.llm_decision_id,
            )
            for node in nodes
        ),
        edges=tuple(
            RuleEdge(
                from_key=edge.from_key,
                to_key=edge.to_key,
                condition=edge.condition or {},
                priority=edge.priority,
            )
            for edge in edges
        ),
    )


class TortoiseRuleGraphRepository(RuleGraphRepository):
    async def active(self, source_key: str, domain: RuleDomain) -> RuleGraph | None:
        row = (
            await RuleGraphModel.filter(
                source_key=source_key, domain=domain, status=RuleGraphStatus.ACTIVE
            )
            .order_by("-version")
            .first()
        )
        return await _load(row) if row else None

    async def get(self, source_key: str, domain: RuleDomain, version: int) -> RuleGraph | None:
        row = await RuleGraphModel.get_or_none(
            source_key=source_key, domain=domain, version=version
        )
        return await _load(row) if row else None

    async def save_version(self, graph: RuleGraph) -> RuleGraph:
        graph_id = uuid4()
        async with in_transaction():
            await RuleGraphModel.filter(
                source_key=graph.source_key, domain=graph.domain, status=RuleGraphStatus.ACTIVE
            ).update(status=RuleGraphStatus.RETIRED)
            await RuleGraphModel.create(
                id=graph_id,
                source_key=graph.source_key,
                domain=graph.domain,
                version=graph.version,
                status=RuleGraphStatus.ACTIVE,
                parent_id=graph.parent_id,
                vocab=graph.vocab,
                notes=graph.notes,
            )
            await RuleNodeModel.bulk_create(
                [
                    RuleNodeModel(
                        id=uuid4(),
                        graph_id=graph_id,
                        key=node.key,
                        kind=node.kind,
                        action=node.action,
                        origin=node.origin,
                        llm_decision_id=node.llm_decision_id,
                        position=position,
                    )
                    for position, node in enumerate(graph.nodes)
                ]
            )
            if graph.edges:
                await RuleEdgeModel.bulk_create(
                    [
                        RuleEdgeModel(
                            id=uuid4(),
                            graph_id=graph_id,
                            from_key=edge.from_key,
                            to_key=edge.to_key,
                            condition=edge.condition,
                            priority=edge.priority,
                            position=position,
                        )
                        for position, edge in enumerate(graph.edges)
                    ]
                )
        saved = await self.get(graph.source_key, graph.domain, graph.version)
        assert saved is not None
        return saved

    async def versions(self, source_key: str, domain: RuleDomain) -> list[RuleGraph]:
        rows = await RuleGraphModel.filter(source_key=source_key, domain=domain).order_by(
            "-version"
        )
        return [await _load(row) for row in rows]


def _to_gap(row: RuleGapModel) -> RuleGap:
    return RuleGap(
        id=row.id,
        source_key=row.source_key,
        domain=row.domain,
        fingerprint=row.fingerprint,
        status=row.status,
        occurrences=row.occurrences,
        samples=list(row.samples or []),
        attempts=row.attempts,
        last_error=row.last_error,
        resolved_version=row.resolved_version,
        attempted_with=row.attempted_with,
        failed_occurrences=row.failed_occurrences,
    )


#: Gaps whose counts are a live backlog, recounted from zero on every collection.
_COUNTED = (GapStatus.OPEN, GapStatus.FAILED, GapStatus.NEEDS_HUMAN)


class TortoiseRuleGapRepository(RuleGapRepository):
    async def reset_counts(self, source_key: str, domain: RuleDomain) -> None:
        await RuleGapModel.filter(
            source_key=source_key, domain=domain, status__in=list(_COUNTED)
        ).update(occurrences=0)

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
        fingerprint = fingerprint[:512]
        row = await RuleGapModel.get_or_none(
            source_key=source_key, domain=domain, fingerprint=fingerprint
        )
        if row is None:
            row = await RuleGapModel.create(
                id=uuid4(),
                source_key=source_key,
                domain=domain,
                fingerprint=fingerprint,
                occurrences=occurrences,
                samples=list(dict.fromkeys(samples))[:max_samples],
            )
            return _to_gap(row)
        if row.status is GapStatus.RESOLVED:
            # The rule that resolved it does not cover these: ask again.
            row.status = GapStatus.OPEN
            row.occurrences = 0
        # Unresolved gaps keep counting, so the count is the live backlog and
        # a FAILED gap is not a black hole for every later input like it.
        row.occurrences += occurrences
        # Current inputs first: older samples may be handled by now.
        row.samples = list(dict.fromkeys([*samples, *(row.samples or [])]))[:max_samples]
        if row.status is GapStatus.FAILED and failed_gap_outgrown(
            row.occurrences, row.failed_occurrences
        ):
            row.status = GapStatus.OPEN
            row.attempts = 0
        await row.save()
        return _to_gap(row)

    async def list(
        self,
        source_key: str,
        domain: RuleDomain | None = None,
        *,
        status: GapStatus | None = None,
        limit: int = 100,
    ) -> list[RuleGap]:
        queryset = RuleGapModel.filter(source_key=source_key)
        if domain is not None:
            queryset = queryset.filter(domain=domain)
        if status is not None:
            queryset = queryset.filter(status=status)
        rows = await queryset.order_by("-occurrences", "created_at").limit(limit)
        return [_to_gap(row) for row in rows]

    async def mark_resolved(self, gap_id: UUID, *, version: int) -> None:
        await RuleGapModel.filter(id=gap_id).update(
            status=GapStatus.RESOLVED, resolved_version=version, last_error=None
        )

    async def record_failure(
        self, gap_id: UUID, error: str, *, max_attempts: int, attempted_with: str | None = None
    ) -> RuleGap:
        row = await RuleGapModel.get(id=gap_id)
        row.attempts += 1
        row.last_error = error[:4000]
        if attempted_with is not None:
            row.attempted_with = attempted_with[:255]
        if row.attempts >= max_attempts:
            row.status = GapStatus.FAILED
            row.failed_occurrences = row.occurrences
        await row.save()
        return _to_gap(row)

    async def mark_needs_human(self, gap_id: UUID, reason: str) -> None:
        await RuleGapModel.filter(id=gap_id).update(
            status=GapStatus.NEEDS_HUMAN, last_error=reason[:4000]
        )

    async def reopen_failed(self, source_key: str, domain: RuleDomain | None = None) -> int:
        queryset = RuleGapModel.filter(
            source_key=source_key, status__in=[GapStatus.FAILED, GapStatus.NEEDS_HUMAN]
        )
        if domain is not None:
            queryset = queryset.filter(domain=domain)
        return await queryset.update(status=GapStatus.OPEN, attempts=0)

    async def reopen_superseded(
        self, source_key: str, domain: RuleDomain, *, attempted_with: str
    ) -> int:
        return (
            await RuleGapModel.filter(
                source_key=source_key,
                domain=domain,
                status=GapStatus.FAILED,
                attempted_with__isnull=False,
            )
            .exclude(attempted_with=attempted_with[:255])
            .update(status=GapStatus.OPEN, attempts=0)
        )


def _to_decision(row: LlmDecisionModel) -> LlmDecision:
    return LlmDecision(
        id=row.id,
        task=row.task,
        input_fp=row.input_fp,
        model=row.model,
        prompt_version=row.prompt_version,
        response=row.response,
        valid=row.valid,
        raw_text=row.raw_text,
        error=row.error,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        latency_ms=row.latency_ms,
        created_at=row.created_at,
        source_key=row.source_key,
    )


class TortoiseLlmDecisionRepository(LlmDecisionRepository):
    async def find_valid(
        self, task: str, input_fp: str, model: str, prompt_version: str
    ) -> LlmDecision | None:
        row = (
            await LlmDecisionModel.filter(
                task=task, input_fp=input_fp, model=model, prompt_version=prompt_version, valid=True
            )
            .order_by("-created_at")
            .first()
        )
        return _to_decision(row) if row else None

    async def record(
        self,
        *,
        task: str,
        input_fp: str,
        model: str,
        prompt_version: str,
        request: Mapping[str, Any],
        response: Mapping[str, Any] | None,
        raw_text: str,
        valid: bool,
        error: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        source_key: str | None = None,
    ) -> LlmDecision:
        row = await LlmDecisionModel.create(
            id=uuid4(),
            source_key=source_key,
            task=task,
            input_fp=input_fp,
            model=model,
            prompt_version=prompt_version,
            request=dict(request),
            response=dict(response) if response is not None else None,
            raw_text=raw_text[:200_000],
            valid=valid,
            error=error[:4000] if error else None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
        return _to_decision(row)

    async def mark_valid(self, decision_id: UUID, valid: bool, error: str | None = None) -> None:
        await LlmDecisionModel.filter(id=decision_id).update(
            valid=valid, error=error[:4000] if error else None
        )

    async def totals(
        self, task_prefix: str | None = None, *, source_key: str | None = None
    ) -> dict[str, int]:
        queryset = LlmDecisionModel.all()
        if task_prefix:
            queryset = queryset.filter(task__startswith=task_prefix)
        if source_key:
            queryset = queryset.filter(source_key=source_key)
        rows = await queryset.values_list("valid", "tokens_in", "tokens_out")
        return {
            "calls": len(rows),
            "valid": sum(1 for valid, _, _ in rows if valid),
            "tokens_in": sum(int(tokens_in or 0) for _, tokens_in, _ in rows),
            "tokens_out": sum(int(tokens_out or 0) for _, _, tokens_out in rows),
        }
