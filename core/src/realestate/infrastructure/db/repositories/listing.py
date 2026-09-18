"""Tortoise-backed listing repository."""

from __future__ import annotations

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
from realestate.infrastructure.db.models import ListingModel

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
        # Collapse duplicates inside the batch; the last draft wins.
        by_external_id = {draft.external_id: draft for draft in drafts}

        existing = {
            row.external_id: row
            for row in await ListingModel.filter(
                source_key=source_key, external_id__in=list(by_external_id)
            )
        }

        to_create: list[ListingModel] = []
        unchanged_ids: list[UUID] = []
        created = updated = 0

        for external_id, draft in by_external_id.items():
            content_hash = compute_content_hash(draft)
            row = existing.get(external_id)

            if row is None:
                to_create.append(
                    ListingModel(
                        id=uuid4(),
                        source_key=source_key,
                        external_id=external_id,
                        content_hash=content_hash,
                        raw_document_id=raw_document_id,
                        **_draft_columns(draft),
                    )
                )
                created += 1
                continue

            if row.content_hash == content_hash:
                # Same ad as last time: record that we saw it, but leave
                # `updated_at` alone so it keeps meaning "last real change".
                unchanged_ids.append(row.id)
                continue

            for column, value in _draft_columns(draft).items():
                setattr(row, column, value)
            row.content_hash = content_hash
            row.last_seen_at = now
            if raw_document_id is not None:
                row.raw_document_id = raw_document_id
            await row.save()
            updated += 1

        if to_create:
            await ListingModel.bulk_create(to_create)
        if unchanged_ids:
            # Queryset update deliberately: it does not fire `auto_now`.
            await ListingModel.filter(id__in=unchanged_ids).update(last_seen_at=now)

        return UpsertResult(created=created, updated=updated, unchanged=len(unchanged_ids))

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


def _quantize(value: Decimal) -> Decimal:
    """Match the column's six decimal places, so a round-trip is stable."""
    return value.quantize(Decimal("0.000001"))
