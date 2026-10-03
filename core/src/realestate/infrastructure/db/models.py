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
    CrawlStatus,
    GapStatus,
    ListingType,
    NodeKind,
    PageKind,
    PriceType,
    PropertyType,
    RawDocumentKind,
    RawDocumentStatus,
    RuleDomain,
    RuleGraphStatus,
    RuleOrigin,
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
    #: When the advert was observed (capture time for archive sources). The
    #: row's business fields always reflect the *latest* observation, whatever
    #: order observations are ingested in.
    first_observed_at = fields.DatetimeField(null=True)
    last_observed_at = fields.DatetimeField(null=True, db_index=True)
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


class ListingObservationModel(Model):
    """One sighting of a listing in one state: the price history."""

    id = fields.UUIDField(primary_key=True)
    listing: fields.ForeignKeyRelation[ListingModel] = fields.ForeignKeyField(
        "models.ListingModel", related_name="observations", on_delete=fields.CASCADE
    )
    observed_at = fields.DatetimeField(db_index=True)
    price = fields.DecimalField(max_digits=14, decimal_places=2, null=True)
    currency = fields.CharField(max_length=3)
    price_type = fields.CharEnumField(PriceType, max_length=16)
    content_hash = fields.CharField(max_length=64)
    raw_document: fields.ForeignKeyNullableRelation[RawDocumentModel] = fields.ForeignKeyField(
        "models.RawDocumentModel",
        related_name="observations",
        null=True,
        on_delete=fields.SET_NULL,
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "listing_observation"
        #: One state per listing per moment: re-parsing the same capture with
        #: better rules replaces its observation instead of adding another.
        unique_together = (("listing", "observed_at"),)


class CrawlFrontierModel(Model):
    """One archived capture and what the crawler decided about it."""

    id = fields.BigIntField(primary_key=True)
    source_key = fields.CharField(max_length=64)
    #: CDX-style canonical key; long (encoded Arabic slugs), so indexed via hash.
    url_key = fields.TextField()
    url_key_hash = fields.CharField(max_length=40)
    timestamp = fields.CharField(max_length=14)
    original_url = fields.TextField()
    digest = fields.CharField(max_length=64)
    status = fields.CharEnumField(CrawlStatus, max_length=16, default=CrawlStatus.DISCOVERED)
    priority = fields.SmallIntField(default=0)
    page_kind = fields.CharEnumField(PageKind, max_length=16, null=True)
    route_node = fields.CharField(max_length=128, null=True)
    evidence: fields.JSONField[dict[str, Any]] = fields.JSONField(default=dict)
    attempts = fields.SmallIntField(default=0)
    error = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "crawl_frontier"
        unique_together = (("source_key", "url_key_hash", "timestamp"),)
        indexes = (("source_key", "status", "priority"), ("source_key", "url_key_hash"))


class CrawlCursorModel(Model):
    """Resume point of one enumeration scope, e.g. ``cdx:2013``."""

    id = fields.IntField(primary_key=True)
    source_key = fields.CharField(max_length=64)
    scope = fields.CharField(max_length=64)
    resume_key = fields.TextField(null=True)
    done = fields.BooleanField(default=False)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "crawl_cursor"
        unique_together = (("source_key", "scope"),)


class RuleGraphModel(Model):
    """One immutable version of a rule graph."""

    id = fields.UUIDField(primary_key=True)
    source_key = fields.CharField(max_length=64)
    domain = fields.CharEnumField(RuleDomain, max_length=16)
    version = fields.IntField()
    status = fields.CharEnumField(RuleGraphStatus, max_length=16, db_index=True)
    parent_id = fields.UUIDField(null=True)
    vocab: fields.JSONField[dict[str, Any]] = fields.JSONField(default=dict)
    notes = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "rule_graph"
        unique_together = (("source_key", "domain", "version"),)


class RuleNodeModel(Model):
    """A state of a rule graph version."""

    id = fields.UUIDField(primary_key=True)
    graph: fields.ForeignKeyRelation[RuleGraphModel] = fields.ForeignKeyField(
        "models.RuleGraphModel", related_name="nodes", on_delete=fields.CASCADE
    )
    key = fields.CharField(max_length=128)
    kind = fields.CharEnumField(NodeKind, max_length=16)
    action: fields.JSONField[dict[str, Any]] = fields.JSONField(default=dict)
    origin = fields.CharEnumField(RuleOrigin, max_length=16, default=RuleOrigin.SEED)
    llm_decision_id = fields.UUIDField(null=True)
    position = fields.IntField(default=0)

    class Meta:
        table = "rule_node"
        unique_together = (("graph", "key"),)


class RuleEdgeModel(Model):
    """A guarded transition of a rule graph version."""

    id = fields.UUIDField(primary_key=True)
    graph: fields.ForeignKeyRelation[RuleGraphModel] = fields.ForeignKeyField(
        "models.RuleGraphModel", related_name="edges", on_delete=fields.CASCADE
    )
    from_key = fields.CharField(max_length=128)
    to_key = fields.CharField(max_length=128)
    condition: fields.JSONField[dict[str, Any]] = fields.JSONField(default=dict)
    priority = fields.IntField(default=100)
    position = fields.IntField(default=0)

    class Meta:
        table = "rule_edge"


class RuleGapModel(Model):
    """A cluster of inputs no rule handled."""

    id = fields.UUIDField(primary_key=True)
    source_key = fields.CharField(max_length=64)
    domain = fields.CharEnumField(RuleDomain, max_length=16)
    fingerprint = fields.CharField(max_length=512)
    status = fields.CharEnumField(GapStatus, max_length=16, default=GapStatus.OPEN, db_index=True)
    occurrences = fields.IntField(default=0)
    samples: fields.JSONField[list[str]] = fields.JSONField(default=list)
    attempts = fields.IntField(default=0)
    last_error = fields.TextField(null=True)
    resolved_version = fields.IntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "rule_gap"
        unique_together = (("source_key", "domain", "fingerprint"),)


class LlmDecisionModel(Model):
    """One model answer: cache, audit trail and cost ledger."""

    id = fields.UUIDField(primary_key=True)
    task = fields.CharField(max_length=64)
    input_fp = fields.CharField(max_length=64)
    model = fields.CharField(max_length=128)
    prompt_version = fields.CharField(max_length=32)
    request: fields.JSONField[dict[str, Any]] = fields.JSONField(default=dict)
    response: fields.JSONField[dict[str, Any]] = fields.JSONField(null=True)
    raw_text = fields.TextField(default="")
    valid = fields.BooleanField(default=False)
    error = fields.TextField(null=True)
    tokens_in = fields.IntField(default=0)
    tokens_out = fields.IntField(default=0)
    latency_ms = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "llm_decision"
        indexes = (("task", "input_fp", "model", "prompt_version"),)


class UserModel(Model):
    """An account. ``user`` is reserved in PostgreSQL, hence ``app_user``."""

    id = fields.UUIDField(primary_key=True)
    #: Stored normalised (stripped, lower-cased), so a plain unique index suffices.
    email = fields.CharField(max_length=254, unique=True)
    display_name = fields.CharField(max_length=128)
    password_hash = fields.CharField(max_length=255)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "app_user"


class SessionModel(Model):
    """A signed-in session, keyed by the sha256 of its bearer token."""

    id = fields.UUIDField(primary_key=True)
    user: fields.ForeignKeyRelation[UserModel] = fields.ForeignKeyField(
        "models.UserModel", related_name="sessions", on_delete=fields.CASCADE
    )
    token_hash = fields.CharField(max_length=64, unique=True)
    expires_at = fields.DatetimeField(db_index=True)
    last_used_at = fields.DatetimeField()
    user_agent = fields.CharField(max_length=512, null=True)
    created_at = fields.DatetimeField()

    class Meta:
        table = "user_session"
