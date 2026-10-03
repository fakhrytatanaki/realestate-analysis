"""Archive and rule repositories, and observation ordering, against PostgreSQL.

Skipped unless ``REALESTATE_TEST_DB_URL`` points at a database (see
``test_repositories_integration.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from realestate.application.rules.seeds import seed_navigation_graph
from realestate.application.services.initial_rule_seed_service import InitialRuleSeedService
from realestate.domain.archive import Capture, LinkRequest
from realestate.domain.enums import (
    CrawlStatus,
    GapStatus,
    LinkRel,
    LinkRequestStatus,
    NodeKind,
    PageKind,
    RawDocumentKind,
    RawDocumentStatus,
    RuleDomain,
    RuleGraphStatus,
    RunStatus,
    RunTrigger,
)
from realestate.domain.models import BlobRef, Price, RawPayload
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleNode
from realestate.infrastructure.archive.rate_gate import PostgresRateGate
from realestate.infrastructure.db.models import (
    ArchiveRateGateModel,
    CrawlLinkRequestModel,
    ListingModel,
    ListingObservationModel,
)
from realestate.infrastructure.db.repositories.crawl import (
    TortoiseCrawlCursorRepository,
    TortoiseCrawlFrontierRepository,
    TortoiseLinkRequestRepository,
)
from realestate.infrastructure.db.repositories.listing import TortoiseListingRepository
from realestate.infrastructure.db.repositories.raw_document import TortoiseRawDocumentRepository
from realestate.infrastructure.db.repositories.rules import (
    TortoiseLlmDecisionRepository,
    TortoiseRuleGapRepository,
    TortoiseRuleGraphRepository,
)
from realestate.infrastructure.db.repositories.scrape_run import TortoiseScrapeRunRepository
from realestate.infrastructure.extraction.seeds import PackagedRuleSeedProvider
from tests.conftest import make_draft

pytestmark = pytest.mark.usefixtures("initialised_db")
T0 = datetime(2013, 3, 5, tzinfo=UTC)


async def test_frontier_lifecycle() -> None:
    frontier = TortoiseCrawlFrontierRepository()
    captures = [
        Capture("eg,com,olx)/a", "20130101000000", "http://olx.com.eg/a", "D1"),
        Capture("eg,com,olx)/a", "20130201000000", "http://olx.com.eg/a", "D2"),
        Capture("eg,com,olx)/b", "20130101000000", "http://olx.com.eg/b", "D3"),
    ]
    assert await frontier.add_captures("s", captures) == 3
    assert await frontier.add_captures("s", captures) == 0  # idempotent

    keys = await frontier.url_keys_with_status("s", [CrawlStatus.DISCOVERED], limit=10)
    assert sorted(keys) == ["eg,com,olx)/a", "eg,com,olx)/b"]
    a = await frontier.captures_for("s", ["eg,com,olx)/a"])
    assert [entry.timestamp for entry in a] == ["20130101000000", "20130201000000"]

    await frontier.set_route(
        [a[1].id], status=CrawlStatus.QUEUED, route_node="n", priority=90, page_kind=PageKind.LIST
    )
    await frontier.set_route([a[0].id], status=CrawlStatus.SKIPPED)
    now = datetime.now(UTC)
    queued = await frontier.claim_queued("s", limit=5, now=now)
    assert [(e.id, e.priority, e.page_kind) for e in queued] == [(a[1].id, 90, PageKind.LIST)]
    assert queued[0].status is CrawlStatus.FETCHING
    assert await frontier.claim_queued("s", limit=5, now=now) == []  # already claimed
    # A claim from a run that died is released; a fresh one is not.
    assert await frontier.release_stale_claims("s", claimed_before=now - timedelta(minutes=1)) == 0
    assert await frontier.release_stale_claims("s", claimed_before=now + timedelta(seconds=1)) == 1
    queued = await frontier.claim_queued("s", limit=5, now=now)
    await frontier.mark_fetched(a[1].id)

    b = (await frontier.captures_for("s", ["eg,com,olx)/b"]))[0]
    await frontier.set_route([b.id], status=CrawlStatus.DEFERRED)
    assert (
        await frontier.add_evidence(
            "s", {"eg,com,olx)/b": LinkRel.DETAIL, "eg,com,olx)/zzz": LinkRel.LIST}
        )
        == 1
    )
    b = (await frontier.captures_for("s", ["eg,com,olx)/b"]))[0]
    assert b.status is CrawlStatus.DISCOVERED and b.evidence == {"linked_as": ["DETAIL"]}

    counts = await frontier.counts("s")
    assert (
        counts[CrawlStatus.FETCHED],
        counts[CrawlStatus.SKIPPED],
        counts[CrawlStatus.DISCOVERED],
    ) == (1, 1, 1)
    await frontier.mark_failed(b.id, "boom")
    assert (
        await frontier.reset_status(
            "s", from_status=CrawlStatus.FAILED, to_status=CrawlStatus.QUEUED
        )
        == 1
    )
    assert await frontier.sample_urls("s", CrawlStatus.QUEUED, limit=5) == ["http://olx.com.eg/b"]

    # A retryable failure waits in the queue until its retry time.
    claimed = await frontier.claim_queued("s", limit=5, now=now)
    assert [e.id for e in claimed] == [b.id]
    await frontier.mark_failed(b.id, "timeout", retry_at=now + timedelta(minutes=10))
    assert await frontier.claim_queued("s", limit=5, now=now) == []
    retried = await frontier.claim_queued("s", limit=5, now=now + timedelta(minutes=11))
    assert [(e.id, e.attempts) for e in retried] == [(b.id, 2)]

    quarters = await frontier.status_by_quarter("s")
    assert quarters[("2013Q1", CrawlStatus.FETCHED)] == 1

    await frontier.set_route([a[0].id], status=CrawlStatus.SKIPPED, route_node="n#capture-cap")
    assert await frontier.reopen_capped("s") == 1
    assert (await frontier.captures_for("s", ["eg,com,olx)/a"]))[0].status is (
        CrawlStatus.DISCOVERED
    )

    cursors = TortoiseCrawlCursorRepository()
    assert await cursors.get("s", "cdx:2013") == (None, False)
    await cursors.save("s", "cdx:2013", resume_key="RK", done=False)
    await cursors.save("s", "cdx:2013", resume_key=None, done=True)
    assert await cursors.get("s", "cdx:2013") == (None, True)


async def test_rule_graph_versions_round_trip() -> None:
    graphs = TortoiseRuleGraphRepository()
    assert await graphs.active("s", RuleDomain.NAVIGATION) is None
    v1 = await graphs.save_version(seed_navigation_graph("s"))
    v2 = await graphs.save_version(
        v1.extended(
            nodes=[RuleNode("skip", NodeKind.ROUTE, {"decision": "SKIP"})],
            edges=[
                RuleEdge(ROOT_KEY, "skip", {"type": "url_regex", "pattern": "/cars/"}, priority=110)
            ],
            vocab={"listing_type": [{"pattern": "rent", "value": "RENT"}]},
        )
    )
    active = await graphs.active("s", RuleDomain.NAVIGATION)
    assert active is not None and active.version == 2 and active.parent_id == v1.id
    assert [n.key for n in active.nodes] == [n.key for n in v2.nodes]
    assert active.edges[-1].condition == {"type": "url_regex", "pattern": "/cars/"}
    assert active.vocab == {"listing_type": [{"pattern": "rent", "value": "RENT"}]}
    old = await graphs.get("s", RuleDomain.NAVIGATION, 1)
    assert old is not None and old.status is RuleGraphStatus.RETIRED
    assert [g.version for g in await graphs.versions("s", RuleDomain.NAVIGATION)] == [2, 1]


async def test_packaged_seed_graphs_round_trip_and_repeated_installation() -> None:
    source_key = "dubizzle_eg_wayback"
    graphs = TortoiseRuleGraphRepository()
    seeds = PackagedRuleSeedProvider()
    service = InitialRuleSeedService(graphs=graphs, seeds=seeds)
    assert len((await service.seed(source_key)).installed) == 2
    for expected in seeds.load(source_key):
        actual = await graphs.active(source_key, expected.domain)
        assert actual is not None and actual.version == 1
        assert actual.nodes == expected.nodes and actual.edges == expected.edges
        assert actual.vocab == expected.vocab
    assert not (await service.seed(source_key)).installed
    for domain in RuleDomain:
        assert len(await graphs.versions(source_key, domain)) == 1


async def test_gaps_and_llm_ledger() -> None:
    gaps = TortoiseRuleGapRepository()
    gap = await gaps.record(
        "s", RuleDomain.NAVIGATION, "shape-a", samples=["u1", "u2"], occurrences=2
    )
    gap = await gaps.record(
        "s", RuleDomain.NAVIGATION, "shape-a", samples=["u2", "u3"], occurrences=1
    )
    assert gap.occurrences == 3 and gap.samples == ["u1", "u2", "u3"]
    await gaps.reset_open("s", RuleDomain.NAVIGATION)
    assert (await gaps.list("s", RuleDomain.NAVIGATION))[0].occurrences == 0
    failed = await gaps.record_failure(gap.id, "nope", max_attempts=1)
    assert failed.status is GapStatus.FAILED
    await gaps.mark_resolved(gap.id, version=3)
    reopened = await gaps.record("s", RuleDomain.NAVIGATION, "shape-a", samples=["u9"])
    assert reopened.status is GapStatus.OPEN and reopened.occurrences == 1

    ledger = TortoiseLlmDecisionRepository()
    decision = await ledger.record(
        task="t",
        input_fp="fp",
        model="m",
        prompt_version="v1",
        request={"x": 1},
        response={"rules": []},
        raw_text="{}",
        valid=False,
        tokens_in=10,
        tokens_out=4,
    )
    assert await ledger.find_valid("t", "fp", "m", "v1") is None
    await ledger.mark_valid(decision.id, True)
    found = await ledger.find_valid("t", "fp", "m", "v1")
    assert found is not None and found.response == {"rules": []}
    assert await ledger.totals() == {"calls": 1, "valid": 1, "tokens_in": 10, "tokens_out": 4}


async def test_upsert_keeps_the_latest_observation_whatever_the_order() -> None:
    repo = TortoiseListingRepository()
    newer = make_draft(
        external_id="a", observed_at=T0 + timedelta(days=30), price=Price(Decimal("2000"), "EGP")
    )
    older = make_draft(external_id="a", observed_at=T0, price=Price(Decimal("1000"), "EGP"))

    first = await repo.upsert_many([newer], source_key="archive")
    late = await repo.upsert_many([older], source_key="archive")
    assert (first.created, late.unchanged) == (1, 1)

    row = await ListingModel.get(source_key="archive", external_id="a")
    assert row.price == Decimal("2000.00")  # the older capture did not overwrite it
    assert (row.first_observed_at, row.last_observed_at) == (T0, T0 + timedelta(days=30))
    history = await ListingObservationModel.filter(listing_id=row.id).order_by("observed_at")
    assert [(h.observed_at, h.price) for h in history] == [
        (T0, Decimal("1000.00")),
        (T0 + timedelta(days=30), Decimal("2000.00")),
    ]
    # Replaying the same capture adds nothing.
    await repo.upsert_many([older], source_key="archive")
    assert await ListingObservationModel.filter(listing_id=row.id).count() == 2


async def test_reparsing_a_capture_replaces_its_observation() -> None:
    repo = TortoiseListingRepository()
    first = make_draft(external_id="r", observed_at=T0, price=Price(Decimal("1000"), "EGP"))
    # Same capture, better rules: a different price reading.
    better = make_draft(external_id="r", observed_at=T0, price=Price(Decimal("1500"), "EGP"))

    await repo.upsert_many([first], source_key="archive")
    await repo.upsert_many([better], source_key="archive")

    row = await ListingModel.get(source_key="archive", external_id="r")
    history = await ListingObservationModel.filter(listing_id=row.id)
    assert [h.price for h in history] == [Decimal("1500.00")]


async def test_partial_captures_do_not_erase_details() -> None:
    repo = TortoiseListingRepository()
    detailed = make_draft(
        external_id="p", observed_at=T0, bedrooms=3, area_sqm=Decimal("120"), title="Flat"
    )
    # A later capture from a page design that shows neither.
    sparse = make_draft(external_id="p", observed_at=T0 + timedelta(days=10), title="Flat!")
    await repo.upsert_many([detailed], source_key="archive")
    await repo.upsert_many([sparse], source_key="archive")

    row = await ListingModel.get(source_key="archive", external_id="p")
    assert (row.title, row.bedrooms, row.area_sqm) == ("Flat!", 3, Decimal("120.00"))

    # And an older capture arriving late fills what the current row lacks.
    late = make_draft(external_id="q", observed_at=T0 + timedelta(days=10))
    early = make_draft(external_id="q", observed_at=T0, bathrooms=2)
    await repo.upsert_many([late], source_key="archive")
    await repo.upsert_many([early], source_key="archive")
    row = await ListingModel.get(source_key="archive", external_id="q")
    assert (row.bathrooms, row.last_observed_at) == (2, T0 + timedelta(days=10))


async def test_live_sources_record_only_changes_as_history() -> None:
    repo = TortoiseListingRepository()
    draft = make_draft(external_id="live")
    await repo.upsert_many([draft], source_key="live")
    await repo.upsert_many([draft], source_key="live")  # unchanged re-scrape
    row = await ListingModel.get(source_key="live", external_id="live")
    assert await ListingObservationModel.filter(listing_id=row.id).count() == 1
    assert row.first_observed_at is not None


async def _raw_document(name: str, *, captured: str = "20130305032637") -> UUID:
    created = await TortoiseRawDocumentRepository().create(
        source_key="archive",
        payload=RawPayload(
            content=b"<html/>",
            kind=RawDocumentKind.HTML,
            content_type="text/html",
            meta={"timestamp": captured},
        ),
        blob=BlobRef(
            key=f"archive/{name}.html",
            uri=f"file:///tmp/{name}.html",
            size_bytes=7,
            sha256=name.ljust(64, "0")[:64],
            content_type="text/html",
        ),
    )
    return created.id


async def test_observations_snapshot_the_capture_and_merges_record_provenance() -> None:
    repo = TortoiseListingRepository()
    early_doc, late_doc = await _raw_document("early"), await _raw_document("late")
    detailed = make_draft(
        external_id="v",
        observed_at=T0,
        bedrooms=3,
        area_sqm=Decimal("120"),
        attributes={"_extraction": {"template": "tpl.a", "graph_version": 4}, "_raw": {"x": 1}},
    )
    sparse = make_draft(
        external_id="v",
        observed_at=T0 + timedelta(days=10),
        title="Flat!",
        attributes={"_extraction": {"template": "tpl.b", "graph_version": 5}},
    )
    await repo.upsert_many([detailed], source_key="archive", raw_document_id=early_doc)
    await repo.upsert_many([sparse], source_key="archive", raw_document_id=late_doc)

    row = await ListingModel.get(source_key="archive", external_id="v")
    assert row.raw_document_id == late_doc  # type: ignore[attr-defined]
    # Carried values name the capture they came from.
    assert row.attributes["_provenance"] == {
        "bedrooms": str(early_doc),
        "area_sqm": str(early_doc),
    }
    history = await ListingObservationModel.filter(listing_id=row.id).order_by("observed_at")
    first, second = history
    # Each observation is that capture alone, never the merged row.
    assert first.snapshot["bedrooms"] == 3 and second.snapshot["bedrooms"] is None
    assert first.snapshot["area_sqm"] == "120" and first.snapshot["raw"] == {"x": 1}
    assert (first.graph_version, first.template_key) == (4, "tpl.a")
    assert (second.graph_version, second.template_key) == (5, "tpl.b")


async def test_parse_provenance_filters_and_rebuild_primitives() -> None:
    documents = TortoiseRawDocumentRepository()
    old, new, missed = (
        await _raw_document("old"),
        await _raw_document("new"),
        await _raw_document("missed"),
    )
    await documents.mark_parsed(
        old, graph_version=3, report={"template_key": "t", "items_total": 2}
    )
    await documents.mark_parsed(new, graph_version=7)
    await documents.mark_unrecognised(
        missed, "no rule", graph_version=7, report={"page_kind": None}
    )

    stored = await documents.get(old)
    assert stored is not None and stored.parse_report == {"template_key": "t", "items_total": 2}
    assert stored.captured_at == T0.replace(hour=3, minute=26, second=37)
    stale = await documents.list_by_status(
        source_key="archive", status=RawDocumentStatus.PARSED, graph_version_below=7
    )
    assert [document.id for document in stale] == [old]

    moved = await documents.reset_status(
        "archive", from_statuses=(RawDocumentStatus.PARSED, RawDocumentStatus.UNRECOGNISED)
    )
    assert moved == 3
    assert len(await documents.list_by_status(source_key="archive")) == 3

    repo = TortoiseListingRepository()
    await repo.upsert_many(
        [
            make_draft(external_id="d1", observed_at=T0),
            make_draft(external_id="d2", observed_at=T0),
        ],
        source_key="archive",
    )
    assert [listing.external_id for listing in await repo.list_for_source("archive")] == [
        "d1",
        "d2",
    ]
    assert await repo.delete_source("archive") == 2
    assert await ListingObservationModel.all().count() == 0

    runs = TortoiseScrapeRunRepository()
    run = await runs.start("archive", RunTrigger.REPLAY)
    finished = await runs.finish(run.id, status=RunStatus.SUCCESS, stats={"documents": 3})
    assert finished.trigger is RunTrigger.REPLAY and finished.stats == {"documents": 3}


async def test_link_requests_and_frontier_lookups() -> None:
    frontier = TortoiseCrawlFrontierRepository()
    await frontier.add_captures(
        "s",
        [
            Capture("eg,com,olx)/known", "20130101000000", "http://olx.com.eg/known", "D1"),
            Capture("eg,com,olx)/other", "20120101000000", "http://olx.com.eg/other", "D2"),
        ],
    )
    known = await frontier.known_url_keys("s", ["eg,com,olx)/known", "eg,com,olx)/missing"])
    assert known == {"eg,com,olx)/known"}
    rows = await frontier.captures_with_status("s", [CrawlStatus.DISCOVERED])
    assert sorted(timestamp for _, timestamp in rows) == ["20120101000000", "20130101000000"]

    requests = TortoiseLinkRequestRepository()
    first = LinkRequest("s", "eg,com,olx)/p", "http://olx.com.eg/p", LinkRel.PAGINATION)
    detail = LinkRequest(
        "s", "eg,com,olx)/d", "http://olx.com.eg/d", LinkRel.DETAIL, parent_timestamp="2013"
    )
    assert await requests.request([first, detail]) == 2
    assert await requests.request([detail]) == 0  # already requested
    pending = await requests.pending("s", limit=10)
    assert [r.rel for r in pending] == [LinkRel.DETAIL, LinkRel.PAGINATION]
    assert pending[0].parent_timestamp == "2013" and pending[0].id is not None
    await requests.resolve(pending[0].id, status=LinkRequestStatus.FOUND, captures_found=2)
    await requests.resolve(pending[1].id or 0, status=LinkRequestStatus.PENDING, error="503")
    counts = await requests.counts("s")
    assert (counts[LinkRequestStatus.FOUND], counts[LinkRequestStatus.PENDING]) == (1, 1)
    retried = await requests.pending("s", limit=10)
    assert retried[0].attempts == 1
    await CrawlLinkRequestModel.all().delete()


async def test_postgres_rate_gate_shares_one_clock() -> None:
    gate, other = PostgresRateGate(), PostgresRateGate()  # two "workers"
    name = f"test-gate-{datetime.now(UTC).timestamp()}"
    waits = [await gate.reserve(name, interval=5), await other.reserve(name, interval=5)]
    waits.append(await gate.reserve(name, interval=5))
    assert waits[0] == 0 and 4 < waits[1] <= 5 and 9 < waits[2] <= 10
    await other.hold(name, seconds=60)
    assert 59 < await gate.reserve(name, interval=5) <= 60
    await ArchiveRateGateModel.filter(name=name).delete()
