"""Compacting a navigation graph: dropping rules that route nothing.

Navigation is first-match, and induced rules are appended, so a rule written
for URLs an earlier rule already routes never decides anything. Such rules
cost routing time and hide what the graph really does; with stale gap samples
they were most of what induction produced (``docs/rule-induction-review.md``).

A plan replays the active graph over every URL key of the frontier, removes
the rules an operator names (a harmful catch-all, say), then every induced
rule that is never the first match in what is left. Removing a rule that
never wins changes no route, so the plan's routing diff must show changes
only for URL keys a *named* rule decided; it is checked by routing every key
through the candidate as well. Applying saves the candidate; ``archive route
--reopen-removed`` (which ``rules compact`` runs) then sends the named rules'
captures back to routing.

Liveness is judged on the URL keys the frontier holds now. A rule that only
matters for years not yet enumerated looks dead; if those years bring its URLs
back, they come back as navigation gaps.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from realestate.domain.archive import FrontierEntry, link_evidence
from realestate.domain.enums import NodeKind, RuleDomain, RuleOrigin
from realestate.domain.exceptions import ConfigurationError
from realestate.domain.ports.archive import CrawlFrontierRepository
from realestate.domain.ports.log_provider import LogProvider
from realestate.domain.ports.rules import RuleEngine, RuleGraphRepository
from realestate.domain.rules import ROOT_KEY, RouteOutcome, RuleGraph

#: Routing outcome of URL keys no rule matches.
UNROUTED = "UNROUTED"


@dataclass(slots=True)
class CompactionPlan:
    current: RuleGraph
    candidate: RuleGraph
    #: Rules the operator asked to remove.
    removed: list[str] = field(default_factory=list)
    #: Induced rules that are never the first match once ``removed`` are gone.
    dead: list[str] = field(default_factory=list)
    url_keys: int = 0
    #: URL keys each rule of the candidate decides.
    wins: Counter[str] = field(default_factory=Counter)
    #: ``"old rule -> new rule (decision)"`` -> URL keys whose route changes.
    changes: Counter[str] = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=dict)
    #: URL keys whose route changes although no named rule decided them. A
    #: correct plan has none; ``apply`` refuses one that does.
    unexpected: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.removed or self.dead)

    @property
    def live_routes(self) -> int:
        return sum(1 for node in self.candidate.nodes if node.kind is NodeKind.ROUTE)

    def summary(self) -> str:
        moved = sum(self.changes.values())
        return (
            f"compacted v{self.current.version}: removed {', '.join(self.removed) or 'none'}"
            f" and {len(self.dead)} induced rules that were never the first match; "
            f"{self.live_routes} routes left; routing diff over {self.url_keys} url keys: "
            f"{moved} changed (decided by removed rules), {self.unexpected} other"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.current.source_key,
            "from_version": self.current.version,
            "to_version": self.candidate.version,
            "url_keys": self.url_keys,
            "removed": self.removed,
            "dead": self.dead,
            "live_routes": self.live_routes,
            "unexpected": self.unexpected,
            "changes": dict(self.changes.most_common()),
            "examples": self.examples,
            "wins": dict(self.wins.most_common()),
        }


class NavigationCompactionService:
    def __init__(
        self,
        *,
        frontier: CrawlFrontierRepository,
        graphs: RuleGraphRepository,
        engine: RuleEngine,
        log: LogProvider,
        batch: int = 2_000,
    ) -> None:
        self._frontier = frontier
        self._graphs = graphs
        self._engine = engine
        self._log = log
        self._batch = batch

    async def plan(self, source_key: str, *, remove: Sequence[str] = ()) -> CompactionPlan:
        current = await self._graphs.active(source_key, RuleDomain.NAVIGATION)
        if current is None:
            raise ConfigurationError(f"source '{source_key}' has no navigation graph")
        named = list(dict.fromkeys(remove))
        if ROOT_KEY in named:
            raise ConfigurationError("the root state cannot be removed")
        missing = [key for key in named if not current.has_node(key)]
        if missing:
            raise ConfigurationError(f"no such navigation rules: {missing}")
        pruned = current.revised(remove=named) if named else current

        # Removing rules that do not win a URL leaves its route unchanged, so
        # only keys a named rule decided need routing through ``pruned``.
        routed: list[tuple[str, dict[str, list[str]], RouteOutcome | None]] = []
        plan = CompactionPlan(current=current, candidate=current, removed=named)
        wins: Counter[str] = Counter()
        async for url, evidence in self._url_keys(source_key):
            old = self._engine.route(current, url, evidence)
            new = old
            if old is not None and old.node_key in named:
                new = self._engine.route(pruned, url, evidence)
                change = f"{old.node_key} -> {_describe(new)}"
                plan.changes[change] += 1
                examples = plan.examples.setdefault(change, [])
                if len(examples) < 5:
                    examples.append(url)
            if new is not None:
                wins[new.node_key] += 1
            routed.append((url, evidence, new))
        plan.url_keys = len(routed)
        plan.wins = wins
        plan.dead = [
            node.key
            for node in pruned.nodes
            if node.kind is NodeKind.ROUTE and node.origin is RuleOrigin.LLM and not wins[node.key]
        ]
        if not plan.changed:
            return plan
        candidate = current.revised(remove=[*named, *plan.dead])
        # Check, rather than assume, that the dead rules decided nothing.
        plan.wins = Counter()
        for url, evidence, expected in routed:
            outcome = self._engine.route(candidate, url, evidence)
            if outcome is not None:
                plan.wins[outcome.node_key] += 1
            if _describe(outcome) != _describe(expected):
                plan.unexpected += 1
        plan.candidate = candidate  # the summary counts the candidate's routes
        plan.candidate = replace(candidate, notes=plan.summary())
        return plan

    async def apply(self, plan: CompactionPlan) -> RuleGraph:
        if not plan.changed:
            return plan.current
        if plan.unexpected:
            raise ConfigurationError(
                f"{plan.unexpected} url keys would change route although no removed rule "
                "decided them; not saving"
            )
        saved = await self._graphs.save_version(plan.candidate)
        await self._log.info(
            "navigation graph compacted",
            source_key=saved.source_key,
            version=saved.version,
            removed=len(plan.removed),
            dead=len(plan.dead),
            live_routes=plan.live_routes,
        )
        return saved

    async def _url_keys(self, source_key: str) -> AsyncIterator[tuple[str, dict[str, list[str]]]]:
        """``(newest original URL, merged link evidence)`` of every URL key."""
        after: str | None = None
        while True:
            keys = await self._frontier.url_keys_page(source_key, after=after, limit=self._batch)
            if not keys:
                return
            by_key: dict[str, list[FrontierEntry]] = {}
            for entry in await self._frontier.captures_for(source_key, keys):
                by_key.setdefault(entry.url_key, []).append(entry)
            for key in keys:
                found = by_key.get(key)
                if found:
                    yield found[-1].original_url, link_evidence(found)
            after = keys[-1]


def _describe(outcome: RouteOutcome | None) -> str:
    if outcome is None:
        return UNROUTED
    return f"{outcome.node_key} ({outcome.decision})"
