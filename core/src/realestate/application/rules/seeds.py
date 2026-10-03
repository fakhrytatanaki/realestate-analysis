"""Generic navigation seed, shared with reviewed source-specific seed graphs.

The navigation seed encodes one idea: a
URL that a *recognised* property list or detail page links to is worth
fetching, whatever its URL looks like. That is what rescues advert URLs whose
slug says nothing (``/ad/-ID9v1Io.html``).
"""

from __future__ import annotations

from realestate.domain.enums import NodeKind, RuleDomain, RuleOrigin
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode

EVIDENCE_NODE = "seed.follow_links_from_recognised_pages"


def seed_navigation_graph(source_key: str) -> RuleGraph:
    """Version 1 of a navigation graph: root plus the link-evidence rule."""
    return RuleGraph.empty(source_key, RuleDomain.NAVIGATION).extended(
        nodes=[
            RuleNode(
                key=EVIDENCE_NODE,
                kind=NodeKind.ROUTE,
                action={"decision": "FETCH", "priority": 75, "page_kind": "FROM_EVIDENCE"},
                origin=RuleOrigin.SEED,
            )
        ],
        edges=[
            RuleEdge(
                from_key=ROOT_KEY,
                to_key=EVIDENCE_NODE,
                condition={
                    "type": "evidence",
                    "key": "linked_as",
                    "in": ["DETAIL", "LIST", "PAGINATION"],
                },
                priority=0,
            )
        ],
        notes="seed: follow links from recognised pages",
    )
