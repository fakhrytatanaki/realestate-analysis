"""Installing a source's curated extraction templates as a new graph version.

Induced templates adapt to unknown designs, but nothing in their validation
proves an id recipe or a price regex *right*. For designs already understood a
source ships hand-written templates (``ArchiveDataSource.extraction_seed``);
this service turns them into ``HUMAN``-origin states ahead of every existing
edge, optionally retiring induced states they supersede. The engine prefers a
curated template that works, so overlapping induced ones stop winning.

The curated vocabulary goes first too. Entries it installs are tagged
``"origin": "HUMAN"``, so the next seed replaces them with whatever the source
now ships rather than keeping old patterns around; untagged entries were
induced and stay after the curated ones until retired by kind and pattern.
Order matters only when no text is decisive: an unambiguous match is found
whatever the order.

A plan is computed first and applied separately, so ``--dry-run`` can audit
the candidate graph before anything is saved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from realestate.domain.enums import NodeKind, RuleDomain, RuleOrigin
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.ports.data_source import ArchiveDataSource
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.rules import RuleGraphRepository
from realestate.domain.ports.source_registry import SourceRegistry
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode, is_curated_vocab

#: ``(kind, pattern)`` of a vocabulary entry, e.g. ``("listing_type", "sale|بيع")``.
type VocabKey = tuple[str, str]


@dataclass(slots=True)
class SeedPlan:
    current: RuleGraph
    candidate: RuleGraph
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    vocab_added: list[str] = field(default_factory=list)
    vocab_retired: list[str] = field(default_factory=list)
    #: The vocabulary changes, even if only in order or tags.
    vocab_changed: bool = False

    @property
    def changes(self) -> bool:
        return bool(self.added or self.replaced or self.retired or self.vocab_changed)


class RuleSeedService:
    def __init__(
        self, *, registry: SourceRegistry, graphs: RuleGraphRepository, log: LogProvider
    ) -> None:
        self._registry = registry
        self._graphs = graphs
        self._log = log

    async def plan(
        self,
        source_key: str,
        *,
        retire: Sequence[str] = (),
        retire_vocab: Sequence[VocabKey] = (),
    ) -> SeedPlan:
        """The next extraction graph: curated templates and vocabulary first.

        ``retire`` removes induced templates by key, ``retire_vocab`` induced
        vocabulary entries by ``(kind, pattern)``.
        """
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

        vocab, vocab_added, vocab_retired = _seed_vocab(current.vocab, seed.vocab, retire_vocab)
        existing = {node.key: node for node in current.nodes}
        conditions = {edge.to_key: edge.condition for edge in current.edges}
        plan = SeedPlan(
            current=current,
            candidate=current,
            retired=list(retire),
            vocab_added=vocab_added,
            vocab_retired=vocab_retired,
            vocab_changed=vocab != current.vocab,
        )
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
                vocab=vocab,
                replace_vocab=True,
                notes=_notes(plan),
            )
        return plan

    async def apply(self, plan: SeedPlan, *, evidence: str | None = None) -> RuleGraph:
        """Save the candidate; ``evidence`` (a replay diff's summary) joins its notes."""
        if not plan.changes:
            return plan.current
        candidate = plan.candidate
        if evidence:
            candidate = replace(candidate, notes=f"{candidate.notes}; {evidence}")
        saved = await self._graphs.save_version(candidate)
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


def _seed_vocab(
    current: Mapping[str, Sequence[Mapping[str, Any]]],
    shipped: Mapping[str, Sequence[Mapping[str, Any]]],
    retire: Sequence[VocabKey],
) -> tuple[dict[str, list[dict[str, Any]]], list[str], list[str]]:
    """``(vocabulary, entries added, entries retired)`` after seeding.

    Per kind: the shipped entries in file order, tagged curated, then the
    induced entries already there, minus retired ones and copies of shipped
    ones. Entries a previous seed installed are replaced, not kept.
    """
    curated = {
        kind: [
            {
                "pattern": str(entry["pattern"]),
                "value": str(entry["value"]),
                "origin": RuleOrigin.HUMAN.value,
            }
            for entry in entries
        ]
        for kind, entries in shipped.items()
    }
    retiring = set(retire)
    present = {
        (kind, str(entry.get("pattern"))) for kind, entries in current.items() for entry in entries
    }
    if missing := sorted(retiring - present):
        raise ConfigurationError(f"no such vocabulary entries to retire: {missing}")
    shipped_keys = {
        (kind, entry["pattern"]) for kind, entries in curated.items() for entry in entries
    }
    if clash := sorted(retiring & shipped_keys):
        raise ConfigurationError(f"cannot retire curated vocabulary: {clash}")
    vocab: dict[str, list[dict[str, Any]]] = {}
    for kind in [*curated, *(kind for kind in current if kind not in curated)]:
        entries = list(curated.get(kind, []))
        for entry in current.get(kind, []):
            if (
                is_curated_vocab(entry)
                or (kind, str(entry.get("pattern"))) in retiring
                or any(_same_entry(entry, other) for other in entries)
            ):
                continue
            entries.append(dict(entry))
        if entries:
            vocab[kind] = entries
    added = [
        _describe_entry(kind, entry)
        for kind, entries in curated.items()
        for entry in entries
        if not any(_same_entry(entry, other) for other in current.get(kind, []))
    ]
    retired = [
        _describe_entry(kind, entry)
        for kind, entries in current.items()
        for entry in entries
        if not any(_same_entry(entry, other) for other in vocab.get(kind, []))
    ]
    return vocab, added, retired


def _same_entry(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        str(left.get("pattern")) == str(right.get("pattern"))
        and str(left.get("value", "")).upper() == str(right.get("value", "")).upper()
    )


def _describe_entry(kind: str, entry: Mapping[str, Any]) -> str:
    return f"{kind}: {entry.get('pattern')} -> {entry.get('value')}"


def _notes(plan: SeedPlan) -> str:
    parts = [f"curated: +{len(plan.added)}", f"replaced {len(plan.replaced)}"]
    if plan.retired:
        parts.append("retired " + ", ".join(plan.retired))
    if plan.vocab_added or plan.vocab_retired:
        parts.append(f"vocabulary +{len(plan.vocab_added)} -{len(plan.vocab_retired)}")
    if plan.vocab_retired:
        parts.append("retired vocabulary " + "; ".join(plan.vocab_retired))
    return "; ".join(parts)
