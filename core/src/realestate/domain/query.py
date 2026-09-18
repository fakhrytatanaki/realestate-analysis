"""Framework-free description of a listing search.

The API layer validates user input into a pydantic DTO and converts it to this
object, so repositories never see a web framework type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from realestate.domain.enums import ListingType, PriceType, PropertyType, SortOrder

#: Hard ceiling on page size, enforced wherever a query is built.
MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class GeoFilter:
    """Radius search around a point. All three parts are required together."""

    latitude: Decimal
    longitude: Decimal
    radius_km: float


@dataclass(frozen=True, slots=True)
class ListingQuery:
    """Every filter the listing search supports.

    ``None`` means "do not filter on this field"; empty sequences likewise.
    """

    text: str | None = None
    source_keys: tuple[str, ...] = ()
    listing_type: ListingType | None = None
    property_types: tuple[PropertyType, ...] = ()
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    currency: str | None = None
    price_types: tuple[PriceType, ...] = ()
    country_code: str | None = None
    city: str | None = None
    district: str | None = None
    location: str | None = None
    bedrooms_min: int | None = None
    bedrooms_max: int | None = None
    bathrooms_min: int | None = None
    area_min: Decimal | None = None
    area_max: Decimal | None = None
    geo: GeoFilter | None = None
    is_active: bool | None = True
    listed_after: datetime | None = None
    listed_before: datetime | None = None
    sort: SortOrder = SortOrder.LISTED_AT_DESC
    limit: int = 20
    offset: int = 0
    # Reserved for source-specific `attributes` filtering; unused for now.
    attributes: dict[str, str] = field(default_factory=dict)
