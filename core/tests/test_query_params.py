"""Search parameter validation and conversion."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from realestate.application.dto.query import ListingQueryParams
from realestate.domain.enums import ListingType, PropertyType, SortOrder
from realestate.domain.query import MAX_LIMIT


def test_defaults_restrict_to_active_listings() -> None:
    query = ListingQueryParams().to_domain()

    assert query.is_active is True
    assert query.sort is SortOrder.LISTED_AT_DESC
    assert query.geo is None


def test_filters_convert_to_the_domain_query() -> None:
    params = ListingQueryParams(
        q="maadi",
        listing_type=ListingType.RENT,
        property_type=[PropertyType.APARTMENT, PropertyType.STUDIO],
        price_max=Decimal("20000"),
        currency="egp",
        city="Cairo",
    )

    query = params.to_domain()

    assert query.text == "maadi"
    assert query.listing_type is ListingType.RENT
    assert query.property_types == (PropertyType.APARTMENT, PropertyType.STUDIO)
    assert query.price_max == Decimal("20000")
    assert query.currency == "egp"  # normalised to upper case by the repository


def test_geo_parts_must_be_supplied_together() -> None:
    with pytest.raises(ValidationError, match="must be supplied together"):
        ListingQueryParams(lat=Decimal("30.04"))


def test_geo_filter_is_built_from_all_three_parts() -> None:
    query = ListingQueryParams(
        lat=Decimal("30.0444"), lon=Decimal("31.2357"), radius_km=5
    ).to_domain()

    assert query.geo is not None
    assert query.geo.radius_km == 5


def test_distance_sort_requires_a_centre_point() -> None:
    with pytest.raises(ValidationError, match="sort=distance requires"):
        ListingQueryParams(sort=SortOrder.DISTANCE)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"price_min": 100, "price_max": 10}, "price_min must not exceed"),
        ({"area_min": 100, "area_max": 10}, "area_min must not exceed"),
        ({"bedrooms_min": 5, "bedrooms_max": 2}, "bedrooms_min must not exceed"),
    ],
)
def test_inverted_ranges_are_rejected(kwargs: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ListingQueryParams(**kwargs)


def test_page_size_is_capped() -> None:
    with pytest.raises(ValidationError):
        ListingQueryParams(limit=MAX_LIMIT + 1)


def test_unknown_parameters_are_rejected() -> None:
    """A typo'd filter must fail loudly rather than silently return everything."""
    with pytest.raises(ValidationError):
        ListingQueryParams(price_maximum=100)


def test_radius_has_an_upper_bound() -> None:
    with pytest.raises(ValidationError):
        ListingQueryParams(lat=Decimal("30"), lon=Decimal("31"), radius_km=10_000)
