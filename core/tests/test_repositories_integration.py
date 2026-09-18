"""Repository behaviour against a real PostgreSQL database.

The haversine search uses Postgres maths functions and Tortoise's ``RawSQL``, so
it cannot be exercised on SQLite. These tests skip unless
``REALESTATE_TEST_DB_URL`` points at a reachable database:

    REALESTATE_TEST_DB_URL=postgres://realestate:realestate@127.0.0.1:5432/realestate \\
      ./venv/bin/pytest tests/test_repositories_integration.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from realestate.domain.enums import (
    ListingType,
    PropertyType,
    RawDocumentKind,
    RawDocumentStatus,
    RunStatus,
    RunTrigger,
    SortOrder,
)
from realestate.domain.models import BlobRef, RawPayload
from realestate.domain.query import GeoFilter, ListingQuery
from realestate.infrastructure.db.repositories.listing import TortoiseListingRepository
from realestate.infrastructure.db.repositories.raw_document import (
    TortoiseRawDocumentRepository,
)
from realestate.infrastructure.db.repositories.scrape_run import TortoiseScrapeRunRepository
from tests.conftest import make_draft

pytestmark = pytest.mark.usefixtures("initialised_db")

DOWNTOWN_CAIRO = (Decimal("30.0444"), Decimal("31.2357"))


@pytest.fixture
def repo() -> TortoiseListingRepository:
    return TortoiseListingRepository()


async def test_upsert_inserts_then_recognises_unchanged(repo) -> None:  # type: ignore[no-untyped-def]
    drafts = [make_draft(external_id="a"), make_draft(external_id="b")]

    first = await repo.upsert_many(drafts, source_key="fixture")
    second = await repo.upsert_many(drafts, source_key="fixture")

    assert (first.created, first.updated, first.unchanged) == (2, 0, 0)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 2)
    assert await repo.count("fixture") == 2


async def test_unchanged_upsert_moves_last_seen_but_not_updated_at(repo) -> None:  # type: ignore[no-untyped-def]
    """`updated_at` must keep meaning 'when the advert really changed'."""
    draft = make_draft(external_id="a")
    await repo.upsert_many([draft], source_key="fixture")
    page = await repo.search(ListingQuery())
    before = page.items[0]

    await repo.upsert_many([draft], source_key="fixture")
    after = (await repo.search(ListingQuery())).items[0]

    assert after.updated_at == before.updated_at
    assert after.last_seen_at > before.last_seen_at


async def test_changed_draft_updates_the_row(repo) -> None:  # type: ignore[no-untyped-def]
    await repo.upsert_many(
        [make_draft(external_id="a", amount=Decimal("1000"))], source_key="fixture"
    )

    result = await repo.upsert_many(
        [make_draft(external_id="a", amount=Decimal("1500"))], source_key="fixture"
    )

    assert (result.created, result.updated, result.unchanged) == (0, 1, 0)
    listing = (await repo.search(ListingQuery())).items[0]
    assert listing.price.amount == Decimal("1500.00")


async def test_same_external_id_from_two_sources_stays_separate(repo) -> None:  # type: ignore[no-untyped-def]
    """The idempotency key is (source_key, external_id), not external_id alone."""
    await repo.upsert_many([make_draft(external_id="shared")], source_key="fixture")
    await repo.upsert_many([make_draft(external_id="shared")], source_key="other")

    assert await repo.count() == 2


async def test_duplicate_drafts_within_one_batch_collapse(repo) -> None:  # type: ignore[no-untyped-def]
    result = await repo.upsert_many(
        [make_draft(external_id="a"), make_draft(external_id="a", title="Later wins")],
        source_key="fixture",
    )

    assert result.created == 1
    assert (await repo.search(ListingQuery())).items[0].title == "Later wins"


async def test_radius_search_excludes_distant_listings(repo) -> None:  # type: ignore[no-untyped-def]
    await repo.upsert_many(
        [
            make_draft(external_id="downtown", latitude=30.0444, longitude=31.2357),
            make_draft(external_id="zamalek", latitude=30.0614, longitude=31.2197),
            make_draft(external_id="seattle", latitude=47.6145, longitude=-122.3448),
        ],
        source_key="fixture",
    )

    page = await repo.search(
        ListingQuery(geo=GeoFilter(*DOWNTOWN_CAIRO, radius_km=10), sort=SortOrder.DISTANCE)
    )

    assert page.total == 2
    assert [item.external_id for item in page.items] == ["downtown", "zamalek"]
    assert page.items[0].distance_km == pytest.approx(0.0, abs=0.01)
    assert page.items[1].distance_km == pytest.approx(2.44, abs=0.1)


async def test_radius_search_total_matches_the_filtered_rows(repo) -> None:  # type: ignore[no-untyped-def]
    """The haversine cut must land in WHERE, or the count would over-report."""
    await repo.upsert_many(
        [
            make_draft(external_id="near", latitude=30.0444, longitude=31.2357),
            make_draft(external_id="far", latitude=31.2001, longitude=29.9187),
        ],
        source_key="fixture",
    )

    page = await repo.search(
        ListingQuery(geo=GeoFilter(*DOWNTOWN_CAIRO, radius_km=5), limit=1)
    )

    assert page.total == 1
    assert len(page.items) == 1


async def test_listings_without_coordinates_are_excluded_from_radius_search(repo) -> None:  # type: ignore[no-untyped-def]
    await repo.upsert_many(
        [
            make_draft(external_id="located", latitude=30.0444, longitude=31.2357),
            make_draft(external_id="unlocated", latitude=None, longitude=None),
        ],
        source_key="fixture",
    )

    page = await repo.search(ListingQuery(geo=GeoFilter(*DOWNTOWN_CAIRO, radius_km=50)))

    assert [item.external_id for item in page.items] == ["located"]


async def test_filters_and_sorting(repo) -> None:  # type: ignore[no-untyped-def]
    await repo.upsert_many(
        [
            make_draft(
                external_id="cheap", amount=Decimal("500"), listing_type=ListingType.RENT
            ),
            make_draft(
                external_id="dear", amount=Decimal("5000"), listing_type=ListingType.RENT
            ),
            make_draft(
                external_id="sale",
                amount=Decimal("100000"),
                listing_type=ListingType.SALE,
                property_type=PropertyType.VILLA,
            ),
        ],
        source_key="fixture",
    )

    rentals = await repo.search(
        ListingQuery(listing_type=ListingType.RENT, sort=SortOrder.PRICE_ASC)
    )
    villas = await repo.search(ListingQuery(property_types=(PropertyType.VILLA,)))
    budget = await repo.search(ListingQuery(price_max=Decimal("1000")))

    assert [item.external_id for item in rentals.items] == ["cheap", "dear"]
    assert [item.external_id for item in villas.items] == ["sale"]
    assert [item.external_id for item in budget.items] == ["cheap"]


async def test_text_search_covers_title_and_description(repo) -> None:  # type: ignore[no-untyped-def]
    await repo.upsert_many(
        [
            make_draft(external_id="a", title="Flat in Zamalek"),
            make_draft(external_id="b", title="Flat", description="Quiet street, Zamalek"),
            make_draft(external_id="c", title="Flat in Maadi"),
        ],
        source_key="fixture",
    )

    page = await repo.search(ListingQuery(text="zamalek"))

    assert {item.external_id for item in page.items} == {"a", "b"}


async def test_paging_is_stable_across_pages(repo) -> None:  # type: ignore[no-untyped-def]
    await repo.upsert_many(
        [make_draft(external_id=f"n-{n}", amount=Decimal(n)) for n in range(10)],
        source_key="fixture",
    )

    first = await repo.search(ListingQuery(sort=SortOrder.PRICE_ASC, limit=4, offset=0))
    second = await repo.search(ListingQuery(sort=SortOrder.PRICE_ASC, limit=4, offset=4))

    assert first.total == second.total == 10
    ids = [item.external_id for item in first.items + second.items]
    assert len(set(ids)) == 8  # no overlap between the two pages


async def test_mark_stale_deactivates_unseen_listings(repo) -> None:  # type: ignore[no-untyped-def]
    await repo.upsert_many([make_draft(external_id="gone")], source_key="fixture")

    deactivated = await repo.mark_stale(
        "fixture", not_seen_since=datetime.now(UTC) + timedelta(minutes=1)
    )

    assert deactivated == 1
    assert (await repo.search(ListingQuery(is_active=True))).total == 0
    assert (await repo.search(ListingQuery(is_active=False))).total == 1


async def test_raw_document_lifecycle() -> None:
    documents = TortoiseRawDocumentRepository()
    payload = RawPayload(
        content=b"{}",
        kind=RawDocumentKind.JSON,
        content_type="application/json",
        external_id="page-1",
        meta={"page": 1},
    )
    blob = BlobRef(
        key="fixture/2026/01/01/x.json",
        uri="file:///tmp/x.json",
        size_bytes=2,
        sha256="a" * 64,
        content_type="application/json",
    )

    created = await documents.create(source_key="fixture", payload=payload, blob=blob)
    pending = await documents.list_by_status(source_key="fixture")
    await documents.mark_parsed(created.id)
    parsed = await documents.get(created.id)

    assert created.status is RawDocumentStatus.PENDING
    assert [document.id for document in pending] == [created.id]
    assert parsed is not None
    assert parsed.status is RawDocumentStatus.PARSED
    assert parsed.parsed_at is not None
    assert await documents.find_by_sha256("fixture", "a" * 64) is not None


async def test_failed_document_records_the_error_and_counts_attempts() -> None:
    documents = TortoiseRawDocumentRepository()
    blob = BlobRef(
        key="fixture/2026/01/01/y.json",
        uri="file:///tmp/y.json",
        size_bytes=2,
        sha256="b" * 64,
        content_type="application/json",
    )
    created = await documents.create(
        source_key="fixture",
        payload=RawPayload(
            content=b"{}", kind=RawDocumentKind.JSON, content_type="application/json"
        ),
        blob=blob,
    )

    await documents.mark_failed(created.id, "ParseError: selector broke")
    await documents.mark_failed(created.id, "ParseError: selector broke")
    failed = await documents.get(created.id)

    assert failed is not None
    assert failed.status is RawDocumentStatus.FAILED
    assert failed.attempts == 2
    assert "selector broke" in (failed.parse_error or "")


async def test_scrape_run_lifecycle_and_latest_per_source() -> None:
    runs = TortoiseScrapeRunRepository()

    first = await runs.start("fixture", RunTrigger.MANUAL)
    await runs.finish(first.id, status=RunStatus.SUCCESS, documents_fetched=3, listings_created=9)
    second = await runs.start("fixture", RunTrigger.SCHEDULED)
    await runs.finish(second.id, status=RunStatus.PARTIAL, errors=1)

    latest = await runs.latest_per_source()
    listed = await runs.list(source_key="fixture")

    assert latest["fixture"].id == second.id
    assert latest["fixture"].status is RunStatus.PARTIAL
    assert listed.total == 2
    assert listed.items[0].id == second.id  # newest first
    finished_first = await runs.get(first.id)
    assert finished_first is not None
    assert finished_first.listings_created == 9
    assert finished_first.finished_at is not None
