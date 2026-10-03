"""Installing a source's curated extraction templates as a new graph version.

Induced templates adapt to unknown designs, but nothing in their validation
proves an id recipe or a price regex *right*. For designs already understood a
source ships hand-written templates (``ArchiveDataSource.extraction_seed``);
this service turns them into ``HUMAN``-origin states ahead of every existing
edge, optionally retiring induced states they supersede. The engine prefers a
curated template that works, so overlapping induced ones stop winning.

A plan is computed first and applied separately, so ``--dry-run`` can audit
the candidate graph before anything is saved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from realestate.domain.enums import NodeKind, RuleDomain, RuleOrigin
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.ports.data_source import ArchiveDataSource
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.rules import RuleGraphRepository
from realestate.domain.ports.source_registry import SourceRegistry
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode


@dataclass(slots=True)
class SeedPlan:
    current: RuleGraph
    candidate: RuleGraph
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)

    @property
    def changes(self) -> bool:
        return bool(self.added or self.replaced or self.retired)


class RuleSeedService:
    def __init__(
        self, *, registry: SourceRegistry, graphs: RuleGraphRepository, log: LogProvider
    ) -> None:
        self._registry = registry
        self._graphs = graphs
        self._log = log

    async def plan(self, source_key: str, *, retire: Sequence[str] = ()) -> SeedPlan:
        """The next extraction graph: curated templates first, ``retire`` removed."""
        source = self._registry.create(source_key)
        try:
            if not isinstance(source, ArchiveDataSource):
                raise ConfigurationError(f"source '{source_key}' is not an archive source")
            seed = source.extraction_seed()
        finally:
            await source.aclose()
        if seed is None:
            raise ConfigurationError(f"source '{source_key}' ships no curated templates")

        current = await self._graphs.active(source_key, RuleDomain.EXTRACTION) or RuleGraph.empty(
            source_key, RuleDomain.EXTRACTION
        )
        seed_keys = {template.key for template in seed.templates}
        clash = seed_keys & set(retire)
        if clash:
            raise ConfigurationError(f"cannot retire curated templates: {sorted(clash)}")
        missing = set(retire) - {node.key for node in current.nodes}
        if missing:
            raise ConfigurationError(f"no such states to retire: {sorted(missing)}")

        existing = {node.key: node for node in current.nodes}
        conditions = {edge.to_key: edge.condition for edge in current.edges}
        plan = SeedPlan(current=current, candidate=current, retired=list(retire))
        remove = set(retire)
        nodes: list[RuleNode] = []
        edges: list[RuleEdge] = []
        # Every curated edge goes ahead of every edge that stays, in file order.
        staying = [
            edge.priority
            for edge in current.edges
            if edge.from_key == ROOT_KEY and edge.to_key not in remove | seed_keys
        ]
        base = (min(staying) if staying else 100) - 10 * (len(seed.templates) + 1)
        for offset, template in enumerate(seed.templates):
            node = existing.get(template.key)
            if (
                node is not None
                and node.action == template.action
                and node.origin is RuleOrigin.HUMAN
                and conditions.get(template.key) == template.condition
                and _ahead(current, template.key, staying)
            ):
                plan.unchanged.append(template.key)
                continue
            if node is not None:
                remove.add(template.key)
                plan.replaced.append(template.key)
            else:
                plan.added.append(template.key)
            nodes.append(
                RuleNode(
                    key=template.key,
                    kind=NodeKind.TEMPLATE,
                    action=dict(template.action),
                    origin=RuleOrigin.HUMAN,
                )
            )
            edges.append(
                RuleEdge(
                    from_key=ROOT_KEY,
                    to_key=template.key,
                    condition=dict(template.condition),
                    priority=base + offset,
                )
            )
        if plan.changes:
            plan.candidate = current.revised(
                nodes=nodes,
                edges=edges,
                remove=sorted(remove),
                vocab=seed.vocab,
                notes=_notes(plan),
            )
        return plan

    async def apply(self, plan: SeedPlan) -> RuleGraph:
        if not plan.changes:
            return plan.current
        saved = await self._graphs.save_version(plan.candidate)
        await self._log.info(
            "curated templates installed",
            source_key=saved.source_key,
            version=saved.version,
            added=len(plan.added),
            replaced=len(plan.replaced),
            retired=len(plan.retired),
        )
        return saved


def _ahead(graph: RuleGraph, key: str, staying: list[int]) -> bool:
    """Whether ``key``'s edge is still tried before every edge that stays."""
    priorities = [edge.priority for edge in graph.edges if edge.to_key == key]
    return bool(priorities) and (not staying or max(priorities) < min(staying))


def _notes(plan: SeedPlan) -> str:
    parts = [f"curated: +{len(plan.added)}", f"replaced {len(plan.replaced)}"]
    if plan.retired:
        parts.append("retired " + ", ".join(plan.retired))
    return "; ".join(parts)
