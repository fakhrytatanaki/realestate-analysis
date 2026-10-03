"""Pure archive vocabulary: URL canonicalisation, URL shapes, rule-graph walks."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from realestate.application.rules.seeds import EVIDENCE_NODE, seed_navigation_graph
from realestate.domain.archive import parse_timestamp, surt_key, url_shape
from realestate.domain.enums import NodeKind, RuleDomain
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "http://www.olx.com.eg:80/houses-apartments-for-sale-cat-367",
            "eg,com,olx)/houses-apartments-for-sale-cat-367",
        ),
        (
            "http://cairo.olx.com.eg:80/houses-apartments-for-rent-cat-363",
            "eg,com,olx,cairo)/houses-apartments-for-rent-cat-363",
        ),
        (
            "https://olx.com.eg/ar/property-for-rent/search/?page=2",
            "eg,com,olx)/ar/property-for-rent/search?page=2",
        ),
        (
            "https://olx.com.eg/en/i2/ad/-ID9v1Io.html#:7ecec404b1",
            "eg,com,olx)/en/i2/ad/-id9v1io.html",
        ),
        ("http://www.olx.com.eg/Search/?b=2&a=1", "eg,com,olx)/search?a=1&b=2"),
        (
            "https://www.olx.com.eg/ad/%D8%AF%D8%B1%D8%A7%D8%AC%D9%87-ID194308575.html",
            "eg,com,olx)/ad/%d8%af%d8%b1%d8%a7%d8%ac%d9%87-id194308575.html",
        ),
    ],
)
def test_surt_key_matches_the_cdx_urlkey(url: str, expected: str) -> None:
    # Expected values are urlkeys returned by the live CDX API for these URLs.
    assert surt_key(url) == expected


def test_url_shape_groups_the_same_template_and_separates_categories() -> None:
    assert url_shape(
        "http://hurgada.olx.com.eg/apartment-for-sale-in-hurghada-ref-1890653-iid-487590261"
    ) == url_shape("http://cairo.olx.com.eg/a-villa-for-rent-in-new-cairo-ref-12-iid-191449087")
    assert url_shape("https://olx.com.eg/ad/oppo-f5-32g-ID8tpzX.html") == url_shape(
        "https://olx.com.eg/ad/-ID9v1Io.html"
    )
    assert url_shape("https://olx.com.eg/ad/htc-IDabcDe.html") == url_shape(
        "https://olx.com.eg/ad/-ID9v1Io.html"
    )
    properties = url_shape(
        "https://olx.com.eg/en/i2/properties/properties-for-sale/apartments-for-sale/maadi/"
    )
    vehicles = url_shape("https://olx.com.eg/en/i2/vehicles/cars-for-sale/toyota/maadi/")
    assert properties != vehicles
    assert url_shape("https://olx.com.eg/ar/property-for-rent/search/?page=2").endswith("|q:page")


def test_parse_timestamp_pads_prefixes() -> None:
    assert parse_timestamp("20130305032637") == datetime(2013, 3, 5, 3, 26, 37, tzinfo=UTC)
    assert parse_timestamp("2013") == datetime(2013, 1, 1, tzinfo=UTC)


def _graph() -> RuleGraph:
    return RuleGraph.empty("s", RuleDomain.EXTRACTION).extended(
        nodes=[
            RuleNode("branch", NodeKind.BRANCH),
            RuleNode("a", NodeKind.TEMPLATE, {"n": "a"}),
            RuleNode("b", NodeKind.TEMPLATE, {"n": "b"}),
            RuleNode("c", NodeKind.TEMPLATE, {"n": "c"}),
        ],
        edges=[
            RuleEdge(ROOT_KEY, "c", {"id": "c"}, priority=200),
            RuleEdge(ROOT_KEY, "branch", {"id": "branch"}, priority=100),
            RuleEdge("branch", "b", {"id": "b"}, priority=20),
            RuleEdge("branch", "a", {"id": "a"}, priority=10),
        ],
    )


def test_candidates_walk_depth_first_in_priority_order() -> None:
    order = [node.key for node, _ in _graph().candidates(lambda condition: True)]
    assert order == ["a", "b", "c"]


def test_candidates_skip_edges_whose_condition_fails() -> None:
    holds = {"branch", "b", "c"}
    walked = list(_graph().candidates(lambda condition: condition["id"] in holds))
    assert [(node.key, path) for node, path in walked] == [
        ("b", [ROOT_KEY, "branch", "b"]),
        ("c", [ROOT_KEY, "c"]),
    ]


def test_cyclic_graph_does_not_hang() -> None:
    graph = RuleGraph.empty("s", RuleDomain.EXTRACTION).extended(
        nodes=[RuleNode("x", NodeKind.BRANCH)],
        edges=[RuleEdge(ROOT_KEY, "x", {}), RuleEdge("x", ROOT_KEY, {})],
    )
    assert list(graph.candidates(lambda condition: True)) == []


def test_extended_creates_next_version_and_merges_vocab() -> None:
    base = RuleGraph.empty("s", RuleDomain.EXTRACTION)
    v1 = base.extended(vocab={"listing_type": [{"pattern": "rent", "value": "RENT"}]})
    v2 = v1.extended(
        vocab={
            "listing_type": [
                {"pattern": "rent", "value": "RENT"},
                {"pattern": "sale", "value": "SALE"},
            ]
        }
    )
    assert (v1.version, v2.version) == (1, 2)
    assert v2.vocab["listing_type"] == [
        {"pattern": "rent", "value": "RENT"},
        {"pattern": "sale", "value": "SALE"},
    ]
    with pytest.raises(ValueError, match="already exist"):
        v2.extended(nodes=[RuleNode(ROOT_KEY, NodeKind.ROOT)])


def test_seed_navigation_graph_follows_link_evidence_first() -> None:
    graph = seed_navigation_graph("s")
    assert graph.version == 1
    assert graph.outgoing(ROOT_KEY)[0].to_key == EVIDENCE_NODE
    assert graph.next_edge_priority() > graph.outgoing(ROOT_KEY)[0].priority
