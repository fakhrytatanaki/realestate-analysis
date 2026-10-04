"""Trend query validation and conversion to the domain query."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from realestate.application.dto.market import TrendQueryParams
from realestate.application.services.market_service import MarketTrendService
from realestate.domain.enums import ListingType, PriceType
from realestate.domain.exceptions import InvalidQueryError
from realestate.domain.market import Place, TrendInterval, TrendMetric, TrendQuery, pool_places


def test_defaults_and_normalisation() -> None:
    query = TrendQueryParams(
        place=[" Cairo ", "Giza / Sheikh Zayed "], country_code="eg", currency="egp"
    ).to_domain(today=date(2020, 6, 30))

    assert query.places == (Place("Cairo"), Place("Giza", "Sheikh Zayed"))
    assert query.country_code == "EG"
    assert query.currency == "EGP"
    assert query.listing_type is ListingType.SALE
    assert query.metric is TrendMetric.MEDIAN_PRICE
    assert query.interval is TrendInterval.MONTH
    assert query.date_to == datetime(2020, 7, 1, tzinfo=UTC)
    assert query.date_from == datetime(1995, 6, 30, tzinfo=UTC)


def test_default_range_survives_leap_day() -> None:
    query = TrendQueryParams(place=["Cairo"]).to_domain(today=date(2028, 2, 29))

    assert query.date_from == datetime(2003, 2, 28, tzinfo=UTC)


def test_price_type_follows_listing_type() -> None:
    sale = TrendQueryParams(listing_type=ListingType.SALE).to_domain()
    rent = TrendQueryParams(listing_type=ListingType.RENT).to_domain()

    assert sale.price_type is PriceType.TOTAL
    assert rent.price_type is PriceType.PER_MONTH


def test_region_label() -> None:
    def label(*places: str) -> str:
        return TrendQueryParams(place=list(places)).to_domain().region_label

    assert label("Cairo") == "Cairo"
    assert label("Cairo/Maadi") == "Cairo · Maadi"
    assert label("Cairo", "Giza/Dokki") == "Cairo + Giza · Dokki"
    assert label() == "All of EG"


def test_no_place_means_the_whole_country() -> None:
    assert TrendQueryParams().to_domain().places == ()


def test_district_keeps_later_slashes() -> None:
    [place] = TrendQueryParams(place=["Giza/6th of October/West"]).to_domain().places

    assert place == Place("Giza", "6th of October/West")


def test_pooling_drops_repeats_and_covered_districts() -> None:
    places = [
        Place("Cairo", "Maadi"),
        Place("Giza", "Dokki"),
        Place("Cairo"),
        Place("Giza", "Dokki"),
        Place("Giza", "Agouza"),
    ]

    assert pool_places(places) == (Place("Giza", "Dokki"), Place("Cairo"), Place("Giza", "Agouza"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"date_from": date(2020, 1, 2), "date_to": date(2020, 1, 1)},
        {"date_from": date(1990, 1, 1), "date_to": date(2020, 1, 1)},
        {"metric": "mean"},
        {"interval": "day"},
        {"unknown": 1},
        {"city": "Cairo"},
        {"place": [""]},
        {"place": ["/Maadi"]},
        {"place": ["Cairo/" + "x" * 129]},
        {"place": [f"City {i}" for i in range(51)]},
    ],
)
def test_rejects_invalid(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TrendQueryParams.model_validate({"place": ["Cairo"], **kwargs})


async def test_service_rejects_empty_range_from_direct_callers() -> None:
    moment = datetime(2020, 1, 1, tzinfo=UTC)
    query = TrendQuery(
        country_code="EG",
        listing_type=ListingType.SALE,
        currency="EGP",
        date_from=moment,
        date_to=moment,
    )

    with pytest.raises(InvalidQueryError):
        await MarketTrendService(stats=None).trend(query)  # type: ignore[arg-type]
