"""Listing response DTOs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from realestate.domain.enums import ListingType, PriceType, PropertyType
from realestate.domain.models import Listing


class LocationRead(BaseModel):
    """Where a listing is."""

    name: str
    country_code: str | None = None
    city: str | None = None
    district: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None


class PriceRead(BaseModel):
    """A money figure and how to read it."""

    amount: Decimal | None = Field(default=None, description="None for on-request prices")
    currency: str
    price_type: PriceType
    installment_plan: dict[str, Any] | None = None


class ListingRead(BaseModel):
    """One aggregated classified ad."""

    id: UUID
    source_key: str
    external_id: str
    title: str
    description: str | None = None
    url: str | None = None
    listing_type: ListingType
    property_type: PropertyType
    location: LocationRead
    price: PriceRead
    area_sqm: Decimal | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    is_active: bool
    listed_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)
    raw_document_id: UUID | None = Field(
        default=None, description="Archived payload this listing was parsed from"
    )
    distance_km: float | None = Field(
        default=None, description="Populated only by radius searches"
    )

    @classmethod
    def from_domain(cls, listing: Listing) -> ListingRead:
        point = listing.location.point
        return cls(
            id=listing.id,
            source_key=listing.source_key,
            external_id=listing.external_id,
            title=listing.title,
            description=listing.description,
            url=listing.url,
            listing_type=listing.listing_type,
            property_type=listing.property_type,
            location=LocationRead(
                name=listing.location.name,
                country_code=listing.location.country_code,
                city=listing.location.city,
                district=listing.location.district,
                latitude=point.latitude if point else None,
                longitude=point.longitude if point else None,
            ),
            price=PriceRead(
                amount=listing.price.amount,
                currency=listing.price.currency,
                price_type=listing.price.price_type,
                installment_plan=listing.price.installment_plan,
            ),
            area_sqm=listing.area_sqm,
            bedrooms=listing.bedrooms,
            bathrooms=listing.bathrooms,
            is_active=listing.is_active,
            listed_at=listing.listed_at,
            first_seen_at=listing.first_seen_at,
            last_seen_at=listing.last_seen_at,
            updated_at=listing.updated_at,
            attributes=listing.attributes,
            raw_document_id=listing.raw_document_id,
            distance_km=(
                round(listing.distance_km, 3)
                if listing.distance_km is not None
                else None
            ),
        )
