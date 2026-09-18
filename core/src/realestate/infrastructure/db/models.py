"""Tortoise ORM models.

These are persistence details, not the domain: repositories map them onto the
dataclasses in ``realestate.domain.models`` so nothing above this layer depends
on the ORM.
"""

from __future__ import annotations

from typing import Any

from tortoise import fields
from tortoise.models import Model

from realestate.domain.enums import (
    ListingType,
    PriceType,
    PropertyType,
    RawDocumentKind,
    RawDocumentStatus,
    RunStatus,
    RunTrigger,
)


class ScrapeRunModel(Model):
    """One execution of the ingestion pipeline for a single source."""

    id = fields.UUIDField(primary_key=True)
    source_key = fields.CharField(max_length=64, db_index=True)
    trigger = fields.CharEnumField(RunTrigger, max_length=16)
    status = fields.CharEnumField(RunStatus, max_length=16, db_index=True)

    started_at = fields.DatetimeField(auto_now_add=True)
    finished_at = fields.DatetimeField(null=True)

    documents_fetched = fields.IntField(default=0)
    listings_created = fields.IntField(default=0)
    listings_updated = fields.IntField(default=0)
    errors = fields.IntField(default=0)
    error_message = fields.TextField(null=True)

    class Meta:
        table = "scrape_run"
        ordering = ["-started_at"]


class RawDocumentModel(Model):
    """Metadata for one archived payload; the bytes live in the blob store."""

    id = fields.UUIDField(primary_key=True)
    source_key = fields.CharField(max_length=64, db_index=True)
    external_id = fields.CharField(max_length=255, null=True)
    kind = fields.CharEnumField(RawDocumentKind, max_length=16)
    status = fields.CharEnumField(
        RawDocumentStatus, max_length=16, default=RawDocumentStatus.PENDING, db_index=True
    )

    blob_key = fields.CharField(max_length=512)
    blob_uri = fields.CharField(max_length=1024)
    content_type = fields.CharField(max_length=128)
    size_bytes = fields.IntField()
    #: Indexed so an identical re-fetch can be recognised without re-parsing.
    sha256 = fields.CharField(max_length=64, db_index=True)

    source_url = fields.CharField(max_length=2048, null=True)
    meta: fields.JSONField[dict[str, Any]] = fields.JSONField(default=dict)

    fetched_at = fields.DatetimeField(auto_now_add=True)
    parsed_at = fields.DatetimeField(null=True)
    parse_error = fields.TextField(null=True)
    attempts = fields.IntField(default=0)

    scrape_run: fields.ForeignKeyNullableRelation[ScrapeRunModel] = fields.ForeignKeyField(
        "models.ScrapeRunModel",
        related_name="documents",
        null=True,
        on_delete=fields.SET_NULL,
    )

    class Meta:
        table = "raw_document"
        ordering = ["fetched_at"]
        indexes = (("source_key", "status"),)


class ListingModel(Model):
    """An aggregated classified ad.

    Location is denormalised onto the row: every source hands us a free-text
    label and sometimes a point, and flattening it keeps the hot search path to
    a single table.
    """

    id = fields.UUIDField(primary_key=True)

    #: ``(source_key, external_id)`` is the idempotency key for upserts.
    source_key = fields.CharField(max_length=64, db_index=True)
    external_id = fields.CharField(max_length=255)

    url = fields.CharField(max_length=2048, null=True)
    title = fields.CharField(max_length=512)
    description = fields.TextField(null=True)

    listing_type = fields.CharEnumField(ListingType, max_length=16, db_index=True)
    property_type = fields.CharEnumField(PropertyType, max_length=32, db_index=True)

    price = fields.DecimalField(max_digits=14, decimal_places=2, null=True, db_index=True)
    currency = fields.CharField(max_length=3)
    price_type = fields.CharEnumField(PriceType, max_length=16, default=PriceType.TOTAL)
    installment_plan: fields.JSONField[dict[str, Any]] = fields.JSONField(null=True)

    area_sqm = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    bedrooms = fields.SmallIntField(null=True)
    bathrooms = fields.SmallIntField(null=True)

    location_name = fields.CharField(max_length=512)
    country_code = fields.CharField(max_length=2, null=True, db_index=True)
    city = fields.CharField(max_length=128, null=True, db_index=True)
    district = fields.CharField(max_length=128, null=True)
    latitude = fields.DecimalField(max_digits=9, decimal_places=6, null=True)
    longitude = fields.DecimalField(max_digits=9, decimal_places=6, null=True)

    #: Source-specific extras that do not warrant a column.
    attributes: fields.JSONField[dict[str, Any]] = fields.JSONField(default=dict)
    #: sha256 over the business fields; see ``realestate.domain.hashing``.
    content_hash = fields.CharField(max_length=64)

    is_active = fields.BooleanField(default=True, db_index=True)
    listed_at = fields.DatetimeField(null=True, db_index=True)
    first_seen_at = fields.DatetimeField(auto_now_add=True)
    last_seen_at = fields.DatetimeField(auto_now_add=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    raw_document: fields.ForeignKeyNullableRelation[RawDocumentModel] = fields.ForeignKeyField(
        "models.RawDocumentModel",
        related_name="listings",
        null=True,
        on_delete=fields.SET_NULL,
    )

    class Meta:
        table = "listing"
        unique_together = (("source_key", "external_id"),)
        indexes = (
            ("latitude", "longitude"),
            ("listing_type", "property_type"),
            ("country_code", "city"),
        )
        ordering = ["-listed_at"]
