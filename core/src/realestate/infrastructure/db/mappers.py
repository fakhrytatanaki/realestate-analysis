"""Translation between ORM rows and domain objects.

Kept in one place so the repositories stay about querying, and so a schema
change has exactly one place to update.
"""

from __future__ import annotations

from decimal import Decimal

from realestate.domain.models import (
    GeoPoint,
    Listing,
    Location,
    Price,
    RawDocument,
    ScrapeRun,
)
from realestate.infrastructure.db.models import ListingModel, RawDocumentModel, ScrapeRunModel


def to_location(row: ListingModel) -> Location:
    point = (
        GeoPoint(latitude=Decimal(row.latitude), longitude=Decimal(row.longitude))
        if row.latitude is not None and row.longitude is not None
        else None
    )
    return Location(
        name=row.location_name,
        country_code=row.country_code,
        city=row.city,
        district=row.district,
        point=point,
    )


def to_price(row: ListingModel) -> Price:
    return Price(
        amount=row.price,
        currency=row.currency,
        price_type=row.price_type,
        installment_plan=row.installment_plan,
    )


def to_listing(row: ListingModel, *, distance_km: float | None = None) -> Listing:
    return Listing(
        id=row.id,
        source_key=row.source_key,
        external_id=row.external_id,
        title=row.title,
        listing_type=row.listing_type,
        property_type=row.property_type,
        location=to_location(row),
        price=to_price(row),
        url=row.url,
        description=row.description,
        area_sqm=row.area_sqm,
        bedrooms=row.bedrooms,
        bathrooms=row.bathrooms,
        listed_at=row.listed_at,
        is_active=row.is_active,
        attributes=row.attributes or {},
        content_hash=row.content_hash,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        # Tortoise synthesises the `<fk>_id` column attribute at runtime.
        raw_document_id=row.raw_document_id,  # type: ignore[attr-defined]
        # Only radius searches populate this; it is computed, not stored.
        distance_km=distance_km if distance_km is None else float(distance_km),
        first_observed_at=row.first_observed_at,
        last_observed_at=row.last_observed_at,
    )


def to_raw_document(row: RawDocumentModel) -> RawDocument:
    return RawDocument(
        id=row.id,
        source_key=row.source_key,
        kind=row.kind,
        status=row.status,
        blob_key=row.blob_key,
        blob_uri=row.blob_uri,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        fetched_at=row.fetched_at,
        external_id=row.external_id,
        source_url=row.source_url,
        meta=row.meta or {},
        parsed_at=row.parsed_at,
        parse_error=row.parse_error,
        attempts=row.attempts,
        scrape_run_id=row.scrape_run_id,  # type: ignore[attr-defined]
        graph_version=row.graph_version,
        parse_report=row.parse_report,
    )


def to_scrape_run(row: ScrapeRunModel) -> ScrapeRun:
    return ScrapeRun(
        id=row.id,
        source_key=row.source_key,
        trigger=row.trigger,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        documents_fetched=row.documents_fetched,
        listings_created=row.listings_created,
        listings_updated=row.listings_updated,
        errors=row.errors,
        error_message=row.error_message,
        stats=row.stats or {},
    )
