"""Price-trend aggregates over ``listing_observation``.

Raw SQL because the shape -- dedupe per listing per bucket, then percentiles --
has no ORM spelling. PostgreSQL-specific (``DISTINCT ON``, ``percentile_cont``,
three-argument ``date_trunc``), like the haversine filter in ``geo.py``.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from tortoise import Tortoise

from realestate.domain.enums import ListingType
from realestate.domain.market import (
    Place,
    RegionSummary,
    TrendInterval,
    TrendMetric,
    TrendPoint,
    TrendQuery,
)
from realestate.domain.ports.repositories import MarketStatsRepository

#: SQL fragments chosen from enums, never from caller text.
_METRIC_SQL: dict[TrendMetric, str] = {
    TrendMetric.MEDIAN_PRICE: "price",
    TrendMetric.MEDIAN_PRICE_PER_SQM: "price / area_sqm",
}
_INTERVAL_SQL: dict[TrendInterval, str] = {
    TrendInterval.WEEK: "week",
    TrendInterval.MONTH: "month",
    TrendInterval.QUARTER: "quarter",
}

_CENT = Decimal("0.01")


def _money(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(_CENT, ROUND_HALF_UP)


def _places_filter(places: tuple[Place, ...], values: list[Any]) -> str:
    """One predicate matching any of ``places``, binding its arrays onto ``values``.

    Arrays keep the parameter count fixed however many places are pooled. Being
    a single ``WHERE`` predicate, overlapping places cannot count a listing twice.
    """
    cities = [place.city for place in places if place.district is None]
    districts = [place for place in places if place.district is not None]
    clauses: list[str] = []
    if cities:
        values.append(cities)
        clauses.append(f"l.city = ANY(${len(values)}::text[])")
    if districts:
        values.append([place.city for place in districts])
        values.append([place.district for place in districts])
        clauses.append(
            f"(l.city, l.district) IN "
            f"(SELECT * FROM unnest(${len(values) - 1}::text[], ${len(values)}::text[]))"
        )
    return f"({' OR '.join(clauses)})"


class TortoiseMarketStatsRepository(MarketStatsRepository):
    """Aggregates computed in the database, one round trip per series."""

    async def price_trend(self, query: TrendQuery) -> list[TrendPoint]:
        values: list[Any] = [
            _INTERVAL_SQL[query.interval],
            query.country_code,
            query.listing_type.value,
            query.currency,
            query.price_type.value,
            query.date_from,
            query.date_to,
        ]
        filters = [
            "l.country_code = $2",
            "l.listing_type = $3",
            "o.currency = $4",
            "o.price_type = $5",
            "o.observed_at >= $6",
            "o.observed_at < $7",
            "o.price > 0",
        ]
        if query.places:
            filters.append(_places_filter(query.places, values))
        if query.property_types:
            values.append([kind.value for kind in query.property_types])
            filters.append(f"l.property_type = ANY(${len(values)}::text[])")
        if query.metric is TrendMetric.MEDIAN_PRICE_PER_SQM:
            # Area lives on the listing row, not on each observation: an
            # approximation that holds because adverts rarely change size.
            filters.append("l.area_sqm > 0")

        metric = _METRIC_SQL[query.metric]
        # Buckets are cut in UTC so they do not depend on the session timezone.
        # DISTINCT ON keeps each listing's latest observation per bucket, so an
        # advert archived ten times in a month still counts once.
        sql = f"""
            WITH scoped AS (
                SELECT o.listing_id, o.observed_at, o.price, l.area_sqm,
                       date_trunc($1, o.observed_at, 'UTC') AS bucket
                FROM listing_observation o
                JOIN listing l ON l.id = o.listing_id
                WHERE {" AND ".join(filters)}
            ), latest AS (
                SELECT DISTINCT ON (listing_id, bucket) bucket, price, area_sqm
                FROM scoped
                ORDER BY listing_id, bucket, observed_at DESC
            )
            SELECT bucket,
                   count(*) AS n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY {metric}) AS p50,
                   percentile_cont(0.25) WITHIN GROUP (ORDER BY {metric}) AS p25,
                   percentile_cont(0.75) WITHIN GROUP (ORDER BY {metric}) AS p75
            FROM latest
            GROUP BY bucket
            ORDER BY bucket
        """
        rows = await Tortoise.get_connection("default").execute_query_dict(sql, values)
        points: list[TrendPoint] = []
        for row in rows:
            n = int(row["n"])
            enough = n >= query.min_samples
            points.append(
                TrendPoint(
                    period_start=row["bucket"],
                    sample_size=n,
                    value=_money(row["p50"]) if enough else None,
                    p25=_money(row["p25"]) if enough else None,
                    p75=_money(row["p75"]) if enough else None,
                )
            )
        return points

    async def regions(
        self, *, country_code: str | None, listing_type: ListingType | None
    ) -> list[RegionSummary]:
        values: list[Any] = []
        filters = ["l.country_code IS NOT NULL", "l.city IS NOT NULL", "o.price > 0"]
        if country_code is not None:
            values.append(country_code)
            filters.append(f"l.country_code = ${len(values)}")
        if listing_type is not None:
            values.append(listing_type.value)
            filters.append(f"l.listing_type = ${len(values)}")
        sql = f"""
            SELECT l.country_code, l.city, l.district,
                   count(DISTINCT l.id) AS listings,
                   count(*) AS observations,
                   min(o.observed_at) AS first_observed,
                   max(o.observed_at) AS last_observed
            FROM listing_observation o
            JOIN listing l ON l.id = o.listing_id
            WHERE {" AND ".join(filters)}
            GROUP BY l.country_code, l.city, l.district
            ORDER BY listings DESC, l.city, l.district NULLS FIRST
        """
        rows = await Tortoise.get_connection("default").execute_query_dict(sql, values)
        return [
            RegionSummary(
                country_code=row["country_code"],
                city=row["city"],
                district=row["district"],
                listing_count=int(row["listings"]),
                observation_count=int(row["observations"]),
                first_observed=row["first_observed"],
                last_observed=row["last_observed"],
            )
            for row in rows
        ]
