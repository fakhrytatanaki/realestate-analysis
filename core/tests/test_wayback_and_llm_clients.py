"""HTTP adapters (Wayback, Ollama) and the archive source, with mocked transports."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from realestate.domain.archive import Capture
from realestate.domain.enums import CrawlStatus, LinkRel, NodeKind, RuleDomain
from realestate.domain.exceptions import FetchError, LlmError, UnrecognisedDocumentError
from realestate.domain.models import FetchContext
from realestate.domain.ports.llm import LlmMessage
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleGraph, RuleNode
from realestate.infrastructure.archive import wayback
from realestate.infrastructure.archive.wayback import (
    WaybackCdxIndex,
    WaybackClient,
    WaybackSettings,
    parse_cdx_json,
)
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.llm import ollama
from realestate.infrastructure.llm.ollama import OllamaLlm, OllamaSettings, extract_json_object
from realestate.infrastructure.sources.olx_eg_wayback.source import OlxEgWaybackDataSource
from tests.archive_fakes import (
    TEMPLATES,
    InMemoryFrontier,
    InMemoryGraphs,
    fixture_bytes,
    fixture_meta,
)
from tests.conftest import NullLogProvider

FAST = WaybackSettings(min_delay_seconds=0, rate_limit_cooldown_seconds=0.01, max_retries=3)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(wayback.asyncio, "sleep", instant)
    monkeypatch.setattr(ollama.asyncio, "sleep", instant)


def test_parse_cdx_json_reads_rows_and_resume_key() -> None:
    rows = [
        ["urlkey", "timestamp", "original", "digest"],
        ["eg,com,olx)/", "20130105083820", "http://www.olx.com.eg:80/", "NLUN"],
        [],
        ["eJxLTddJ"],
    ]
    captures, key = parse_cdx_json(rows)
    assert captures == [
        Capture("eg,com,olx)/", "20130105083820", "http://www.olx.com.eg:80/", "NLUN")
    ]
    assert key == "eJxLTddJ"
    assert parse_cdx_json(rows[:2])[1] is None
    assert parse_cdx_json([]) == ([], None)


async def test_client_cools_down_on_429_then_succeeds() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(
            200,
            content=b"<html></html>",
            headers={
                "Memento-Datetime": "Tue, 05 Mar 2013 03:26:37 GMT",
                "Content-Type": "text/html",
            },
        )

    client = WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler))
    archived = await client.fetch_capture("20130301000000", "http://www.olx.com.eg/x")
    assert len(calls) == 2
    assert "20130301000000id_/http://www.olx.com.eg/x" in calls[0]
    assert archived.timestamp == "20130305032637"  # the capture actually served
    assert client.client.headers["User-Agent"].startswith("realestatepy-archive-research")
    await client.aclose()


async def test_client_does_not_retry_404() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    client = WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError):
        await client.fetch_capture("2013", "http://x.eg/")
    assert calls == 1


async def test_cdx_index_follows_resume_keys() -> None:
    pages = {
        None: [
            ["urlkey", "timestamp", "original", "digest"],
            ["k1", "20130101000000", "http://a", "D1"],
            [],
            ["RK"],
        ],
        "RK": [
            ["urlkey", "timestamp", "original", "digest"],
            ["k2", "20130202000000", "http://b", "D2"],
        ],
    }
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append({**params, "filters": request.url.params.get_list("filter")})
        return httpx.Response(200, json=pages[params.get("resumeKey")])

    index = WaybackCdxIndex(
        WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler))
    )
    got = [page async for page in index.iter_captures("olx.com.eg", 2013, page_size=1)]
    assert [len(p.captures) for p in got] == [1, 1]
    assert [p.resume_key for p in got] == ["RK", None]
    assert seen[0]["matchType"] == "domain" and seen[0]["filters"] == [
        "statuscode:200",
        "mimetype:text/html",
    ]


async def test_wayback_source_fetches_queued_captures_and_parses_with_pinned_graph() -> None:
    name = "a2_2013_list"
    meta = fixture_meta(name)
    frontier = InMemoryFrontier()
    await frontier.add_captures(
        "olx_eg_wayback",
        [
            Capture(
                "eg,com,olx)/houses-apartments-for-sale-cat-367",
                meta["timestamp"],
                meta["url"],
                "D",
            )
        ],
    )
    await frontier.set_route([1], status=CrawlStatus.QUEUED, priority=90)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=fixture_bytes(name), headers={"Content-Type": "text/html; charset=utf-8"}
        )

    graphs = InMemoryGraphs()
    source = OlxEgWaybackDataSource(
        log=NullLogProvider(),
        frontier=frontier,
        graphs=graphs,
        engine=HtmlRuleEngine(),
        client=WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler)),
    )
    payloads = [p async for p in source.fetch(FetchContext(max_items=5))]
    # Claimed, not done: only an acknowledged (archived) payload completes it.
    assert len(payloads) == 1 and frontier.rows[1].status is CrawlStatus.FETCHING
    await source.acknowledge(payloads[0])
    assert frontier.rows[1].status is CrawlStatus.FETCHED
    assert source.fetch_stats()["fetch_attempts"] == 1
    assert payloads[0].meta["captured_at"].startswith("2013-03-05")

    with pytest.raises(UnrecognisedDocumentError, match="graph v0"):
        await source.parse(payloads[0])

    # A graph saved later does not change what this instance parses with...
    await graphs.save_version(
        RuleGraph.empty("olx_eg_wayback", RuleDomain.EXTRACTION).extended(
            nodes=[RuleNode(name, NodeKind.TEMPLATE, TEMPLATES[name]["template"])],
            edges=[RuleEdge(ROOT_KEY, name, TEMPLATES[name]["condition"])],
            vocab=TEMPLATES["vocab"],
        )
    )
    with pytest.raises(UnrecognisedDocumentError):
        await source.parse(payloads[0])
    # ...but a fresh instance (a new run) does.
    fresh = OlxEgWaybackDataSource(
        log=NullLogProvider(), frontier=frontier, graphs=graphs, engine=HtmlRuleEngine()
    )
    drafts = await fresh.parse(payloads[0])
    links = await fresh.discover_links(payloads[0])
    assert len(drafts) == 30
    assert sum(1 for link in links if link.rel is LinkRel.DETAIL) == 30
    assert fresh.archive_scope().domain == "olx.com.eg"
    await source.aclose()


def _chat(message: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json={"message": message, "prompt_eval_count": 12, "eval_count": 5}
    )


async def test_ollama_reads_tool_calls_and_sends_auth() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(
            {"auth": request.headers.get("authorization"), "body": json.loads(request.content)}
        )
        return _chat(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "t", "arguments": {"a": 1}}}],
            }
        )

    llm = OllamaLlm(
        OllamaSettings(api_key="k"), log=NullLogProvider(), transport=httpx.MockTransport(handler)
    )
    response = await llm.complete(
        [LlmMessage("user", "hi")], schema={"type": "object"}, tool_name="t", tool_description="d"
    )
    assert response.data == {"a": 1}
    assert (response.tokens_in, response.tokens_out) == (12, 5)
    assert sent[0]["auth"] == "Bearer k"
    body = sent[0]["body"]
    assert body["model"] == "gemma4:31b" and body["stream"] is False and "format" not in body
    assert body["tools"][0]["function"]["name"] == "t"


async def test_ollama_falls_back_to_json_when_tools_are_rejected() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" in body:
            return httpx.Response(400, json={"error": "model does not support tools"})
        return _chat({"role": "assistant", "content": 'Sure!\n```json\n{"b": 2}\n```'})

    llm = OllamaLlm(OllamaSettings(), log=NullLogProvider(), transport=httpx.MockTransport(handler))
    response = await llm.complete(
        [LlmMessage("user", "hi")], schema={"type": "object"}, tool_name="t", tool_description="d"
    )
    assert response.data == {"b": 2}
    assert "JSON schema" in bodies[-1]["messages"][-1]["content"]


async def test_ollama_retries_502_and_rejects_bad_credentials() -> None:
    statuses = iter([502, 200])

    def flaky(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return _chat({"content": '{"ok": true}'}) if status == 200 else httpx.Response(status)

    llm = OllamaLlm(OllamaSettings(), log=NullLogProvider(), transport=httpx.MockTransport(flaky))
    response = await llm.complete(
        [LlmMessage("user", "x")], schema={}, tool_name="t", tool_description="d"
    )
    assert response.data == {"ok": True}

    denied = OllamaLlm(
        OllamaSettings(),
        log=NullLogProvider(),
        transport=httpx.MockTransport(lambda r: httpx.Response(401)),
    )
    with pytest.raises(LlmError, match="credentials"):
        await denied.complete(
            [LlmMessage("user", "x")], schema={}, tool_name="t", tool_description="d"
        )


def test_extract_json_object_tolerates_chatter() -> None:
    assert extract_json_object('here: {"a": {"b": [1, 2]}} done') == {"a": {"b": [1, 2]}}
    assert extract_json_object('{broken {"x": 1}') == {"x": 1}
    assert extract_json_object("no json") is None
