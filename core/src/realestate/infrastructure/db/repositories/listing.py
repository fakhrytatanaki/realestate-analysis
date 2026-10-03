"""Tortoise-backed listing repository."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from tortoise.expressions import Q, RawSQL

from realestate.domain.enums import SortOrder
from realestate.domain.hashing import compute_content_hash
from realestate.domain.models import Listing, ListingDraft, Page, UpsertResult
from realestate.domain.ports.repositories import ListingRepository
from realestate.domain.query import ListingQuery
from realestate.infrastructure.db.geo import bounding_box, haversine_sql
from realestate.infrastructure.db.mappers import to_listing
from realestate.infrastructure.db.models import ListingModel, ListingObservationModel

#: Alias under which the computed distance is selected and ordered.
DISTANCE_ALIAS = "distance_km"


class TortoiseListingRepository(ListingRepository):
    """Listing persistence and search.

    All geo handling is confined here, so moving to PostGIS later means
    rewriting this one class and nothing above it.
    """

    async def upsert_many(
        self,
        drafts: Sequence[ListingDraft],
        *,
        source_key: str,
        raw_document_id: UUID | None = None,
    ) -> UpsertResult:
        if not drafts:
            return UpsertResult()

        now = datetime.now(UTC)
        # Collapse duplicates inside the batch; the latest observation wins.
        by_external_id: dict[str, ListingDraft] = {}
        for draft in drafts:
            current = by_external_id.get(draft.external_id)
            if current is None or (draft.observed_at or now) >= (current.observed_at or now):
                by_external_id[draft.external_id] = draft

        existing = {
            row.external_id: row
            for row in await ListingModel.filter(
                source_key=source_key, external_id__in=list(by_external_id)
            )
        }

        to_create: list[ListingModel] = []
        observations: list[ListingObservationModel] = []
        #: observed_at -> ids, so archive rows keep their own capture times.
        unchanged_groups: dict[datetime, list[UUID]] = defaultdict(list)
        earlier_sightings: list[tuple[UUID, datetime]] = []
        backfills: list[tuple[UUID, dict[str, Any]]] = []
        created = updated = unchanged = 0

        for external_id, draft in by_external_id.items():
            observed_at = draft.observed_at or now
            # Archive sources observe history, so every sighting is recorded;
            # live sources only record genuine changes, not each hourly re-read.
            historical = draft.observed_at is not None
            content_hash = compute_content_hash(draft)
            row = existing.get(external_id)

            if row is None:
                listing_id = uuid4()
                to_create.append(
                    ListingModel(
                        id=listing_id,
                        source_key=source_key,
                        external_id=external_id,
                        content_hash=content_hash,
                        raw_document_id=raw_document_id,
                        first_observed_at=observed_at,
                        last_observed_at=observed_at,
                        **_draft_columns(draft),
                    )
                )
                observations.append(
                    _observation(listing_id, draft, observed_at, content_hash, raw_document_id)
                )
                created += 1
                continue

            if row.last_observed_at is not None and observed_at < row.last_observed_at:
                # An older capture arriving after a newer one (replays, CDX
                # order): it is history, not the current state of the row --
                # though it may fill details the newer capture did not show.
                if row.first_observed_at is None or observed_at < row.first_observed_at:
                    earlier_sightings.append((row.id, observed_at))
                if historical:
                    gaps = _fill_gaps(row, _draft_columns(draft))
                    if gaps:
                        backfills.append((row.id, gaps))
                if historical:
                    observations.append(
                        _observation(row.id, draft, observed_at, content_hash, raw_document_id)
                    )
                unchanged += 1
                continue

            if row.content_hash == content_hash:
                # Same ad as last time: record that we saw it, but leave
                # `updated_at` alone so it keeps meaning "last real change".
                unchanged_groups[observed_at].append(row.id)
                if historical:
                    observations.append(
                        _observation(row.id, draft, observed_at, content_hash, raw_document_id)
                    )
                unchanged += 1
                continue

            columns = _draft_columns(draft)
            if historical:
                # Archived captures are partial: a capture parsed by a template
                # without e.g. bedrooms must not erase what an earlier one found.
                for column in _CARRY_FORWARD:
                    if columns[column] is None:
                        columns[column] = getattr(row, column)
                if columns["location_name"] == _UNKNOWN_LOCATION:
                    columns["location_name"] = row.location_name
            for column, value in columns.items():
                setattr(row, column, value)
            row.content_hash = content_hash
            row.last_seen_at = now
            row.last_observed_at = observed_at
            if row.first_observed_at is None:
                row.first_observed_at = observed_at
            if raw_document_id is not None:
                row.raw_document_id = raw_document_id
            await row.save()
            observations.append(
                _observation(row.id, draft, observed_at, content_hash, raw_document_id)
            )
            updated += 1

        if to_create:
            await ListingModel.bulk_create(to_create)
        for observed_at, ids in unchanged_groups.items():
            # Queryset update deliberately: it does not fire `auto_now`.
            await ListingModel.filter(id__in=ids).update(
                last_seen_at=now, last_observed_at=observed_at
            )
        for listing_id, observed_at in earlier_sightings:
            await ListingModel.filter(id=listing_id).update(first_observed_at=observed_at)
        for listing_id, gaps in backfills:
            await ListingModel.filter(id=listing_id).update(**gaps)
        if observations:
            # Re-parsing a capture refreshes its observation (better rules,
            # same moment) rather than adding a second one.
            await ListingObservationModel.bulk_create(
                observations,
                on_conflict=["listing_id", "observed_at"],
                update_fields=_OBSERVATION_STATE,
            )

        return UpsertResult(created=created, updated=updated, unchanged=unchanged)

    async def search(self, query: ListingQuery) -> Page[Listing]:
        queryset = ListingModel.filter(self._build_filters(query))

        distance_expression: str | None = None
        if query.geo is not None:
            distance_expression = haversine_sql(
                float(query.geo.latitude), float(query.geo.longitude)
            )
            # Annotating and then filtering puts the haversine term in WHERE
            # (verified against Tortoise 1.1), so counts and paging stay exact.
            queryset = queryset.annotate(**{DISTANCE_ALIAS: RawSQL(distance_expression)}).filter(
                **{f"{DISTANCE_ALIAS}__lte": query.geo.radius_km}
            )

        total = await queryset.count()
        rows = await queryset.order_by(*self._order_by(query)).offset(query.offset).limit(
            query.limit
        )

        items = [
            to_listing(row, distance_km=getattr(row, DISTANCE_ALIAS, None)) for row in rows
        ]
        return Page(items=items, total=total, limit=query.limit, offset=query.offset)

    async def get(self, listing_id: UUID) -> Listing | None:
        row = await ListingModel.get_or_none(id=listing_id)
        return to_listing(row) if row else None

    async def mark_stale(self, source_key: str, *, not_seen_since: datetime) -> int:
        return await ListingModel.filter(
            source_key=source_key, is_active=True, last_seen_at__lt=not_seen_since
        ).update(is_active=False)

    async def count(self, source_key: str | None = None) -> int:
        queryset = ListingModel.all()
        if source_key is not None:
            queryset = queryset.filter(source_key=source_key)
        return await queryset.count()

    @staticmethod
    def _build_filters(query: ListingQuery) -> Q:
        """Translate the domain query into a Tortoise ``Q`` tree."""
        conditions: list[Q] = []

        if query.text:
            conditions.append(
                Q(title__icontains=query.text) | Q(description__icontains=query.text)
            )
        if query.source_keys:
            conditions.append(Q(source_key__in=list(query.source_keys)))
        if query.listing_type is not None:
            conditions.append(Q(listing_type=query.listing_type))
        if query.property_types:
            conditions.append(Q(property_type__in=list(query.property_types)))
        if query.price_min is not None:
            conditions.append(Q(price__gte=query.price_min))
        if query.price_max is not None:
            conditions.append(Q(price__lte=query.price_max))
        if query.currency:
            conditions.append(Q(currency=query.currency.upper()))
        if query.price_types:
            conditions.append(Q(price_type__in=list(query.price_types)))
        if query.country_code:
            conditions.append(Q(country_code=query.country_code.upper()))
        if query.city:
            conditions.append(Q(city__icontains=query.city))
        if query.district:
            conditions.append(Q(district__icontains=query.district))
        if query.location:
            conditions.append(Q(location_name__icontains=query.location))
        if query.bedrooms_min is not None:
            conditions.append(Q(bedrooms__gte=query.bedrooms_min))
        if query.bedrooms_max is not None:
            conditions.append(Q(bedrooms__lte=query.bedrooms_max))
        if query.bathrooms_min is not None:
            conditions.append(Q(bathrooms__gte=query.bathrooms_min))
        if query.area_min is not None:
            conditions.append(Q(area_sqm__gte=query.area_min))
        if query.area_max is not None:
            conditions.append(Q(area_sqm__lte=query.area_max))
        if query.is_active is not None:
            conditions.append(Q(is_active=query.is_active))
        if query.listed_after is not None:
            conditions.append(Q(listed_at__gte=query.listed_after))
        if query.listed_before is not None:
            conditions.append(Q(listed_at__lte=query.listed_before))

        if query.geo is not None:
            # Indexed bounding box first; the exact haversine cut is applied by
            # the caller. The box is a superset of the circle, never the reverse.
            box = bounding_box(query.geo.latitude, query.geo.longitude, query.geo.radius_km)
            conditions.append(
                Q(latitude__gte=box.min_latitude) & Q(latitude__lte=box.max_latitude)
            )
            if not box.spans_all_longitudes:
                conditions.append(
                    Q(longitude__gte=box.min_longitude) & Q(longitude__lte=box.max_longitude)
                )

        return Q(*conditions) if conditions else Q()

    @staticmethod
    def _order_by(query: ListingQuery) -> tuple[str, ...]:
        """Ordering columns, always ending in ``id`` so paging is deterministic."""
        match query.sort:
            case SortOrder.PRICE_ASC:
                return ("price", "id")
            case SortOrder.PRICE_DESC:
                return ("-price", "id")
            case SortOrder.LISTED_AT_ASC:
                return ("listed_at", "id")
            case SortOrder.DISTANCE:
                # Only reachable when a geo filter supplied the annotation.
                return (DISTANCE_ALIAS, "id") if query.geo else ("-listed_at", "id")
            case _:
                return ("-listed_at", "id")


#: Details an archived capture may simply not show (another page design, a
#: list row instead of the detail page); absent in a newer capture, the
#: stored value stands.
_CARRY_FORWARD = (
    "url",
    "description",
    "area_sqm",
    "bedrooms",
    "bathrooms",
    "city",
    "district",
    "latitude",
    "longitude",
    "listed_at",
)
_UNKNOWN_LOCATION = "unknown"
_OBSERVATION_STATE = ("price", "currency", "price_type", "content_hash", "raw_document_id")


def _fill_gaps(row: ListingModel, columns: dict[str, Any]) -> dict[str, Any]:
    """Carry-forward columns the row lacks and an (older) capture has."""
    gaps = {
        column: columns[column]
        for column in _CARRY_FORWARD
        if getattr(row, column) is None and columns[column] is not None
    }
    if row.location_name == _UNKNOWN_LOCATION and columns["location_name"] != _UNKNOWN_LOCATION:
        gaps["location_name"] = columns["location_name"]
    return gaps


def _draft_columns(draft: ListingDraft) -> dict[str, Any]:
    """Flatten a draft onto the listing table's columns."""
    point = draft.location.point
    return {
        "url": draft.url,
        "title": draft.title,
        "description": draft.description,
        "listing_type": draft.listing_type,
        "property_type": draft.property_type,
        "price": draft.price.amount,
        "currency": draft.price.currency.upper(),
        "price_type": draft.price.price_type,
        "installment_plan": draft.price.installment_plan,
        "area_sqm": draft.area_sqm,
        "bedrooms": draft.bedrooms,
        "bathrooms": draft.bathrooms,
        "location_name": draft.location.name,
        "country_code": (
            draft.location.country_code.upper() if draft.location.country_code else None
        ),
        "city": draft.location.city,
        "district": draft.location.district,
        "latitude": _quantize(point.latitude) if point else None,
        "longitude": _quantize(point.longitude) if point else None,
        "attributes": draft.attributes,
        "listed_at": draft.listed_at,
        "is_active": draft.is_active,
    }


def _observation(
    listing_id: UUID,
    draft: ListingDraft,
    observed_at: datetime,
    content_hash: str,
    raw_document_id: UUID | None,
) -> ListingObservationModel:
    return ListingObservationModel(
        id=uuid4(),
        listing_id=listing_id,
        observed_at=observed_at,
        price=draft.price.amount,
        currency=draft.price.currency.upper(),
        price_type=draft.price.price_type,
        content_hash=content_hash,
        raw_document_id=raw_document_id,
    )


def _quantize(value: Decimal) -> Decimal:
    """Match the column's six decimal places, so a round-trip is stable."""
    return value.quantize(Decimal("0.000001"))
