"""Market statistics. Signed-in users only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from realestate.api.deps import CurrentUserDep, MarketServiceDep
from realestate.application.dto.market import RegionRead, TrendQueryParams, TrendSeriesRead
from realestate.domain.enums import ListingType

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("/regions", response_model=list[RegionRead], summary="Regions with price history")
async def list_regions(
    _: CurrentUserDep,
    markets: MarketServiceDep,
    country_code: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    listing_type: ListingType | None = None,
) -> list[RegionRead]:
    """Every ``(country, city, district)`` with priced observations, busiest
    first, with its observed date span. Drives region pickers."""
    regions = await markets.regions(
        country_code=country_code.upper() if country_code else None,
        listing_type=listing_type,
    )
    return [RegionRead.from_domain(region) for region in regions]


@router.get("/trends", response_model=TrendSeriesRead, summary="Price trend for a region")
async def price_trend(
    _: CurrentUserDep,
    params: Annotated[TrendQueryParams, Query()],
    markets: MarketServiceDep,
) -> TrendSeriesRead:
    """Median asking price (or price per m²) per interval bucket, with the
    interquartile range.

    The region is every ``place`` pooled into one series (each listing counts
    once, even where places overlap), or the whole country when none is given.

    Built from the observation history, so archived captures contribute at
    their capture date. Each listing counts once per bucket. Sale prices are
    totals and rents are monthly; other price readings are excluded. Buckets
    with fewer than ``min_samples`` listings report null figures.
    """
    return TrendSeriesRead.from_domain(await markets.trend(params.to_domain()))
