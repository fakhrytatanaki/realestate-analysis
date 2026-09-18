"""Fixture data source -- a working reference implementation.

It reads JSON files from ``var/fixtures/`` shaped like a typical portal's search
API response, so the whole pipeline (blob archive, raw document, parse, upsert,
query) is exercisable with no network. It is also the template a real portal's
adapter should follow.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, ClassVar

import aiofiles

from realestate.config.paths import resolve
from realestate.domain.enums import ListingType, PriceType, PropertyType, RawDocumentKind
from realestate.domain.exceptions import ParseError
from realestate.domain.models import (
    FetchContext,
    GeoPoint,
    ListingDraft,
    Location,
    Price,
    RawPayload,
)
from realestate.domain.ports.data_source import DataSource
from realestate.domain.ports.log_provider import LogProvider

#: Portal vocabulary -> domain vocabulary. A real source needs the same two maps.
_PURPOSE_MAP: dict[str, ListingType] = {
    "rent": ListingType.RENT,
    "for-rent": ListingType.RENT,
    "sale": ListingType.SALE,
    "for-sale": ListingType.SALE,
}

_CATEGORY_MAP: dict[str, PropertyType] = {
    "apartment": PropertyType.APARTMENT,
    "flat": PropertyType.APARTMENT,
    "villa": PropertyType.VILLA,
    "townhouse": PropertyType.TOWNHOUSE,
    "house": PropertyType.HOUSE,
    "studio": PropertyType.STUDIO,
    "land": PropertyType.LAND,
    "office": PropertyType.OFFICE,
    "shop": PropertyType.SHOP,
    "retail": PropertyType.SHOP,
    "warehouse": PropertyType.WAREHOUSE,
}

_PERIOD_MAP: dict[str, PriceType] = {
    "total": PriceType.TOTAL,
    "monthly": PriceType.PER_MONTH,
    "weekly": PriceType.PER_WEEK,
    "nightly": PriceType.PER_NIGHT,
    "yearly": PriceType.PER_YEAR,
    "per-sqm": PriceType.PER_SQM,
    "installment": PriceType.INSTALLMENT,
}


class FixtureDataSource(DataSource):
    """Serves listings from local JSON files."""

    key: ClassVar[str] = "fixture"
    display_name: ClassVar[str] = "Local Fixtures"
    country_code: ClassVar[str] = "ZZ"

    def __init__(
        self,
        *,
        log: LogProvider,
        directory: str | Path = "var/fixtures",
    ) -> None:
        self._log = log.bind(source_key=self.key)
        self._directory = resolve(directory)

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
        """Yield one payload per fixture file, exactly as stored on disk."""
        files = sorted(self._directory.glob("*.json"))
        if not files:
            await self._log.warning("no fixture files found", directory=str(self._directory))

        emitted = 0
        for path in files:
            if ctx.max_items is not None and emitted >= ctx.max_items:
                break
            async with aiofiles.open(path, "rb") as handle:
                content = await handle.read()
            emitted += 1
            await self._log.debug("fetched fixture", file=path.name, bytes=len(content))
            yield RawPayload(
                content=content,
                kind=RawDocumentKind.JSON,
                content_type="application/json",
                source_url=path.as_uri(),
                external_id=path.stem,
                meta={"file": path.name, "page": emitted},
            )

    async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]:
        """Normalise one fixture file into drafts."""
        try:
            document = json.loads(payload.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ParseError(f"{self.key}: payload is not valid JSON: {exc}") from exc

        results = document.get("results")
        if not isinstance(results, list):
            raise ParseError(
                f"{self.key}: expected a 'results' array, "
                f"got {type(results).__name__}"
            )

        drafts: list[ListingDraft] = []
        for index, item in enumerate(results):
            try:
                drafts.append(self._to_draft(item))
            except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                # One malformed ad must not lose the rest of the page.
                await self._log.warning(
                    "skipping unparseable item",
                    file=payload.meta.get("file"),
                    index=index,
                    error=str(exc),
                )
        return drafts

    def _to_draft(self, item: dict[str, Any]) -> ListingDraft:
        raw_location = item.get("location") or {}
        price = item.get("price") or {}

        point: GeoPoint | None = None
        latitude, longitude = raw_location.get("lat"), raw_location.get("lng")
        if latitude is not None and longitude is not None:
            point = GeoPoint(latitude=Decimal(str(latitude)), longitude=Decimal(str(longitude)))

        return ListingDraft(
            external_id=str(item["id"]),
            title=str(item["title"]),
            listing_type=_PURPOSE_MAP.get(
                str(item.get("purpose", "")).lower(), ListingType.SALE
            ),
            property_type=_CATEGORY_MAP.get(
                str(item.get("category", "")).lower(), PropertyType.OTHER
            ),
            location=Location(
                name=str(raw_location.get("name") or "Unknown"),
                country_code=_optional_str(raw_location.get("country")),
                city=_optional_str(raw_location.get("city")),
                district=_optional_str(raw_location.get("district")),
                point=point,
            ),
            price=Price(
                amount=_optional_decimal(price.get("value")),
                currency=str(price.get("currency") or "USD"),
                price_type=_PERIOD_MAP.get(
                    str(price.get("period", "total")).lower(), PriceType.TOTAL
                ),
                installment_plan=price.get("installment_plan"),
            ),
            url=_optional_str(item.get("url")),
            description=_optional_str(item.get("description")),
            area_sqm=_optional_decimal(item.get("area")),
            bedrooms=_optional_int(item.get("beds")),
            bathrooms=_optional_int(item.get("baths")),
            listed_at=_optional_datetime(item.get("created_at")),
            is_active=bool(item.get("active", True)),
            attributes={
                key: value
                for key, value in item.items()
                if key
                not in {
                    "id", "title", "purpose", "category", "location", "price",
                    "url", "description", "area", "beds", "baths", "created_at", "active",
                }
            },
        )

    async def healthcheck(self) -> bool:
        return self._directory.is_dir()


def _optional_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _optional_decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    # ``fromisoformat`` handles the trailing 'Z' from 3.11 onwards.
    return datetime.fromisoformat(str(value))
