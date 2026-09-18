"""Listing search parameters.

Declared once as a pydantic model so validation, OpenAPI documentation and the
conversion to the domain query all come from a single definition.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from realestate.domain.enums import ListingType, PriceType, PropertyType, SortOrder
from realestate.domain.exceptions import InvalidQueryError
from realestate.domain.query import MAX_LIMIT, GeoFilter, ListingQuery


class ListingQueryParams(BaseModel):
    """Every filter ``GET /listings`` accepts."""

    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, description="Free text over title and description")
    source_key: list[str] = Field(default_factory=list, description="Restrict to these sources")
    listing_type: ListingType | None = Field(default=None, description="RENT or SALE")
    property_type: list[PropertyType] = Field(default_factory=list)

    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(
        default=None, min_length=3, max_length=3, description="ISO-4217 code, e.g. EGP"
    )
    price_type: list[PriceType] = Field(default_factory=list)

    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    city: str | None = None
    district: str | None = None
    location: str | None = Field(default=None, description="Substring of the location label")

    bedrooms_min: int | None = Field(default=None, ge=0)
    bedrooms_max: int | None = Field(default=None, ge=0)
    bathrooms_min: int | None = Field(default=None, ge=0)
    area_min: Decimal | None = Field(default=None, ge=0)
    area_max: Decimal | None = Field(default=None, ge=0)

    lat: Decimal | None = Field(default=None, ge=-90, le=90)
    lon: Decimal | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=500)

    is_active: bool | None = Field(default=True, description="Pass null to include inactive ads")
    listed_after: datetime | None = None
    listed_before: datetime | None = None

    sort: SortOrder = SortOrder.LISTED_AT_DESC
    limit: int = Field(default=20, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_coherent(self) -> ListingQueryParams:
        geo_parts = (self.lat, self.lon, self.radius_km)
        if any(part is not None for part in geo_parts) and not all(
            part is not None for part in geo_parts
        ):
            raise ValueError("lat, lon and radius_km must be supplied together")
        if self.sort is SortOrder.DISTANCE and self.lat is None:
            raise ValueError("sort=distance requires lat, lon and radius_km")
        if self.price_min is not None and self.price_max is not None:
            if self.price_min > self.price_max:
                raise ValueError("price_min must not exceed price_max")
        if self.area_min is not None and self.area_max is not None:
            if self.area_min > self.area_max:
                raise ValueError("area_min must not exceed area_max")
        if self.bedrooms_min is not None and self.bedrooms_max is not None:
            if self.bedrooms_min > self.bedrooms_max:
                raise ValueError("bedrooms_min must not exceed bedrooms_max")
        if self.listed_after is not None and self.listed_before is not None:
            if self.listed_after > self.listed_before:
                raise ValueError("listed_after must not be later than listed_before")
        return self

    def to_domain(self) -> ListingQuery:
        """Convert to the framework-free query the repositories understand."""
        geo: GeoFilter | None = None
        if self.lat is not None and self.lon is not None and self.radius_km is not None:
            geo = GeoFilter(latitude=self.lat, longitude=self.lon, radius_km=self.radius_km)
        elif self.sort is SortOrder.DISTANCE:
            # Unreachable through the validator, but a direct caller could get here.
            raise InvalidQueryError("sort=distance requires lat, lon and radius_km")

        return ListingQuery(
            text=self.q,
            source_keys=tuple(self.source_key),
            listing_type=self.listing_type,
            property_types=tuple(self.property_type),
            price_min=self.price_min,
            price_max=self.price_max,
            currency=self.currency,
            price_types=tuple(self.price_type),
            country_code=self.country_code,
            city=self.city,
            district=self.district,
            location=self.location,
            bedrooms_min=self.bedrooms_min,
            bedrooms_max=self.bedrooms_max,
            bathrooms_min=self.bathrooms_min,
            area_min=self.area_min,
            area_max=self.area_max,
            geo=geo,
            is_active=self.is_active,
            listed_after=self.listed_after,
            listed_before=self.listed_before,
            sort=self.sort,
            limit=min(self.limit, MAX_LIMIT),
            offset=self.offset,
        )
