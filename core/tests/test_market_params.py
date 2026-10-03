"""Trend query validation and conversion to the domain query."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from realestate.application.dto.market import TrendQueryParams
from realestate.application.services.market_service import MarketTrendService
from realestate.domain.enums import ListingType, PriceType
from realestate.domain.exceptions import InvalidQueryError
from realestate.domain.market import TrendInterval, TrendMetric, TrendQuery


def test_defaults_and_normalisation() -> None:
    query = TrendQueryParams(city=" Cairo ", country_code="eg", currency="egp").to_domain(
        today=date(2020, 6, 30)
    )

    assert query.city == "Cairo"
    assert query.country_code == "EG"
    assert query.currency == "EGP"
    assert query.listing_type is ListingType.SALE
    assert query.metric is TrendMetric.MEDIAN_PRICE
    assert query.interval is TrendInterval.MONTH
    assert query.date_to == datetime(2020, 7, 1, tzinfo=UTC)
    assert query.date_from == datetime(1995, 6, 30, tzinfo=UTC)


def test_default_range_survives_leap_day() -> None:
    query = TrendQueryParams(city="Cairo").to_domain(today=date(2028, 2, 29))

    assert query.date_from == datetime(2003, 2, 28, tzinfo=UTC)


def test_price_type_follows_listing_type() -> None:
    sale = TrendQueryParams(city="Cairo", listing_type=ListingType.SALE).to_domain()
    rent = TrendQueryParams(city="Cairo", listing_type=ListingType.RENT).to_domain()

    assert sale.price_type is PriceType.TOTAL
    assert rent.price_type is PriceType.PER_MONTH


def test_region_label() -> None:
    assert TrendQueryParams(city="Cairo").to_domain().region_label == "Cairo"
    assert (
        TrendQueryParams(city="Cairo", district="Maadi").to_domain().region_label == "Cairo · Maadi"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"date_from": date(2020, 1, 2), "date_to": date(2020, 1, 1)},
        {"date_from": date(1990, 1, 1), "date_to": date(2020, 1, 1)},
        {"metric": "mean"},
        {"interval": "day"},
        {"unknown": 1},
        {"city": ""},
    ],
)
def test_rejects_invalid(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TrendQueryParams.model_validate({"city": "Cairo", **kwargs})


async def test_service_rejects_empty_range_from_direct_callers() -> None:
    moment = datetime(2020, 1, 1, tzinfo=UTC)
    query = TrendQuery(
        country_code="EG",
        city="Cairo",
        listing_type=ListingType.SALE,
        currency="EGP",
        date_from=moment,
        date_to=moment,
    )

    with pytest.raises(InvalidQueryError):
        await MarketTrendService(stats=None).trend(query)  # type: ignore[arg-type]
