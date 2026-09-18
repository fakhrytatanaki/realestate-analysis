"""The reference data source implementation."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from realestate.domain.enums import ListingType, PriceType, PropertyType, RawDocumentKind
from realestate.domain.exceptions import ParseError
from realestate.domain.models import FetchContext, RawPayload
from realestate.infrastructure.sources.fixture.source import FixtureDataSource
from tests.conftest import FIXTURE_DIR, NullLogProvider


@pytest.fixture
def source(null_log: NullLogProvider) -> FixtureDataSource:
    return FixtureDataSource(log=null_log, directory=FIXTURE_DIR)


async def test_fetch_yields_one_payload_per_file(source: FixtureDataSource) -> None:
    payloads = [payload async for payload in source.fetch(FetchContext())]

    assert len(payloads) == len(list(FIXTURE_DIR.glob("*.json")))
    assert all(payload.kind is RawDocumentKind.JSON for payload in payloads)
    assert all(payload.content for payload in payloads)


async def test_fetch_respects_max_items(source: FixtureDataSource) -> None:
    payloads = [payload async for payload in source.fetch(FetchContext(max_items=1))]

    assert len(payloads) == 1


async def test_parse_normalises_portal_vocabulary(source: FixtureDataSource) -> None:
    payload = await _payload_for(source, "cairo_rentals.json")

    drafts = await source.parse(payload)

    maadi = next(draft for draft in drafts if draft.external_id == "eg-rent-1001")
    assert maadi.listing_type is ListingType.RENT
    assert maadi.property_type is PropertyType.APARTMENT
    assert maadi.price.price_type is PriceType.PER_MONTH
    assert maadi.price.amount == Decimal("18000")
    assert maadi.price.currency == "EGP"
    assert maadi.bedrooms == 2
    assert maadi.location.city == "Cairo"
    assert maadi.location.point is not None
    assert maadi.location.point.latitude == Decimal("29.9602")


async def test_parse_keeps_installment_terms(source: FixtureDataSource) -> None:
    payload = await _payload_for(source, "cairo_sales.json")

    drafts = await source.parse(payload)

    installment = next(draft for draft in drafts if draft.external_id == "eg-sale-2001")
    assert installment.price.price_type is PriceType.INSTALLMENT
    assert installment.price.installment_plan == {
        "down_payment": 1300000,
        "months": 96,
        "monthly_amount": 54167,
    }


async def test_unmapped_category_falls_back_to_other(
    source: FixtureDataSource, tmp_path: Path, null_log: NullLogProvider
) -> None:
    """An unknown category must not lose the ad."""
    payload = _json_payload({"results": [_item(category="houseboat")]})

    drafts = await source.parse(payload)

    assert drafts[0].property_type is PropertyType.OTHER


async def test_one_broken_item_does_not_lose_the_page(
    source: FixtureDataSource, null_log: NullLogProvider
) -> None:
    payload = _json_payload({"results": [_item(), {"no": "id"}, _item(item_id="ok-2")]})

    drafts = await source.parse(payload)

    assert [draft.external_id for draft in drafts] == ["ok-1", "ok-2"]
    assert any("skipping unparseable item" in record[1] for record in null_log.records)


async def test_invalid_json_raises_parse_error(source: FixtureDataSource) -> None:
    payload = RawPayload(
        content=b"<html>not json</html>",
        kind=RawDocumentKind.JSON,
        content_type="application/json",
    )

    with pytest.raises(ParseError):
        await source.parse(payload)


async def test_missing_results_array_raises_parse_error(source: FixtureDataSource) -> None:
    with pytest.raises(ParseError):
        await source.parse(_json_payload({"page": 1}))


async def test_parse_is_deterministic(source: FixtureDataSource) -> None:
    """Replays must produce identical drafts, or re-parsing history is unsafe."""
    payload = await _payload_for(source, "cairo_rentals.json")

    first = await source.parse(payload)
    second = await source.parse(payload)

    assert first == second


async def _payload_for(source: FixtureDataSource, filename: str) -> RawPayload:
    async for payload in source.fetch(FetchContext()):
        if payload.meta.get("file") == filename:
            return payload
    raise AssertionError(f"fixture {filename} not found")


def _json_payload(document: dict) -> RawPayload:
    return RawPayload(
        content=json.dumps(document).encode(),
        kind=RawDocumentKind.JSON,
        content_type="application/json",
        meta={"file": "inline.json"},
    )


def _item(item_id: str = "ok-1", category: str = "apartment") -> dict:
    return {
        "id": item_id,
        "title": "An advert",
        "purpose": "rent",
        "category": category,
        "price": {"value": 100, "currency": "EGP", "period": "monthly"},
        "location": {"name": "Somewhere", "city": "Cairo", "country": "EG"},
    }
