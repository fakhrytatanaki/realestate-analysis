"""Auth and market endpoints over HTTP, with in-memory collaborators."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from realestate.api import deps
from realestate.application.services.auth_service import AuthService, AuthSettings
from realestate.application.services.market_service import MarketTrendService
from realestate.config.settings import Settings
from realestate.domain.enums import ListingType
from realestate.domain.market import RegionSummary, TrendPoint, TrendQuery
from realestate.domain.ports.repositories import MarketStatsRepository
from realestate.main import create_app
from tests.conftest import FakePasswordHasher, InMemorySessionRepository, InMemoryUserRepository

PASSWORD = "correct horse battery"


class FakeMarketStats(MarketStatsRepository):
    def __init__(self) -> None:
        self.queries: list[TrendQuery] = []

    async def price_trend(self, query: TrendQuery) -> list[TrendPoint]:
        self.queries.append(query)
        return [
            TrendPoint(
                period_start=datetime(2015, 1, 1, tzinfo=UTC),
                sample_size=12,
                value=Decimal("1500000.00"),
                p25=Decimal("1100000.00"),
                p75=Decimal("2100000.00"),
            ),
            TrendPoint(period_start=datetime(2015, 2, 1, tzinfo=UTC), sample_size=2),
        ]

    async def regions(
        self, *, country_code: str | None, listing_type: ListingType | None
    ) -> list[RegionSummary]:
        return [
            RegionSummary(
                country_code="EG",
                city="Cairo",
                district="Maadi",
                listing_count=40,
                observation_count=95,
                first_observed=datetime(2014, 3, 1, tzinfo=UTC),
                last_observed=datetime(2019, 8, 1, tzinfo=UTC),
            )
        ]


@pytest.fixture
def stats() -> FakeMarketStats:
    return FakeMarketStats()


def _client(stats: FakeMarketStats, *, allow_signup: bool = True) -> Iterator[TestClient]:
    app = create_app(Settings())
    auth = AuthService(
        users=InMemoryUserRepository(),
        sessions=InMemorySessionRepository(),
        hasher=FakePasswordHasher(),
        settings=AuthSettings(allow_signup=allow_signup),
    )
    app.dependency_overrides[deps.get_auth] = lambda: auth
    app.dependency_overrides[deps.get_markets] = lambda: MarketTrendService(stats=stats)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(stats: FakeMarketStats) -> Iterator[TestClient]:
    yield from _client(stats)


def _register(client: TestClient, email: str = "ada@example.com") -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Ada"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["token"])


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_returns_session_without_password_hash(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register", json={"email": "Ada@Example.com", "password": PASSWORD}
    )

    body = response.json()
    assert response.status_code == 201
    assert body["token"]
    assert body["user"]["email"] == "ada@example.com"
    assert "password_hash" not in body["user"]


def test_register_duplicate_is_409(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/api/v1/auth/register", json={"email": "ada@example.com", "password": PASSWORD}
    )

    assert response.status_code == 409


def test_register_validates_input(client: TestClient) -> None:
    bad_email = client.post("/api/v1/auth/register", json={"email": "nope", "password": PASSWORD})
    short_password = client.post(
        "/api/v1/auth/register", json={"email": "ada@example.com", "password": "short"}
    )

    assert bad_email.status_code == 422
    assert short_password.status_code == 400


def test_register_disabled_is_403(stats: FakeMarketStats) -> None:
    for client in _client(stats, allow_signup=False):
        response = client.post(
            "/api/v1/auth/register", json={"email": "ada@example.com", "password": PASSWORD}
        )
        assert response.status_code == 403


def test_login_me_logout_cycle(client: TestClient) -> None:
    _register(client)
    login = client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    token = login.json()["token"]

    me = client.get("/api/v1/auth/me", headers=_bearer(token))
    logout = client.post("/api/v1/auth/logout", headers=_bearer(token))
    after = client.get("/api/v1/auth/me", headers=_bearer(token))

    assert login.status_code == 200
    assert me.json()["display_name"] == "Ada"
    assert logout.status_code == 204
    assert after.status_code == 401
    assert after.headers["WWW-Authenticate"] == "Bearer"


def test_wrong_password_is_401(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong password"}
    )

    assert response.status_code == 401


@pytest.mark.parametrize("header", [None, "Bearer", "Basic abc", "Bearer nope"])
def test_me_requires_a_valid_bearer(client: TestClient, header: str | None) -> None:
    headers = {"Authorization": header} if header else {}

    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


@pytest.mark.parametrize("path", ["/api/v1/markets/regions", "/api/v1/markets/trends?city=Cairo"])
def test_markets_require_login(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_regions(client: TestClient) -> None:
    token = _register(client)
    response = client.get("/api/v1/markets/regions?country_code=eg", headers=_bearer(token))

    assert response.status_code == 200
    assert response.json()[0]["district"] == "Maadi"


def test_trends_returns_series_with_gaps(client: TestClient, stats: FakeMarketStats) -> None:
    token = _register(client)
    response = client.get(
        "/api/v1/markets/trends",
        params={
            "city": "Cairo",
            "district": "Maadi",
            "listing_type": "SALE",
            "property_type": ["APARTMENT", "VILLA"],
            "metric": "median_price_per_sqm",
            "date_from": "2015-01-01",
            "date_to": "2015-12-31",
        },
        headers=_bearer(token),
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["region_label"] == "Cairo · Maadi"
    assert body["points"][0]["value"] == "1500000.00"
    assert body["points"][1]["value"] is None
    query = stats.queries[0]
    assert query.date_from == datetime(2015, 1, 1, tzinfo=UTC)
    assert query.date_to == datetime(2016, 1, 1, tzinfo=UTC)  # inclusive end, half-open query
    assert len(query.property_types) == 2


def test_trends_rejects_inverted_range(client: TestClient) -> None:
    token = _register(client)
    response = client.get(
        "/api/v1/markets/trends",
        params={"city": "Cairo", "date_from": "2016-01-01", "date_to": "2015-01-01"},
        headers=_bearer(token),
    )

    assert response.status_code == 422
