"""Market statistics vocabulary: price trends over the observation history.

Framework-free like the rest of the domain. The aggregation itself lives in the
repository; these types only say what is being asked for and what comes back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from realestate.domain.enums import ListingType, PriceType, PropertyType


class TrendInterval(StrEnum):
    """Width of one bucket on the time axis."""

    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"


class TrendMetric(StrEnum):
    """What each bucket summarises."""

    MEDIAN_PRICE = "median_price"
    MEDIAN_PRICE_PER_SQM = "median_price_per_sqm"


#: The only price reading comparable across adverts of each listing type: sale
#: prices are totals, rents are monthly. Anything else (instalments, per-night,
#: on-request) would mix incompatible figures into one median.
COMPARABLE_PRICE_TYPE: dict[ListingType, PriceType] = {
    ListingType.SALE: PriceType.TOTAL,
    ListingType.RENT: PriceType.PER_MONTH,
}


@dataclass(frozen=True, slots=True)
class TrendQuery:
    """One region's price series over ``[date_from, date_to)``."""

    country_code: str
    city: str
    listing_type: ListingType
    currency: str
    date_from: datetime
    date_to: datetime
    district: str | None = None
    property_types: tuple[PropertyType, ...] = ()
    metric: TrendMetric = TrendMetric.MEDIAN_PRICE
    interval: TrendInterval = TrendInterval.MONTH
    #: Buckets with fewer listings than this report no value (a gap), since a
    #: median of two adverts says more about the adverts than the market.
    min_samples: int = 5

    @property
    def price_type(self) -> PriceType:
        return COMPARABLE_PRICE_TYPE[self.listing_type]

    @property
    def region_label(self) -> str:
        return f"{self.city} · {self.district}" if self.district else self.city


@dataclass(frozen=True, slots=True)
class TrendPoint:
    """One bucket. ``value``/``p25``/``p75`` are ``None`` below ``min_samples``."""

    period_start: datetime
    sample_size: int
    value: Decimal | None = None
    p25: Decimal | None = None
    p75: Decimal | None = None


@dataclass(frozen=True, slots=True)
class TrendSeries:
    """A region's points plus enough of the query to label them."""

    region_label: str
    city: str
    district: str | None
    metric: TrendMetric
    interval: TrendInterval
    currency: str
    points: list[TrendPoint]


@dataclass(frozen=True, slots=True)
class RegionSummary:
    """A place with price history, for region pickers and default date spans."""

    country_code: str
    city: str
    district: str | None
    listing_count: int
    observation_count: int
    first_observed: datetime
    last_observed: datetime
