"""Market statistics: price trends per region."""

from __future__ import annotations

from realestate.domain.enums import ListingType
from realestate.domain.exceptions import InvalidQueryError
from realestate.domain.market import RegionSummary, TrendQuery, TrendSeries
from realestate.domain.ports.repositories import MarketStatsRepository


class MarketTrendService:
    """Thin like :class:`ListingQueryService`: the aggregation is the
    repository's job, this only guards the query and labels the result."""

    def __init__(self, *, stats: MarketStatsRepository) -> None:
        self._stats = stats

    async def trend(self, query: TrendQuery) -> TrendSeries:
        """A region's price series.

        Raises:
            InvalidQueryError: if the date range is empty or inverted.
        """
        if query.date_from >= query.date_to:
            raise InvalidQueryError("date_from must be earlier than date_to")
        points = await self._stats.price_trend(query)
        return TrendSeries(
            region_label=query.region_label,
            places=query.places,
            metric=query.metric,
            interval=query.interval,
            currency=query.currency,
            points=points,
        )

    async def regions(
        self, *, country_code: str | None = None, listing_type: ListingType | None = None
    ) -> list[RegionSummary]:
        return await self._stats.regions(country_code=country_code, listing_type=listing_type)
