"""Explicit installation of reviewed rules; operator graphs take precedence."""

from __future__ import annotations

from dataclasses import dataclass

from realestate.domain.enums import RuleDomain
from realestate.domain.ports.rules import RuleGraphRepository, RuleSeedProvider


@dataclass(frozen=True, slots=True)
class SeedReport:
    installed: tuple[RuleDomain, ...]
    retained: tuple[RuleDomain, ...]


class RuleSeedService:
    def __init__(self, *, graphs: RuleGraphRepository, seeds: RuleSeedProvider) -> None:
        self._graphs = graphs
        self._seeds = seeds

    async def seed(self, source_key: str) -> SeedReport:
        # Validate every graph before writing either domain.
        seeds = self._seeds.load(source_key)
        installed: list[RuleDomain] = []
        retained: list[RuleDomain] = []
        for graph in seeds:
            if await self._graphs.active(source_key, graph.domain) is not None:
                retained.append(graph.domain)
                continue
            await self._graphs.save_version(graph)
            installed.append(graph.domain)
        return SeedReport(tuple(installed), tuple(retained))
