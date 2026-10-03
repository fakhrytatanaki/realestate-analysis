"""Replay provenance and redirect boundaries, using only mocked HTTP."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from realestate.domain.archive import ArchivedDocument, Capture, parse_timestamp, surt_key
from realestate.domain.enums import CrawlStatus
from realestate.domain.exceptions import FetchError
from realestate.domain.models import FetchContext
from realestate.infrastructure.archive import wayback
from realestate.infrastructure.archive.rate_gate import RateGate
from realestate.infrastructure.archive.wayback import WaybackClient, WaybackSettings
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.extraction.seeds import PackagedRuleSeedProvider
from realestate.infrastructure.sources.dubizzle_eg_wayback.source import DubizzleEgWaybackDataSource
from realestate.infrastructure.sources.olx_eg_wayback.source import OlxEgWaybackDataSource
from tests.archive_fakes import InMemoryFrontier, InMemoryGraphs
from tests.conftest import NullLogProvider

FAST = WaybackSettings(min_delay_seconds=0, max_retries=1)
ORIGINAL = "https://www.dubizzle.com.eg/properties/?page=2"
REQUESTED = "20230101000000"
START = f"{wayback.REPLAY_PREFIX}/{REQUESTED}id_/{ORIGINAL}"


async def test_nearest_capture_retains_each_url_timestamp_and_redirect_hop() -> None:
    served_url = "http://dubizzle.com.eg/en/properties/?page=3&view=list"
    middle = f"{wayback.REPLAY_PREFIX}/2023id_/{served_url}"
    final = f"{wayback.REPLAY_PREFIX}/20230202030405id_/{served_url}"
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        if len(visits) == 1:
            return httpx.Response(302, headers={"Location": middle})
        if len(visits) == 2:
            return httpx.Response(301, headers={"Location": final})
        return httpx.Response(200, content=b"historic", headers={"Content-Type": "text/html"})

    client = WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler))
    try:
        result = await client.fetch_capture(REQUESTED, ORIGINAL)
        assert result.original_url == served_url
        assert result.timestamp == "20230202030405"
        assert result.captured_at == datetime(2023, 2, 2, 3, 4, 5, tzinfo=UTC)
        assert result.requested_timestamp == REQUESTED
        assert result.requested_original_url == ORIGINAL
        assert result.requested_replay_url == START
        assert result.replay_url == final
        assert result.redirect_chain == tuple(visits) == (START, middle, final)
        assert result.timestamp_source == "replay-url"
        assert result.replay_timestamp == result.timestamp
        assert result.content == b"historic"
    finally:
        await client.aclose()


async def test_every_replay_redirect_reserves_the_shared_rate_gate() -> None:
    middle = f"{wayback.REPLAY_PREFIX}/2023id_/{ORIGINAL}"
    final = f"{wayback.REPLAY_PREFIX}/20230202030405id_/{ORIGINAL}"
    responses = iter(
        [
            httpx.Response(302, headers={"Location": middle}),
            httpx.Response(302, headers={"Location": final}),
            httpx.Response(200, content=b"historic"),
        ]
    )
    gate = Mock(spec=RateGate)
    gate.reserve = AsyncMock(return_value=0)
    client = WaybackClient(
        FAST,
        log=NullLogProvider(),
        gate=gate,
        transport=httpx.MockTransport(lambda request: next(responses)),
    )
    try:
        result = await client.fetch_capture(REQUESTED, ORIGINAL)
        assert result.redirect_chain == (START, middle, final)
        assert gate.reserve.await_count == 3
        for call in gate.reserve.await_args_list:
            assert call.args == (wayback.ARCHIVE_GATE,) and call.kwargs == {"interval": 0}
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "target",
    [
        "https://www.dubizzle.com.eg/properties/",  # live-site escape
        "https://web.archive.org.evil.example/web/20230202000000id_/" + ORIGINAL,
        "https://web.archive.org:8443/web/20230202000000id_/" + ORIGINAL,
        "https://user:password@web.archive.org/web/20230202000000id_/" + ORIGINAL,
        f"{wayback.REPLAY_PREFIX}/20230202000000id_/https://olx.com.eg/properties/",
        f"{wayback.REPLAY_PREFIX}/20230202000000id_/https://images.dubizzle.com.eg/",
        f"{wayback.REPLAY_PREFIX}/20230202000000id_/https://dubizzle.com.eg.evil.example/",
        f"{wayback.REPLAY_PREFIX}/20230202000000id_/https://user@dubizzle.com.eg/",
        f"{wayback.REPLAY_PREFIX}/20230202000000id_/https://dubizzle.com.eg:8443/",
        f"{wayback.REPLAY_PREFIX}/20230202000000/{ORIGINAL}",  # rewritten replay
        "https://web.archive.org/",  # archive landing page is not capture evidence
    ],
)
async def test_bad_redirect_is_rejected_before_any_request_to_target(target: str) -> None:
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        return httpx.Response(302, headers={"Location": target})

    client = WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FetchError):
            await client.fetch_capture(REQUESTED, ORIGINAL)
        assert visits == [START]
    finally:
        await client.aclose()


async def test_initial_original_must_match_explicit_source_hosts_before_io() -> None:
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        return httpx.Response(200)

    client = WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FetchError, match="unexpected original host"):
            await client.fetch_capture(
                REQUESTED, "https://olx.com.eg/", allowed_hosts={"dubizzle.com.eg"}
            )
        assert visits == []
    finally:
        await client.aclose()


@pytest.mark.parametrize("header", [None, "invalid", "Sun, 01 Jan 2023 00:00:00"])
async def test_short_replay_without_aware_memento_never_uses_requested_time(
    header: str | None,
) -> None:
    headers = {"Memento-Datetime": header} if header is not None else {}
    client = WaybackClient(
        FAST,
        log=NullLogProvider(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=headers)),
    )
    try:
        with pytest.raises(FetchError, match="no trustworthy served capture timestamp"):
            await client.fetch_capture("2023", ORIGINAL)
    finally:
        await client.aclose()


async def test_memento_time_is_utc_and_recorded_separately_from_replay_url_time() -> None:
    client = WaybackClient(
        FAST,
        log=NullLogProvider(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Memento-Datetime": "Thu, 02 Feb 2023 05:04:05 +0200"}
            )
        ),
    )
    try:
        result = await client.fetch_capture(REQUESTED, ORIGINAL)
        assert result.timestamp == "20230202030405"
        assert result.captured_at.utcoffset().total_seconds() == 0
        assert result.replay_timestamp == REQUESTED
        assert result.timestamp_source == "memento-datetime"
    finally:
        await client.aclose()


@pytest.mark.parametrize("timestamp", ["20230230000000", "not-a-time"])
async def test_invalid_replay_timestamp_fails_before_io(timestamp: str) -> None:
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        return httpx.Response(200)

    client = WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FetchError):
            await client.fetch_capture(timestamp, ORIGINAL)
        assert visits == []
    finally:
        await client.aclose()


async def test_invalid_memento_can_use_valid_full_replay_url() -> None:
    client = WaybackClient(
        FAST,
        log=NullLogProvider(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Memento-Datetime": "invalid"})
        ),
    )
    try:
        result = await client.fetch_capture(REQUESTED, ORIGINAL)
        assert result.timestamp == REQUESTED and result.timestamp_source == "replay-url"
    finally:
        await client.aclose()


async def test_redirect_loop_is_bounded_and_each_hop_is_throttled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    visits: list[str] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        return httpx.Response(302, headers={"Location": START})

    monkeypatch.setattr(wayback.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(wayback.asyncio, "sleep", sleep)
    client = WaybackClient(
        WaybackSettings(min_delay_seconds=1.5, max_retries=1),
        log=NullLogProvider(),
        transport=httpx.MockTransport(handler),
    )
    client.client.max_redirects = 2
    try:
        with pytest.raises(FetchError, match="too many archive redirects"):
            await client.fetch_capture(REQUESTED, ORIGINAL)
        assert len(visits) == 3
        assert sleeps == [1.5, 1.5]
    finally:
        await client.aclose()


async def test_cdx_redirect_cannot_escape_wayback() -> None:
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://example.com/cdx"})

    client = WaybackClient(FAST, log=NullLogProvider(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FetchError, match="escaped Wayback"):
            await client.get(wayback.CDX_ENDPOINT)
        assert visits == [wayback.CDX_ENDPOINT]
    finally:
        await client.aclose()


async def test_network_retry_after_redirect_keeps_only_successful_provenance() -> None:
    final = f"{wayback.REPLAY_PREFIX}/20230202030405id_/{ORIGINAL}"
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        if str(request.url) == START:
            return httpx.Response(302, headers={"Location": final})
        if len(visits) == 2:
            raise httpx.ConnectError("temporary archive outage", request=request)
        return httpx.Response(200, content=b"historic")

    client = WaybackClient(
        WaybackSettings(min_delay_seconds=0, max_retries=2),
        log=NullLogProvider(),
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.fetch_capture(REQUESTED, ORIGINAL)
        assert visits == [START, final, START, final]
        assert result.redirect_chain == (START, final)
        assert result.timestamp == "20230202030405"
    finally:
        await client.aclose()


@pytest.mark.parametrize("served_year", [2022, 2023, 2024])
async def test_source_only_emits_served_captures_in_selected_years(served_year: int) -> None:
    served_timestamp = f"{served_year}0202030405"
    served_url = "https://dubizzle.com.eg/en/properties/?page=3"
    final = f"{wayback.REPLAY_PREFIX}/{served_timestamp}id_/{served_url}"
    frontier, graphs, log = InMemoryFrontier(), InMemoryGraphs(), NullLogProvider()
    await frontier.add_captures(
        DubizzleEgWaybackDataSource.key, [Capture(surt_key(ORIGINAL), REQUESTED, ORIGINAL, "CDX")]
    )
    await frontier.set_route([1], status=CrawlStatus.QUEUED)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == START:
            return httpx.Response(302, headers={"Location": final})
        return httpx.Response(200, content=b"historic")

    source = DubizzleEgWaybackDataSource(
        params={"from_year": 2023, "to_year": 2023},
        log=log,
        frontier=frontier,
        graphs=graphs,
        engine=HtmlRuleEngine(),
        client=WaybackClient(FAST, log=log, transport=httpx.MockTransport(handler)),
    )
    try:
        payloads = [payload async for payload in source.fetch(FetchContext(max_items=1))]
        if served_year != 2023:
            assert payloads == []
            assert frontier.rows[1].status is CrawlStatus.FAILED
            assert "held for review" in frontier.rows[1].error
            assert served_timestamp in frontier.rows[1].error
            assert not any(message == "capture fetched" for _, message, _ in log.records)
            return
        assert len(payloads) == 1 and frontier.rows[1].status is CrawlStatus.FETCHING
        await source.acknowledge(payloads[0])
        assert frontier.rows[1].status is CrawlStatus.FETCHED
        payload = payloads[0]
        assert payload.source_url == payload.meta["original_url"] == served_url
        assert payload.meta["requested_original_url"] == ORIGINAL
        assert payload.meta["requested_timestamp"] == REQUESTED
        assert payload.meta["served_timestamp"] == payload.meta["timestamp"] == served_timestamp
        assert payload.meta["requested_replay_url"] == START
        assert payload.meta["replay_url"] == final
        assert payload.meta["redirect_chain"] == [START, final]
        assert payload.meta["served_url_key"] == surt_key(served_url)
        assert payload.meta["url_key"] == surt_key(ORIGINAL)
        assert payload.meta["cdx_digest"] == payload.meta["digest"] == "CDX"
        document = parse_timestamp(served_timestamp)
        assert payload.meta["captured_at"] == document.isoformat()
        assert (
            payload.meta["capture_drift_seconds"]
            == (document - parse_timestamp(REQUESTED)).total_seconds()
        )
        # Metadata reconstructed during offline replay uses the served URL/time.
        replay = ArchivedDocument.from_payload(
            payload.content,
            content_type=payload.content_type,
            source_url=payload.source_url,
            meta=payload.meta,
        )
        assert replay.url == served_url and replay.captured_at == document
    finally:
        await source.aclose()


@pytest.mark.parametrize("failure", ["missing_time", "header_outside_scope", "wrong_host"])
async def test_source_records_untrustworthy_captures_without_yielding_payloads(
    failure: str,
) -> None:
    frontier, log = InMemoryFrontier(), NullLogProvider()
    requested = "2023" if failure == "missing_time" else REQUESTED
    await frontier.add_captures(
        DubizzleEgWaybackDataSource.key, [Capture(surt_key(ORIGINAL), requested, ORIGINAL, "CDX")]
    )
    await frontier.set_route([1], status=CrawlStatus.QUEUED)
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        if failure == "wrong_host":
            return httpx.Response(302, headers={"Location": "https://dubizzle.com.eg/"})
        headers = (
            {"Memento-Datetime": "Tue, 02 Jan 2024 00:00:00 GMT"}
            if failure == "header_outside_scope"
            else {}
        )
        return httpx.Response(200, content=b"historic", headers=headers)

    source = DubizzleEgWaybackDataSource(
        params={"from_year": 2023, "to_year": 2023},
        log=log,
        frontier=frontier,
        graphs=InMemoryGraphs(),
        engine=HtmlRuleEngine(),
        client=WaybackClient(FAST, log=log, transport=httpx.MockTransport(handler)),
    )
    try:
        assert [payload async for payload in source.fetch(FetchContext(max_items=1))] == []
        assert frontier.rows[1].status is CrawlStatus.FAILED
        reason = {
            "missing_time": "no trustworthy served capture timestamp",
            "header_outside_scope": "held for review",
            "wrong_host": "escaped Wayback",
        }[failure]
        assert reason in frontier.rows[1].error
        assert len(visits) == 1
        assert any(message == "capture fetch failed" for _, message, _ in log.records)
    finally:
        await source.aclose()


async def test_redirected_fixture_uses_served_time_for_offline_listing_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = Path(__file__).parent / "fixtures" / "wayback_dubizzle_eg"
    meta = json.loads((fixtures / "index.json").read_text())["2023_rent"]
    content = gzip.decompress((fixtures / "2023_rent.html.gz").read_bytes())
    final = f"{wayback.REPLAY_PREFIX}/{meta['served_timestamp']}id_/{meta['url']}"
    frontier, graphs, log = InMemoryFrontier(), InMemoryGraphs(), NullLogProvider()
    await frontier.add_captures(
        DubizzleEgWaybackDataSource.key, [Capture(surt_key(ORIGINAL), REQUESTED, ORIGINAL, "CDX")]
    )
    await frontier.set_route([1], status=CrawlStatus.QUEUED)
    for graph in PackagedRuleSeedProvider().load(DubizzleEgWaybackDataSource.key):
        await graphs.save_version(graph)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == START:
            return httpx.Response(302, headers={"Location": final})
        return httpx.Response(200, content=content)

    source = DubizzleEgWaybackDataSource(
        params={"from_year": 2023, "to_year": 2023},
        log=log,
        frontier=frontier,
        graphs=graphs,
        engine=HtmlRuleEngine(),
        client=WaybackClient(FAST, log=log, transport=httpx.MockTransport(handler)),
    )
    try:
        payloads = [payload async for payload in source.fetch(FetchContext(max_items=1))]

        async def no_network(*args: object, **kwargs: object) -> None:
            raise AssertionError("offline replay made a network call")

        monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
        drafts = await source.parse(payloads[0])
        assert len(drafts) == len(meta["expected"])
        assert {draft.observed_at for draft in drafts} == {
            parse_timestamp(meta["served_timestamp"])
        }
        assert all(not draft.is_active for draft in drafts)
        assert await source.parse(payloads[0]) == drafts
        assert payloads[0].meta["requested_timestamp"] == REQUESTED
    finally:
        await source.aclose()


@pytest.mark.parametrize(
    ("served_host", "accepted"),
    [
        ("hurgada.olx.com.eg", True),
        ("www.olx.com.eg", True),
        ("alexandria.olx.com.eg", False),
        ("hurgada.olx.com.eg.evil.example", False),
    ],
)
async def test_olx_city_capture_allows_requested_city_and_root_hosts_only(
    served_host: str,
    accepted: bool,
) -> None:
    original = "http://hurgada.olx.com.eg/example-iid-123"
    requested = "20130310000000"
    start = f"{wayback.REPLAY_PREFIX}/{requested}id_/{original}"
    final = f"{wayback.REPLAY_PREFIX}/{requested}id_/http://{served_host}/example-iid-123"
    frontier, log = InMemoryFrontier(), NullLogProvider()
    await frontier.add_captures(
        OlxEgWaybackDataSource.key, [Capture(surt_key(original), requested, original, "CDX")]
    )
    await frontier.set_route([1], status=CrawlStatus.QUEUED)
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        if len(visits) == 1:
            return httpx.Response(302, headers={"Location": final})
        return httpx.Response(200, content=b"historic")

    source = OlxEgWaybackDataSource(
        log=log,
        frontier=frontier,
        graphs=InMemoryGraphs(),
        engine=HtmlRuleEngine(),
        client=WaybackClient(FAST, log=log, transport=httpx.MockTransport(handler)),
    )
    try:
        payloads = [payload async for payload in source.fetch(FetchContext(max_items=1))]
        if accepted:
            assert len(payloads) == 1 and visits == [start, final]
            assert payloads[0].source_url == f"http://{served_host}/example-iid-123"
        else:
            assert payloads == [] and visits == [start]
            assert frontier.rows[1].status is CrawlStatus.FAILED
    finally:
        await source.aclose()
