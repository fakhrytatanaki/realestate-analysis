"""Account, session and price-trend repositories against real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from realestate.domain.enums import ListingType, PriceType, PropertyType
from realestate.domain.exceptions import ConflictError
from realestate.domain.market import Place, TrendInterval, TrendMetric, TrendQuery
from realestate.domain.models import Location, Price, Session, User
from realestate.infrastructure.db.repositories.listing import TortoiseListingRepository
from realestate.infrastructure.db.repositories.market import TortoiseMarketStatsRepository
from realestate.infrastructure.db.repositories.user import (
    TortoiseSessionRepository,
    TortoiseUserRepository,
)
from tests.conftest import make_draft

pytestmark = pytest.mark.usefixtures("initialised_db")

JAN = datetime(2013, 1, 10, tzinfo=UTC)
FEB = datetime(2013, 2, 10, tzinfo=UTC)


def _user(email: str = "ada@example.com") -> User:
    return User(
        id=uuid4(),
        email=email,
        display_name="Ada",
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(UTC),
    )


async def test_user_repository_round_trip_and_uniqueness() -> None:
    users = TortoiseUserRepository()
    user = await users.add(_user())

    assert (await users.get_by_email("ada@example.com")) == (await users.get(user.id))
    await users.update_password_hash(user.id, "new-hash")
    assert (await users.get(user.id)).password_hash == "new-hash"  # type: ignore[union-attr]
    with pytest.raises(ConflictError):
        await users.add(_user())


async def test_session_repository_lifecycle() -> None:
    user = await TortoiseUserRepository().add(_user())
    sessions = TortoiseSessionRepository()
    now = datetime.now(UTC).replace(microsecond=0)
    session = await sessions.add(
        Session(
            id=uuid4(),
            user_id=user.id,
            token_hash="a" * 64,
            created_at=now,
            expires_at=now + timedelta(days=1),
            last_used_at=now,
        )
    )

    found = await sessions.get_by_token_hash("a" * 64)
    assert found is not None and found.user_id == user.id
    await sessions.touch(session.id, at=now + timedelta(hours=1))
    assert (await sessions.get_by_token_hash("a" * 64)).last_used_at == now + timedelta(hours=1)  # type: ignore[union-attr]
    assert await sessions.delete_for_user(user.id) == 1
    assert await sessions.get_by_token_hash("a" * 64) is None


async def _seed(
    external_id: str,
    observations: list[tuple[datetime, str]],
    *,
    city: str = "Cairo",
    district: str | None = "Maadi",
    area: str | None = "100",
    listing_type: ListingType = ListingType.SALE,
    price_type: PriceType = PriceType.TOTAL,
    property_type: PropertyType = PropertyType.APARTMENT,
) -> None:
    repo = TortoiseListingRepository()
    for observed_at, amount in observations:
        draft = make_draft(
            external_id=external_id,
            listing_type=listing_type,
            property_type=property_type,
            price=Price(Decimal(amount), "EGP", price_type),
            location=Location(name="x", country_code="EG", city=city, district=district),
            area_sqm=Decimal(area) if area else None,
            observed_at=observed_at,
        )
        await repo.upsert_many([draft], source_key="fixture")


def _query(**overrides: object) -> TrendQuery:
    base: dict[str, object] = {
        "country_code": "EG",
        "places": (Place("Cairo"),),
        "listing_type": ListingType.SALE,
        "currency": "EGP",
        "date_from": datetime(2013, 1, 1, tzinfo=UTC),
        "date_to": datetime(2013, 3, 1, tzinfo=UTC),
        "min_samples": 1,
    }
    base.update(overrides)
    return TrendQuery(**base)  # type: ignore[arg-type]


async def test_price_trend_counts_each_listing_once_per_bucket() -> None:
    # Captured three times in January: only the latest January price counts.
    await _seed("a", [(JAN, "100"), (JAN + timedelta(days=5), "300"), (FEB, "500")])
    await _seed("b", [(JAN + timedelta(days=1), "200")])

    points = await TortoiseMarketStatsRepository().price_trend(_query())

    assert [(p.period_start.month, p.sample_size, p.value) for p in points] == [
        (1, 2, Decimal("250.00")),  # median of 300 and 200
        (2, 1, Decimal("500.00")),
    ]


async def test_price_trend_filters() -> None:
    await _seed("maadi", [(JAN, "1000")], district="Maadi", area="50")
    await _seed("zamalek", [(JAN, "9000")], district="Zamalek", area="100")
    await _seed("no-area", [(JAN, "7000")], district="Maadi", area=None)
    await _seed("villa", [(JAN, "5000")], property_type=PropertyType.VILLA)
    await _seed(
        "rent", [(JAN, "10")], listing_type=ListingType.RENT, price_type=PriceType.PER_MONTH
    )
    await _seed("instalment", [(JAN, "1")], price_type=PriceType.INSTALLMENT)
    await _seed("too-late", [(datetime(2013, 3, 1, tzinfo=UTC), "1")])
    stats = TortoiseMarketStatsRepository()

    maadi = await stats.price_trend(
        _query(places=(Place("Cairo", "Maadi"),), property_types=(PropertyType.APARTMENT,))
    )
    per_sqm = await stats.price_trend(
        _query(places=(Place("Cairo", "Maadi"),), metric=TrendMetric.MEDIAN_PRICE_PER_SQM)
    )
    rent = await stats.price_trend(_query(listing_type=ListingType.RENT))
    by_quarter = await stats.price_trend(_query(interval=TrendInterval.QUARTER))

    assert [p.sample_size for p in maadi] == [2]  # maadi + no-area; villa, instalment excluded
    assert [p.value for p in per_sqm] == [Decimal("35.00")]  # median of 1000/50 and 5000/100
    assert [p.value for p in rent] == [Decimal("10.00")]
    assert [p.sample_size for p in by_quarter] == [4]  # every SALE+TOTAL apartment and villa


async def test_price_trend_pools_places_and_covers_the_country() -> None:
    await _seed("maadi", [(JAN, "100")], district="Maadi")
    await _seed("zamalek", [(JAN, "200")], district="Zamalek")
    await _seed("dokki", [(JAN, "300")], city="Giza", district="Dokki")
    await _seed("agouza", [(JAN, "400")], city="Giza", district="Agouza")
    await _seed("nasr", [(JAN, "500")], city="Nasr City", district=None)
    stats = TortoiseMarketStatsRepository()

    async def sample_size(*places: Place) -> int:
        [point] = await stats.price_trend(_query(places=places))
        return point.sample_size

    assert await sample_size(Place("Cairo", "Maadi"), Place("Giza", "Dokki")) == 2
    assert await sample_size(Place("Cairo"), Place("Nasr City")) == 3
    # Overlapping places are a union: Maadi is not counted a second time.
    assert await sample_size(Place("Cairo"), Place("Cairo", "Maadi")) == 2
    assert await sample_size() == 5
    # A district only matches inside its own city.
    assert await stats.price_trend(_query(places=(Place("Giza", "Maadi"),))) == []


async def test_price_trend_hides_thin_buckets() -> None:
    await _seed("a", [(JAN, "100")])

    [point] = await TortoiseMarketStatsRepository().price_trend(_query(min_samples=2))

    assert point.sample_size == 1
    assert point.value is None and point.p25 is None


async def test_regions_summary() -> None:
    await _seed("a", [(JAN, "100"), (FEB, "200")], district="Maadi")
    await _seed("b", [(FEB, "100")], district=None)

    regions = await TortoiseMarketStatsRepository().regions(country_code="EG", listing_type=None)

    by_district = {region.district: region for region in regions}
    assert by_district["Maadi"].listing_count == 1
    assert by_district["Maadi"].observation_count == 2
    assert (by_district["Maadi"].first_observed, by_district["Maadi"].last_observed) == (JAN, FEB)
    assert by_district[None].listing_count == 1
