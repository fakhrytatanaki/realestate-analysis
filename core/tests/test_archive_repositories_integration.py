"""Archive and rule repositories, and observation ordering, against PostgreSQL.

Skipped unless ``REALESTATE_TEST_DB_URL`` points at a database (see
``test_repositories_integration.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from realestate.application.rules.seeds import seed_navigation_graph
from realestate.domain.archive import Capture
from realestate.domain.enums import (
    CrawlStatus,
    GapStatus,
    LinkRel,
    NodeKind,
    PageKind,
    RuleDomain,
    RuleGraphStatus,
)
from realestate.domain.models import Price
from realestate.domain.rules import ROOT_KEY, RuleEdge, RuleNode
from realestate.infrastructure.db.models import ListingModel, ListingObservationModel
from realestate.infrastructure.db.repositories.crawl import (
    TortoiseCrawlCursorRepository,
    TortoiseCrawlFrontierRepository,
)
from realestate.infrastructure.db.repositories.listing import TortoiseListingRepository
from realestate.infrastructure.db.repositories.rules import (
    TortoiseLlmDecisionRepository,
    TortoiseRuleGapRepository,
    TortoiseRuleGraphRepository,
)
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
    queued = await frontier.next_queued("s", limit=5)
    assert [(e.id, e.priority, e.page_kind) for e in queued] == [(a[1].id, 90, PageKind.LIST)]
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
