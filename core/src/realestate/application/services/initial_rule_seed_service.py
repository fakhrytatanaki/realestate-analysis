"""Explicit installation of reviewed rules; operator graphs take precedence."""

from __future__ import annotations

from dataclasses import dataclass, replace

from realestate.domain.enums import RuleDomain
from realestate.domain.ports.rules import RuleGraphRepository, RuleSeedProvider
from realestate.domain.rules import RuleGraph


@dataclass(frozen=True, slots=True)
class SeedReport:
    installed: tuple[RuleDomain, ...]
    retained: tuple[RuleDomain, ...]
    #: Active graphs superseded by the reviewed one: ``(domain, old, new version)``.
    replaced: tuple[tuple[RuleDomain, int, int], ...] = ()


class InitialRuleSeedService:
    def __init__(self, *, graphs: RuleGraphRepository, seeds: RuleSeedProvider) -> None:
        self._graphs = graphs
        self._seeds = seeds

    async def seed(
        self, source_key: str, *, dry_run: bool = False, replace_active: bool = False
    ) -> SeedReport:
        """Install the reviewed graphs where a domain has none.

        An active graph is retained unless ``replace_active``: then the reviewed
        graph is saved as its next version, which retires it. A crawl creates a
        navigation graph on its first run and induction extends both domains, so
        without this the reviewed rules could never follow an early crawl.
        """
        # Validate every graph before writing either domain.
        seeds = self._seeds.load(source_key)
        installed: list[RuleDomain] = []
        retained: list[RuleDomain] = []
        replaced: list[tuple[RuleDomain, int, int]] = []
        for graph in seeds:
            active = await self._graphs.active(source_key, graph.domain)
            if active is None:
                if not dry_run:
                    await self._graphs.save_version(graph)
                installed.append(graph.domain)
            elif not replace_active or same_rules(active, graph):
                retained.append(graph.domain)
            else:
                successor = replace(
                    graph,
                    version=active.version + 1,
                    parent_id=active.id,
                    notes=f"{graph.notes}; replaces v{active.version}",
                )
                if not dry_run:
                    await self._graphs.save_version(successor)
                replaced.append((graph.domain, active.version, successor.version))
        return SeedReport(tuple(installed), tuple(retained), tuple(replaced))


def same_rules(left: RuleGraph, right: RuleGraph) -> bool:
    """Whether two graphs decide alike: same states, guards and vocabulary."""

    def rules(graph: RuleGraph) -> tuple[object, ...]:
        return (
            [(node.key, node.kind, node.action) for node in graph.nodes],
            [(edge.from_key, edge.to_key, edge.condition, edge.priority) for edge in graph.edges],
            graph.vocab,
        )

    return rules(left) == rules(right)
