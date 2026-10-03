"""Load packaged, reviewed rule proposals and compile immutable version-one graphs."""

from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from realestate.application.rules.proposals import NavRuleProposal, TemplateProposal, VocabEntry
from realestate.application.rules.seeds import seed_navigation_graph
from realestate.domain.enums import ListingType, NodeKind, PropertyType, RuleDomain
from realestate.domain.ports.rules import RuleSeedProvider
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.extraction.template import KNOWN_FIELDS


class SeedBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str
    navigation: list[NavRuleProposal] = Field(min_length=1)
    extraction: list[TemplateProposal] = Field(min_length=1)
    vocab: dict[str, list[VocabEntry]]


def compile_seed_bundle(data: dict[str, Any], *, source_key: str) -> tuple[RuleGraph, ...]:
    """Validate all proposals before exposing either graph to persistence."""
    bundle = SeedBundle.model_validate(data)
    if bundle.source_key != source_key:
        raise ValueError("seed source key does not match requested source")
    allowed_vocab = {"listing_type": ListingType, "property_type": PropertyType}
    for field, entries in bundle.vocab.items():
        if field not in allowed_vocab:
            raise ValueError(f"unknown seed vocabulary {field!r}")
        for entry in entries:
            allowed_vocab[field](entry.value)
            try:
                re.compile(entry.pattern)
            except re.error as exc:
                raise ValueError(f"invalid vocabulary pattern: {exc}") from exc
    engine = HtmlRuleEngine()
    navigation = seed_navigation_graph(source_key)
    nav_nodes: list[RuleNode] = []
    nav_edges: list[RuleEdge] = []
    for index, rule in enumerate(bundle.navigation):
        key = f"seed.navigation.{index}"
        nav_nodes.append(
            RuleNode(
                key,
                NodeKind.ROUTE,
                {
                    "decision": rule.decision,
                    "page_kind": rule.page_kind,
                    "priority": rule.priority,
                },
            )
        )
        nav_edges.append(
            RuleEdge(
                ROOT_KEY,
                key,
                {"type": "url_regex", "pattern": rule.pattern},
                priority=index + 10,
            )
        )
    # Host validation must precede the generic evidence rule. The bundle's
    # first navigation rule rejects every URL outside the allowed site hosts.
    nav_edges[0] = RuleEdge(ROOT_KEY, nav_nodes[0].key, nav_edges[0].condition, priority=-10)
    navigation = RuleGraph(
        source_key=source_key,
        domain=RuleDomain.NAVIGATION,
        version=1,
        nodes=(*navigation.nodes, *nav_nodes),
        edges=(*navigation.edges, *nav_edges),
        notes="reviewed Dubizzle archive navigation plus generic link evidence",
    )
    nodes: list[RuleNode] = []
    edges: list[RuleEdge] = []
    for index, proposal in enumerate(bundle.extraction):
        errors = engine.check_condition(proposal.condition())
        unknown = set(proposal.fields) - KNOWN_FIELDS
        errors.extend(f"unknown field {name}" for name in unknown if not name.startswith("extra."))
        if proposal.page_kind == "LIST" and not proposal.items:
            errors.append("LIST seed needs items")
        if proposal.page_kind != "OTHER" and "title" not in proposal.fields:
            errors.append("listing seed needs title")
        if proposal.items and not (proposal.items.get("script") or proposal.items.get("css")):
            errors.append("items needs script or css")
        for alternatives in proposal.fields.values():
            for spec in alternatives:
                if spec.get("regex"):
                    try:
                        re.compile(spec["regex"])
                    except re.error as exc:
                        errors.append(f"invalid field regex: {exc}")
        if errors:
            raise ValueError(f"invalid seed {proposal.name}: {errors}")
        key = f"seed.{proposal.name}"
        if any(node.key == key for node in nodes):
            raise ValueError(f"duplicate seed template {key}")
        nodes.append(RuleNode(key, NodeKind.TEMPLATE, proposal.action()))
        edges.append(RuleEdge(ROOT_KEY, key, proposal.condition(), priority=index * 10))
    extraction = RuleGraph.empty(source_key, RuleDomain.EXTRACTION).extended(
        nodes=nodes,
        edges=edges,
        vocab={
            key: [entry.model_dump() for entry in entries] for key, entries in bundle.vocab.items()
        },
        notes="reviewed JSON lists, 2023 detail and sales HTML fallback; unknowns are gaps",
    )
    return navigation, extraction


class PackagedRuleSeedProvider(RuleSeedProvider):
    def load(self, source_key: str) -> tuple[RuleGraph, ...]:
        if source_key != "dubizzle_eg_wayback":
            raise ValueError(f"no reviewed rule seeds are packaged for source {source_key!r}")
        resource = files("realestate.infrastructure.sources.dubizzle_eg_wayback").joinpath(
            "rules.json"
        )
        return compile_seed_bundle(json.loads(resource.read_text()), source_key=source_key)
