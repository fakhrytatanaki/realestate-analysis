"""Dubizzle Egypt data source.

Uses Dubizzle's internal Elasticsearch ``_msearch`` endpoint (observed at
``search.olx.com.eg``) to collect property listings.  ``fetch()`` only produces
raw JSON bytes; ``parse()`` turns those bytes into :class:`ListingDraft` objects.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

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
from realestate.domain.ports.log_provider import LogProvider
from realestate.infrastructure.sources.base import HttpDataSource

#: Public, hardcoded search endpoint used by the dubizzle.com.eg SPA.
_SEARCH_URL: str = "https://search.olx.com.eg/_msearch"

#: The ``category.lvl0.externalID`` for the Properties vertical.
_PROPERTY_CATEGORY_ID: str = "138"

#: Dubizzle's root location externalID (Egypt).
_DEFAULT_LOCATION_EXTERNAL_ID: str = "0-1"

#: Auth token observed in browser requests.  Exposed as a param default so it can
#: be rotated without a code change.
_DEFAULT_AUTH_TOKEN: str = (
    "Basic b2x4LWVnLXByb2R1Y3Rpb24tc2VhcmNoOn1nNDM2Q0R5QDJmWXs2alpHVGhGX0dEZjxJVSZKbnhL"
)

#: Default property categories to scrape (sale + rent for apartments & villas).
_DEFAULT_CATEGORIES: tuple[str, ...] = (
    "apartments-duplex-for-sale",
    "apartments-duplex-for-rent",
    "villas-for-sale",
    "villas-for-rent",
)

#: Default page size for Elasticsearch queries.  Keep moderate; the site returns
#: up to 60 on its own pages.
_DEFAULT_PAGE_SIZE: int = 50

#: Default cap on pages per scrape when ``ctx.page_limit`` is not supplied.
_DEFAULT_PAGE_LIMIT: int = 10

#: Map Dubizzle rental period codes to domain price types.
_RENTAL_PERIOD_MAP: dict[str, PriceType] = {
    "1": PriceType.PER_NIGHT,
    "2": PriceType.PER_WEEK,
    "3": PriceType.PER_MONTH,
    "4": PriceType.PER_YEAR,
}

#: Map apartment/duplex subtype codes to domain property types.
_APARTMENT_TYPE_MAP: dict[str, PropertyType] = {
    "1": PropertyType.APARTMENT,
    "2": PropertyType.APARTMENT,
    "3": PropertyType.APARTMENT,
    "4": PropertyType.STUDIO,
    "6": PropertyType.APARTMENT,
    "7": PropertyType.APARTMENT,
}

#: Map villa subtype codes to domain property types.
_VILLA_TYPE_MAP: dict[str, PropertyType] = {
    "1": PropertyType.VILLA,
    "2": PropertyType.VILLA,
    "3": PropertyType.TOWNHOUSE,
    "4": PropertyType.VILLA,
}

#: Map commercial subtype codes to domain property types.
_COMMERCIAL_TYPE_MAP: dict[str, PropertyType] = {
    "1": PropertyType.OTHER,
    "2": PropertyType.OTHER,
    "3": PropertyType.OTHER,
    "4": PropertyType.OTHER,
    "5": PropertyType.SHOP,
    "6": PropertyType.OFFICE,
    "7": PropertyType.OTHER,
    "8": PropertyType.WAREHOUSE,
    "9": PropertyType.OTHER,
    "10": PropertyType.OTHER,
    "11": PropertyType.OTHER,
    "12": PropertyType.OTHER,
}


class DubizzleEgDataSource(HttpDataSource):
    """Egyptian classifieds portal (dubizzle.com.eg)."""

    key: ClassVar[str] = "dubizzle_eg"
    display_name: ClassVar[str] = "Dubizzle Egypt"
    country_code: ClassVar[str] = "EG"

    #: Egyptian portals are rate-sensitive; stay conservative.
    max_concurrency: ClassVar[int] = 2
    min_delay_seconds: ClassVar[float] = 1.5

    def __init__(
        self, *, log: LogProvider, params: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(log=log, params=params)
        self._auth_token = self._params.get("auth_token", _DEFAULT_AUTH_TOKEN)
        self._categories = tuple(self._params.get("categories", _DEFAULT_CATEGORIES))
        self._page_size = int(self._params.get("page_size", _DEFAULT_PAGE_SIZE))
        self._location_external_id = str(
            self._params.get("location_external_id", _DEFAULT_LOCATION_EXTERNAL_ID)
        )

    def default_headers(self) -> dict[str, str]:
        """Headers matching the observed browser requests to the search API."""
        headers = super().default_headers()
        headers["Content-Type"] = "application/x-ndjson"
        headers["Authorization"] = self._auth_token
        headers["Referer"] = "https://www.dubizzle.com.eg/"
        headers["Origin"] = "https://www.dubizzle.com.eg"
        headers["Accept"] = "*/*"
        return headers

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawPayload]:
        """Yield one raw JSON payload per category page.

        ``fetch`` never parses the response; it only paginates through the
        Elasticsearch ``_msearch`` endpoint up to ``ctx.page_limit`` pages per
        scrape (default :data:`_DEFAULT_PAGE_LIMIT`).
        """
        page_limit = ctx.page_limit if ctx.page_limit is not None else _DEFAULT_PAGE_LIMIT
        emitted = 0
        max_items = ctx.max_items

        for category in self._categories:
            if max_items is not None and emitted >= max_items:
                break

            for page in range(page_limit):
                if max_items is not None and emitted >= max_items:
                    break

                offset = page * self._page_size
                body = self._build_search_body(category, offset, ctx.since)
                response = await self.request(
                    "POST",
                    _SEARCH_URL,
                    content=body.encode("utf-8"),
                )
                response.raise_for_status()

                yield RawPayload(
                    content=response.content,
                    kind=RawDocumentKind.JSON,
                    content_type="application/json",
                    source_url=f"{_SEARCH_URL}?category={category}&from={offset}",
                    external_id=f"{self.key}:{category}:page{page}",
                    meta={
                        "category": category,
                        "page": page,
                        "from": offset,
                        "size": self._page_size,
                    },
                )
                emitted += 1

    async def parse(self, payload: RawPayload) -> Sequence[ListingDraft]:
        """Decode an archived msearch response into normalised drafts."""
        try:
            document = json.loads(payload.content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ParseError(f"{self.key}: invalid JSON payload") from exc

        try:
            response = document["responses"][0]
            hits = response["hits"]["hits"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ParseError(f"{self.key}: unexpected msearch response shape") from exc

        category_slug = payload.meta.get("category", "")
        drafts: list[ListingDraft] = []
        for index, hit in enumerate(hits):
            try:
                drafts.append(self._to_draft(hit, category_slug))
            except (KeyError, TypeError, ValueError, InvalidOperation, ParseError) as exc:
                await self._log.warning(
                    "skipping unparseable item",
                    external_id=payload.external_id,
                    index=index,
                    error=str(exc),
                )
        return drafts

    async def healthcheck(self) -> bool:
        """Cheap check that the search endpoint responds."""
        try:
            body = self._build_search_body(
                category="apartments-duplex-for-sale",
                offset=0,
                since=None,
                size=0,
            )
            response = await self.request(
                "POST",
                _SEARCH_URL,
                content=body.encode("utf-8"),
            )
            return response.status_code == 200
        except Exception:
            return False

    def _build_search_body(
        self,
        category: str,
        offset: int,
        since: datetime | None,
        size: int | None = None,
    ) -> str:
        """Build an NDJSON msearch body for one query."""
        if size is None:
            size = self._page_size

        must: list[dict[str, Any]] = [
            {"term": {"category.slug": category}},
            {"term": {"category.lvl0.externalID": _PROPERTY_CATEGORY_ID}},
            {"term": {"location.externalID": self._location_external_id}},
        ]
        if since is not None:
            must.append(
                {"range": {"timestamp": {"gte": since.timestamp()}}}
            )

        query: dict[str, Any] = {
            "from": offset,
            "size": size,
            "track_total_hits": 200000,
            "query": {"bool": {"must": must}},
            "sort": [
                {"timestamp": {"order": "desc"}},
                {"id": {"order": "desc"}},
            ],
            "timeout": "5s",
        }

        header = json.dumps({"index": "olx-eg-production-ads-ar"})
        return f"{header}\n{json.dumps(query)}\n"

    def _to_draft(self, hit: dict[str, Any], category_slug: str) -> ListingDraft:
        """Map one Elasticsearch hit to a domain draft."""
        source = hit.get("_source") if isinstance(hit, dict) else None
        if not isinstance(source, dict):
            raise ParseError("hit missing _source")

        raw_external_id = source.get("externalID")
        if not isinstance(raw_external_id, str) or not raw_external_id:
            raise ParseError("hit missing externalID")
        external_id = raw_external_id
        title = self._first_text(source, "title_l1", "title")
        description = self._first_text(source, "description_l1", "description")
        listing_type = self._listing_type(category_slug, source.get("purpose"))
        property_type = self._property_type(category_slug, source)
        location = self._location(source)
        price = self._price(source, listing_type)
        url = self._url(source)
        area_sqm = _safe_decimal(source.get("extraFields", {}).get("ft"))
        bedrooms = _safe_int(source.get("extraFields", {}).get("rooms"))
        bathrooms = _safe_int(source.get("extraFields", {}).get("bathrooms"))
        listed_at = _parse_timestamp(source.get("createdAt"))
        is_active = source.get("state") == "active"
        attributes = self._attributes(source)

        return ListingDraft(
            external_id=external_id,
            title=title,
            listing_type=listing_type,
            property_type=property_type,
            location=location,
            price=price,
            url=url,
            description=description,
            area_sqm=area_sqm,
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            listed_at=listed_at,
            is_active=is_active,
            attributes=attributes,
        )

    def _first_text(self, source: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _listing_type(self, category_slug: str, purpose: Any) -> ListingType:
        if category_slug.endswith("-for-rent") or purpose == "for-rent":
            return ListingType.RENT
        return ListingType.SALE

    def _property_type(self, category_slug: str, source: dict[str, Any]) -> PropertyType:
        type_code = str(source.get("extraFields", {}).get("type", ""))

        if "apartments-duplex" in category_slug:
            return _APARTMENT_TYPE_MAP.get(type_code, PropertyType.APARTMENT)
        if "villas" in category_slug:
            return _VILLA_TYPE_MAP.get(type_code, PropertyType.VILLA)
        if "commercial" in category_slug:
            return _COMMERCIAL_TYPE_MAP.get(type_code, PropertyType.OTHER)
        if "buildings-lands" in category_slug:
            return PropertyType.LAND
        if "vacation-homes" in category_slug:
            return PropertyType.OTHER

        return PropertyType.OTHER

    def _price(self, source: dict[str, Any], listing_type: ListingType) -> Price:
        extra = source.get("extraFields", {})
        amount = _safe_decimal(extra.get("price"))
        currency = "EGP"

        if listing_type is ListingType.RENT:
            price_type = _RENTAL_PERIOD_MAP.get(
                str(extra.get("rental_period", "3")), PriceType.PER_MONTH
            )
            return Price(amount=amount, currency=currency, price_type=price_type)

        payment_option = str(extra.get("payment_option", "1"))
        if payment_option == "2":
            down_payment = extra.get("down_payment")
            installment_plan: dict[str, Any] | None = None
            if down_payment is not None:
                installment_plan = {"down_payment": down_payment}
            return Price(
                amount=amount,
                currency=currency,
                price_type=PriceType.INSTALLMENT,
                installment_plan=installment_plan,
            )

        return Price(amount=amount, currency=currency, price_type=PriceType.TOTAL)

    def _location(self, source: dict[str, Any]) -> Location:
        lvl1 = source.get("location.lvl1") or {}
        lvl2 = source.get("location.lvl2") or {}
        lvl3 = source.get("location.lvl3") or {}
        lvl4 = source.get("location.lvl4") or {}

        city = _location_name(lvl1)
        district = _location_name(lvl2) or _location_name(lvl3)
        name = (
            _location_name(lvl4)
            or _location_name(lvl3)
            or _location_name(lvl2)
            or city
            or "Egypt"
        )

        point: GeoPoint | None = None
        geo = source.get("geography")
        if isinstance(geo, dict):
            lat = _safe_decimal(geo.get("lat"))
            lng = _safe_decimal(geo.get("lng"))
            if lat is not None and lng is not None:
                point = GeoPoint(latitude=lat, longitude=lng)

        return Location(
            name=name,
            country_code="EG",
            city=city,
            district=district,
            point=point,
        )

    def _url(self, source: dict[str, Any]) -> str | None:
        external_id = source.get("externalID")
        slug = source.get("slug_l1") or source.get("slug")
        if not external_id or not slug:
            return None
        return f"https://www.dubizzle.com.eg/en/ad/{slug}-ID{external_id}.html"

    def _attributes(self, source: dict[str, Any]) -> dict[str, Any]:
        extra = source.get("extraFields", {})
        agency = source.get("agency") or {}
        agency_name = agency.get("name_l1") or agency.get("name")
        formatted_type = _formatted_type_label(source)

        attributes: dict[str, Any] = {
            **(extra if isinstance(extra, dict) else {}),
            "product": source.get("product"),
            "agency_name": agency_name,
            "photo_count": source.get("photoCount"),
            "seller_verified": source.get("isSellerVerified"),
            "formatted_type": formatted_type,
        }
        return attributes


def _location_name(level: Any) -> str | None:
    if isinstance(level, dict):
        return level.get("name_l1") or level.get("name") or None
    return None


def _formatted_type_label(source: dict[str, Any]) -> str | None:
    formatted = source.get("formattedExtraFields", [])
    if not isinstance(formatted, list):
        return None
    for field in formatted:
        if isinstance(field, dict) and field.get("attribute") == "type":
            return field.get("formattedValue_l1") or field.get("formattedValue")
    return None


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        return int(digits) if digits else None
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (ValueError, TypeError, OSError):
        return None
