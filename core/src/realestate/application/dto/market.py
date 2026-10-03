"""Market trend parameters and responses."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from realestate.domain.enums import ListingType, PropertyType
from realestate.domain.market import (
    RegionSummary,
    TrendInterval,
    TrendMetric,
    TrendPoint,
    TrendQuery,
    TrendSeries,
)

#: Wider ranges are allowed by the data but make weekly series unreadable and
#: the query needlessly heavy.
MAX_RANGE_YEARS = 25


def _midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February in a year without one
        return day.replace(year=day.year - years, day=28)


class TrendQueryParams(BaseModel):
    """Every filter ``GET /markets/trends`` accepts."""

    model_config = ConfigDict(extra="forbid")

    country_code: str = Field(default="EG", min_length=2, max_length=2)
    city: str = Field(min_length=1, max_length=128)
    district: str | None = Field(default=None, max_length=128)
    listing_type: ListingType = ListingType.SALE
    property_type: list[PropertyType] = Field(default_factory=list)
    currency: str = Field(default="EGP", min_length=3, max_length=3)
    metric: TrendMetric = TrendMetric.MEDIAN_PRICE
    interval: TrendInterval = TrendInterval.MONTH
    date_from: date | None = Field(
        default=None, description="Inclusive; defaults to the widest allowed range"
    )
    date_to: date | None = Field(default=None, description="Inclusive; defaults to today")
    min_samples: int = Field(default=5, ge=1, le=1000)

    @model_validator(mode="after")
    def _check_range(self) -> TrendQueryParams:
        if self.date_from is not None and self.date_to is not None:
            if self.date_from > self.date_to:
                raise ValueError("date_from must not be later than date_to")
            if self.date_to - self.date_from > timedelta(days=366 * MAX_RANGE_YEARS):
                raise ValueError(f"the date range may span at most {MAX_RANGE_YEARS} years")
        return self

    def to_domain(self, *, today: date | None = None) -> TrendQuery:
        """Convert to a half-open ``[from, to)`` query in UTC."""
        last = self.date_to or today or datetime.now(UTC).date()
        first = self.date_from or _years_before(last, MAX_RANGE_YEARS)
        return TrendQuery(
            country_code=self.country_code.upper(),
            city=self.city.strip(),
            district=self.district.strip() if self.district else None,
            listing_type=self.listing_type,
            property_types=tuple(self.property_type),
            currency=self.currency.upper(),
            metric=self.metric,
            interval=self.interval,
            date_from=_midnight_utc(first),
            date_to=_midnight_utc(last + timedelta(days=1)),
            min_samples=self.min_samples,
        )


class TrendPointRead(BaseModel):
    """One bucket; figures are null when ``sample_size`` < ``min_samples``."""

    period_start: datetime
    sample_size: int
    value: Decimal | None = None
    p25: Decimal | None = None
    p75: Decimal | None = None

    @classmethod
    def from_domain(cls, point: TrendPoint) -> TrendPointRead:
        return cls(
            period_start=point.period_start,
            sample_size=point.sample_size,
            value=point.value,
            p25=point.p25,
            p75=point.p75,
        )


class TrendSeriesRead(BaseModel):
    """A region's price series."""

    region_label: str
    city: str
    district: str | None = None
    metric: TrendMetric
    interval: TrendInterval
    currency: str
    points: list[TrendPointRead]

    @classmethod
    def from_domain(cls, series: TrendSeries) -> TrendSeriesRead:
        return cls(
            region_label=series.region_label,
            city=series.city,
            district=series.district,
            metric=series.metric,
            interval=series.interval,
            currency=series.currency,
            points=[TrendPointRead.from_domain(point) for point in series.points],
        )


class RegionRead(BaseModel):
    """A place with price history. ``district`` is null for city-level adverts."""

    country_code: str
    city: str
    district: str | None = None
    listing_count: int
    observation_count: int
    first_observed: datetime
    last_observed: datetime

    @classmethod
    def from_domain(cls, region: RegionSummary) -> RegionRead:
        return cls(
            country_code=region.country_code,
            city=region.city,
            district=region.district,
            listing_count=region.listing_count,
            observation_count=region.observation_count,
            first_observed=region.first_observed,
            last_observed=region.last_observed,
        )
