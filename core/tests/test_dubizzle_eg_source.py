"""Tests for the Dubizzle Egypt data source."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from realestate.domain.enums import ListingType, PriceType, PropertyType, RawDocumentKind
from realestate.domain.exceptions import ParseError
from realestate.domain.models import FetchContext, RawPayload
from realestate.infrastructure.sources.dubizzle_eg.source import (
    _DEFAULT_CATEGORIES,
    DubizzleEgDataSource,
)
from tests.conftest import FIXTURE_DIR, NullLogProvider


@pytest.fixture
def source(null_log: NullLogProvider) -> DubizzleEgDataSource:
    return DubizzleEgDataSource(log=null_log)


@pytest.fixture
def apartments_payload(source: DubizzleEgDataSource) -> RawPayload:
    path = FIXTURE_DIR / "dubizzle_eg_apartments_sale.json"
    content = path.read_bytes()
    return RawPayload(
        content=content,
        kind=RawDocumentKind.JSON,
        content_type="application/json",
        source_url="https://search.olx.com.eg/_msearch",
        external_id="dubizzle_eg:apartments-duplex-for-sale:page0",
        meta={"category": "apartments-duplex-for-sale", "page": 0, "from": 0, "size": 50},
    )


async def test_parse_normalises_apartment_for_sale(
    source: DubizzleEgDataSource, apartments_payload: RawPayload
) -> None:
    drafts = await source.parse(apartments_payload)

    assert len(drafts) == 3
    first = next(d for d in drafts if d.external_id == "504114527")
    assert first.title == "Super Lux Apartment for Sale in Al-Andalus"
    assert first.listing_type is ListingType.SALE
    assert first.property_type is PropertyType.APARTMENT
    assert first.price.amount == Decimal("5000000")
    assert first.price.currency == "EGP"
    assert first.price.price_type is PriceType.TOTAL
    assert first.area_sqm == Decimal("160")
    assert first.bedrooms == 3
    assert first.bathrooms == 2
    assert first.location.city == "Cairo"
    assert first.location.country_code == "EG"
    assert first.location.point is not None
    assert first.location.point.latitude == Decimal("29.99352595")
    assert first.location.point.longitude == Decimal("31.5196573")
    assert first.is_active is True
    assert first.url == "https://www.dubizzle.com.eg/en/ad/super-lux-apartment-for-sale-in-al-andalus-ID504114527.html"


async def test_parse_maps_installment_price(source: DubizzleEgDataSource) -> None:
    payload = _payload(_installment_hit())

    drafts = await source.parse(payload)

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.price.price_type is PriceType.INSTALLMENT
    assert draft.price.installment_plan == {"down_payment": 435000}


async def test_parse_maps_rental_price(source: DubizzleEgDataSource) -> None:
    payload = _payload(_rent_hit())

    drafts = await source.parse(payload)

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.listing_type is ListingType.RENT
    assert draft.price.price_type is PriceType.PER_MONTH


async def test_parse_maps_daily_rental_price(source: DubizzleEgDataSource) -> None:
    payload = _payload(_rent_hit(rental_period="1"))

    drafts = await source.parse(payload)

    assert drafts[0].price.price_type is PriceType.PER_NIGHT


async def test_parse_maps_studio_subtype(source: DubizzleEgDataSource) -> None:
    payload = _payload(_sale_hit(type_code="4"))

    drafts = await source.parse(payload)

    assert drafts[0].property_type is PropertyType.STUDIO


async def test_parse_maps_townhouse_subtype(source: DubizzleEgDataSource) -> None:
    payload = _payload(
        _sale_hit(category="villas-for-sale", type_code="3", formatted_type="Town House"),
        category="villas-for-sale",
    )

    drafts = await source.parse(payload)

    assert drafts[0].property_type is PropertyType.TOWNHOUSE


async def test_parse_keeps_extra_attributes(source: DubizzleEgDataSource) -> None:
    payload = _payload(_sale_hit())

    drafts = await source.parse(payload)

    attrs = drafts[0].attributes
    assert attrs.get("product") == "free"
    assert attrs.get("agency_name") == "Test Agency"
    assert attrs.get("formatted_type") == "Apartment"
    assert "price" in attrs


async def test_one_broken_item_does_not_lose_the_page(
    source: DubizzleEgDataSource, null_log: NullLogProvider
) -> None:
    payload = _payload(_sale_hit(), {"_source": {"externalID": None}})

    drafts = await source.parse(payload)

    assert len(drafts) == 1
    assert drafts[0].external_id == "504114527"
    assert any("skipping unparseable item" in record[1] for record in null_log.records)


async def test_invalid_json_raises_parse_error(source: DubizzleEgDataSource) -> None:
    payload = RawPayload(
        content=b"<html>not json</html>",
        kind=RawDocumentKind.JSON,
        content_type="application/json",
    )

    with pytest.raises(ParseError):
        await source.parse(payload)


async def test_parse_is_deterministic(
    source: DubizzleEgDataSource, apartments_payload: RawPayload
) -> None:
    first = await source.parse(apartments_payload)
    second = await source.parse(apartments_payload)

    assert first == second


async def test_fetch_yields_one_payload_per_page(source: DubizzleEgDataSource) -> None:
    responses = [json.dumps(_empty_response()).encode()]
    _patch_request(source, responses)

    payloads = [payload async for payload in source.fetch(FetchContext(page_limit=1))]

    assert len(payloads) == len(_DEFAULT_CATEGORIES)
    assert all(payload.kind is RawDocumentKind.JSON for payload in payloads)
    assert all(payload.content for payload in payloads)


async def test_fetch_respects_page_limit(source: DubizzleEgDataSource) -> None:
    _patch_request(source, [json.dumps(_empty_response()).encode()])

    payloads = [payload async for payload in source.fetch(FetchContext(page_limit=2))]

    assert len(payloads) == len(_DEFAULT_CATEGORIES) * 2


async def test_fetch_respects_max_items(source: DubizzleEgDataSource) -> None:
    _patch_request(source, [json.dumps(_empty_response()).encode()])

    payloads = [
        payload async for payload in source.fetch(FetchContext(max_items=2))
    ]

    assert len(payloads) == 2


async def test_fetch_respects_since(source: DubizzleEgDataSource) -> None:
    captured: list[bytes] = []

    async def fake_request(method: str, url: str, **kwargs: Any) -> Any:
        captured.append(kwargs.get("content", b""))
        return _Response(json.dumps(_empty_response()).encode())

    source.request = fake_request  # type: ignore[method-assign]

    since = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    async for _ in source.fetch(FetchContext(page_limit=1, since=since)):
        pass

    assert captured
    body = captured[0].decode()
    assert "range" in body
    assert str(since.timestamp()) in body


async def test_healthcheck_happy_path(source: DubizzleEgDataSource) -> None:
    _patch_request(source, [json.dumps(_empty_response()).encode()])

    assert await source.healthcheck() is True


async def test_healthcheck_failure(source: DubizzleEgDataSource) -> None:
    async def failing_request(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("boom")

    source.request = failing_request  # type: ignore[method-assign]

    assert await source.healthcheck() is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_request(source: DubizzleEgDataSource, responses: Sequence[bytes]) -> None:
    iterator = iter(responses)

    async def fake_request(method: str, url: str, **kwargs: Any) -> Any:
        try:
            content = next(iterator)
        except StopIteration:
            content = json.dumps(_empty_response()).encode()
        return _Response(content)

    source.request = fake_request  # type: ignore[method-assign]


def _payload(*hits: dict[str, Any], category: str = "apartments-duplex-for-sale") -> RawPayload:
    document = {
        "took": 1,
        "responses": [{"hits": {"total": {"value": len(hits)}, "hits": list(hits)}}],
    }
    return RawPayload(
        content=json.dumps(document).encode(),
        kind=RawDocumentKind.JSON,
        content_type="application/json",
        source_url="https://search.olx.com.eg/_msearch",
        external_id=f"dubizzle_eg:{category}:page0",
        meta={"category": category, "page": 0, "from": 0, "size": 50},
    )


def _empty_response() -> dict[str, Any]:
    return {"took": 1, "responses": [{"hits": {"total": {"value": 0}, "hits": []}}]}


def _base_hit(
    *,
    category: str = "apartments-duplex-for-sale",
    purpose: str = "for-sale",
    type_code: str = "1",
    formatted_type: str = "Apartment",
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extras: dict[str, Any] = {
        "ft": 120,
        "type": type_code,
        "price": 2500000,
        "rooms": "2",
        "bathrooms": "2",
        "price_type": "price",
        "payment_option": "1",
        "completion_status": "1",
    }
    if extra_fields:
        extras.update(extra_fields)

    return {
        "_source": {
            "id": 12345,
            "objectID": 12345,
            "state": "active",
            "purpose": purpose,
            "geography": {"lat": 30.0444, "lng": 31.2357},
            "title": "Test title",
            "title_l1": "Test English title",
            "description_l1": "Test description",
            "externalID": "504114527",
            "slug": "test-slug",
            "slug_l1": "test-english-slug",
            "createdAt": 1700000000.0,
            "updatedAt": 1700000000.0,
            "timestamp": 1700000000.0,
            "extraFields": extras,
            "type": "general",
            "product": "free",
            "agency": {"name": "Test Agency", "name_l1": "Test Agency"},
            "photoCount": 5,
            "isSellerVerified": False,
            "formattedExtraFields": [
                {"attribute": "type", "formattedValue": "شقة", "formattedValue_l1": formatted_type},
            ],
            "location.lvl0": {"name_l1": "Egypt"},
            "location.lvl1": {"name_l1": "Cairo"},
            "location.lvl2": {"name_l1": "New Cairo"},
            "location.lvl3": {"name_l1": "5th Settlement"},
            "category.lvl0": {"externalID": "138"},
            "category.lvl1": {"slug": category},
            "category": [{"slug": category}],
        }
    }


def _sale_hit(**overrides: Any) -> dict[str, Any]:
    return _base_hit(**overrides)


def _installment_hit() -> dict[str, Any]:
    return _base_hit(
        extra_fields={
            "ft": 89,
            "type": "1",
            "price": 8700000,
            "rooms": "1",
            "bathrooms": "2",
            "price_type": "price",
            "payment_option": "2",
            "down_payment": 435000,
            "completion_status": "1",
        }
    )


def _rent_hit(rental_period: str = "3") -> dict[str, Any]:
    return _base_hit(
        category="apartments-duplex-for-rent",
        purpose="for-rent",
        extra_fields={
            "ft": 100,
            "type": "1",
            "price": 15000,
            "rooms": "2",
            "bathrooms": "1",
            "price_type": "price",
            "rental_period": rental_period,
        },
    )


