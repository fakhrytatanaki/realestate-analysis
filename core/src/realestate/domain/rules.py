"""Rule graphs: the state machine that caches LLM decisions.

A graph is a set of states (:class:`RuleNode`) joined by guarded transitions
(:class:`RuleEdge`). Walking it from ``root`` tries each outgoing edge in
priority order; an edge whose condition holds leads to the next state, and a
state with no outgoing edges is terminal and carries the action -- a navigation
decision or an extraction recipe.

A walk that reaches no terminal state is a *miss*. Misses are what rule
induction turns into new states, after which the same input is a *hit* that no
longer needs a model. Graph versions are immutable, so a replay with a given
version is deterministic.

Conditions and actions are plain JSON-compatible dicts: they are stored as
JSONB, written by an LLM, and interpreted by the rule engine in the
infrastructure layer. The domain only defines the structure and the walk.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from realestate.domain.archive import DiscoveredLink
from realestate.domain.enums import (
    GapStatus,
    NodeKind,
    PageKind,
    RouteDecision,
    RuleDomain,
    RuleGraphStatus,
    RuleOrigin,
)
from realestate.domain.models import ListingDraft

#: Key of the start state every graph has.
ROOT_KEY = "root"

#: A condition evaluator: given a condition dict, does it hold for the input?
ConditionEvaluator = Callable[[Mapping[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class RuleNode:
    """One state."""

    key: str
    kind: NodeKind
    action: dict[str, Any] = field(default_factory=dict)
    origin: RuleOrigin = RuleOrigin.SEED
    llm_decision_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RuleEdge:
    """A guarded transition. Lower ``priority`` is tried first."""

    from_key: str
    to_key: str
    condition: dict[str, Any]
    priority: int = 100


@dataclass(frozen=True, slots=True)
class RuleGraph:
    """One immutable version of a source's rules for one decision domain."""

    source_key: str
    domain: RuleDomain
    version: int
    nodes: tuple[RuleNode, ...]
    edges: tuple[RuleEdge, ...]
    #: Vocabulary shared by every template: ``{"listing_type": [{"pattern": ..,
    #: "value": ..}], "property_type": [...]}``. Versioned with the graph so
    #: replays stay deterministic.
    vocab: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    status: RuleGraphStatus = RuleGraphStatus.ACTIVE
    id: UUID | None = None
    parent_id: UUID | None = None
    created_at: datetime | None = None
    notes: str | None = None

    @classmethod
    def empty(cls, source_key: str, domain: RuleDomain) -> RuleGraph:
        """Version 0: just a root, so every input is a miss."""
        return cls(
            source_key=source_key,
            domain=domain,
            version=0,
            nodes=(RuleNode(key=ROOT_KEY, kind=NodeKind.ROOT),),
            edges=(),
        )

    def node(self, key: str) -> RuleNode:
        for node in self.nodes:
            if node.key == key:
                return node
        raise KeyError(key)

    def has_node(self, key: str) -> bool:
        return any(node.key == key for node in self.nodes)

    def outgoing(self, key: str) -> list[RuleEdge]:
        """Edges leaving ``key`` in evaluation order (priority, then age)."""
        indexed = [(edge.priority, index, edge) for index, edge in enumerate(self.edges)]
        return [edge for _, _, edge in sorted(indexed) if edge.from_key == key]

    def terminals(self) -> list[RuleNode]:
        return [node for node in self.nodes if node.kind in (NodeKind.ROUTE, NodeKind.TEMPLATE)]

    def candidates(self, evaluate: ConditionEvaluator) -> Iterator[tuple[RuleNode, list[str]]]:
        """Terminal states reachable through holding conditions, best first.

        Depth-first in edge priority order. The caller tries each candidate and
        moves on when one's action yields nothing (an extraction template that
        finds no items falls through to the next), which is what makes a
        too-generic early rule recoverable.
        """
        yield from self._walk(ROOT_KEY, evaluate, [ROOT_KEY], set())

    def _walk(
        self,
        key: str,
        evaluate: ConditionEvaluator,
        path: list[str],
        visiting: set[str],
    ) -> Iterator[tuple[RuleNode, list[str]]]:
        if key in visiting:  # a malformed cyclic graph must not hang a parse
            return
        visiting = visiting | {key}
        for edge in self.outgoing(key):
            if not self.has_node(edge.to_key) or not evaluate(edge.condition):
                continue
            child = self.node(edge.to_key)
            child_path = [*path, child.key]
            if child.kind in (NodeKind.ROUTE, NodeKind.TEMPLATE):
                yield child, child_path
            else:
                yield from self._walk(child.key, evaluate, child_path, visiting)

    def extended(
        self,
        *,
        nodes: Sequence[RuleNode] = (),
        edges: Sequence[RuleEdge] = (),
        vocab: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        notes: str | None = None,
    ) -> RuleGraph:
        """The next version: this graph plus new states, edges and vocabulary.

        Node keys must be unique; vocabulary entries already present are not
        duplicated.
        """
        existing = {node.key for node in self.nodes}
        clashes = existing & {node.key for node in nodes}
        if clashes:
            raise ValueError(f"node keys already exist: {sorted(clashes)}")
        merged_vocab = {key: list(entries) for key, entries in self.vocab.items()}
        for key, entries in (vocab or {}).items():
            bucket = merged_vocab.setdefault(key, [])
            for entry in entries:
                if dict(entry) not in bucket:
                    bucket.append(dict(entry))
        return replace(
            self,
            version=self.version + 1,
            nodes=(*self.nodes, *nodes),
            edges=(*self.edges, *edges),
            vocab=merged_vocab,
            status=RuleGraphStatus.ACTIVE,
            id=None,
            parent_id=self.id,
            created_at=None,
            notes=notes,
        )

    def next_edge_priority(self, from_key: str = ROOT_KEY) -> int:
        """A priority after every existing edge from ``from_key``."""
        priorities = [edge.priority for edge in self.edges if edge.from_key == from_key]
        return (max(priorities) + 10) if priorities else 100


# -- engine outcomes ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RouteOutcome:
    """A navigation hit."""

    decision: RouteDecision
    node_key: str
    priority: int = 50
    page_kind: PageKind | None = None


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    """What one extraction template produced for one document."""

    template_key: str
    page_kind: PageKind
    drafts: list[ListingDraft] = field(default_factory=list)
    links: list[DiscoveredLink] = field(default_factory=list)
    items_total: int = 0
    items_valid: int = 0
    #: Human-readable reasons items were dropped; feeds validation feedback.
    problems: list[str] = field(default_factory=list)
    #: Category/title texts no vocabulary entry matched.
    vocab_misses: list[str] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    #: Fields the template declares that produced no value on any item.
    empty_fields: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """A template "works" when it recognises a non-listing page or finds items."""
        return self.page_kind is PageKind.OTHER or self.items_valid > 0


@dataclass(frozen=True, slots=True)
class CandidateReport:
    """How a proposed template fared on one document during validation."""

    document_ref: str
    condition_matched: bool
    outcome: ExtractionOutcome | None
    error: str | None = None


# -- gaps and the LLM ledger ----------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleGap:
    """A cluster of inputs no rule handled: one question for induction."""

    id: UUID
    source_key: str
    domain: RuleDomain
    fingerprint: str
    status: GapStatus
    occurrences: int
    #: Navigation: sample URLs. Extraction: sample raw document ids.
    samples: list[str]
    attempts: int = 0
    last_error: str | None = None
    resolved_version: int | None = None


@dataclass(frozen=True, slots=True)
class LlmDecision:
    """One recorded model answer: the raw material rules are compiled from."""

    id: UUID
    task: str
    input_fp: str
    model: str
    prompt_version: str
    response: dict[str, Any] | None
    valid: bool
    raw_text: str = ""
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    created_at: datetime | None = None
