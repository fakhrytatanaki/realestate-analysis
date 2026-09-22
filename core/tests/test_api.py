"""HTTP surface, driven against in-memory repositories.

The API is wired entirely through ports, so these tests need no database: every
dependency is swapped with ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from realestate.api import deps
from realestate.application.services.ingestion_service import IngestionService
from realestate.application.services.listing_query_service import ListingQueryService
from realestate.config.settings import AppSettings, Settings, SourceSettings
from realestate.domain.enums import ListingType, PropertyType
from realestate.main import create_app
from tests.conftest import (
    InMemoryListingRepository,
    InMemoryRawDocumentRepository,
    InMemoryScrapeRunRepository,
    NullLogProvider,
    make_draft,
    make_listing,
)

ADMIN_KEY = "test-admin-key"

# Downtown Cairo, Zamalek (~5 km away) and Seattle (a different continent).
CAIRO_DOWNTOWN = (30.0444, 31.2357)


@pytest.fixture
def listings_repo() -> InMemoryListingRepository:
    return InMemoryListingRepository(
        [
            make_listing(
                draft=make_draft(
                    external_id="cairo-rent",
                    title="Apartment in Downtown Cairo",
                    listing_type=ListingType.RENT,
                    property_type=PropertyType.APARTMENT,
                    amount=Decimal("18000"),
                    latitude=30.0444,
                    longitude=31.2357,
                    city="Cairo",
                )
            ),
            make_listing(
                draft=make_draft(
                    external_id="zamalek-rent",
                    title="Studio in Zamalek",
                    listing_type=ListingType.RENT,
                    property_type=PropertyType.STUDIO,
                    amount=Decimal("12500"),
                    latitude=30.0614,
                    longitude=31.2197,
                    city="Cairo",
                )
            ),
            make_listing(
                draft=make_draft(
                    external_id="seattle-rent",
                    title="Condo in Seattle",
                    listing_type=ListingType.RENT,
                    property_type=PropertyType.APARTMENT,
                    amount=Decimal("2400"),
                    currency="USD",
                    latitude=47.6145,
                    longitude=-122.3448,
                    city="Seattle",
                )
            ),
            make_listing(
                draft=make_draft(
                    external_id="cairo-sale",
                    title="Villa for sale in New Cairo",
                    listing_type=ListingType.SALE,
                    property_type=PropertyType.VILLA,
                    amount=Decimal("11000000"),
                    latitude=30.0131,
                    longitude=31.4969,
                    city="Cairo",
                )
            ),
        ]
    )


@pytest.fixture
def client(listings_repo: InMemoryListingRepository) -> Iterator[TestClient]:
    settings = Settings(
        app=AppSettings(admin_api_key=ADMIN_KEY),
        sources={"fixture": SourceSettings(enabled=True, interval_minutes=60)},
    )
    app = create_app(settings)

    documents = InMemoryRawDocumentRepository()
    runs = InMemoryScrapeRunRepository()
    log = NullLogProvider()
    queries = ListingQueryService(listings=listings_repo, documents=documents)

    container = app.state.container
    ingestion = IngestionService(
        registry=container.registry,
        blob=container.blob,
        listings=listings_repo,
        documents=documents,
        runs=runs,
        log=log,
    )

    app.dependency_overrides[deps.get_queries] = lambda: queries
    app.dependency_overrides[deps.get_runs] = lambda: runs
    app.dependency_overrides[deps.get_ingestion] = lambda: ingestion

    with TestClient(app) as test_client:
        test_client.runs = runs  # type: ignore[attr-defined]
        yield test_client


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_search_returns_everything_by_default(client: TestClient) -> None:
    body = client.get("/api/v1/listings").json()

    assert body["meta"]["total"] == 4
    assert len(body["items"]) == 4
    assert body["meta"]["has_more"] is False


def test_filter_by_listing_type(client: TestClient) -> None:
    body = client.get("/api/v1/listings", params={"listing_type": "RENT"}).json()

    assert body["meta"]["total"] == 3
    assert {item["listing_type"] for item in body["items"]} == {"RENT"}


def test_filter_by_price_ceiling_and_sort_ascending(client: TestClient) -> None:
    body = client.get(
        "/api/v1/listings", params={"price_max": 20000, "sort": "price_asc"}
    ).json()

    prices = [Decimal(item["price"]["amount"]) for item in body["items"]]
    assert prices == sorted(prices)
    assert all(price <= 20000 for price in prices)


def test_filter_by_property_type_accepts_repeats(client: TestClient) -> None:
    body = client.get(
        "/api/v1/listings", params=[("property_type", "APARTMENT"), ("property_type", "STUDIO")]
    ).json()

    assert {item["property_type"] for item in body["items"]} == {"APARTMENT", "STUDIO"}


def test_radius_search_excludes_far_away_listings(client: TestClient) -> None:
    """Seattle must not appear in a 10 km circle around Cairo."""
    body = client.get(
        "/api/v1/listings",
        params={"lat": CAIRO_DOWNTOWN[0], "lon": CAIRO_DOWNTOWN[1], "radius_km": 10},
    ).json()

    titles = {item["title"] for item in body["items"]}
    assert "Condo in Seattle" not in titles
    assert "Apartment in Downtown Cairo" in titles
    assert all(item["distance_km"] is not None for item in body["items"])


def test_distance_sort_orders_nearest_first(client: TestClient) -> None:
    body = client.get(
        "/api/v1/listings",
        params={
            "lat": CAIRO_DOWNTOWN[0],
            "lon": CAIRO_DOWNTOWN[1],
            "radius_km": 50,
            "sort": "distance",
        },
    ).json()

    distances = [item["distance_km"] for item in body["items"]]
    assert distances == sorted(distances)
    assert body["items"][0]["title"] == "Apartment in Downtown Cairo"


def test_incomplete_geo_parameters_are_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/listings", params={"lat": 30.0})

    assert response.status_code == 422
    assert "supplied together" in response.text


def test_unknown_filter_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/listings", params={"price_maximum": 100})

    assert response.status_code == 422


def test_paging_reports_more_results(client: TestClient) -> None:
    body = client.get("/api/v1/listings", params={"limit": 2}).json()

    assert len(body["items"]) == 2
    assert body["meta"]["total"] == 4
    assert body["meta"]["has_more"] is True


def test_get_single_listing(client: TestClient, listings_repo: InMemoryListingRepository) -> None:
    listing_id = next(iter(listings_repo.listings))

    response = client.get(f"/api/v1/listings/{listing_id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(listing_id)


def test_missing_listing_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/listings/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"] == "NotFoundError"


def test_sources_lists_stubs_as_unimplemented(client: TestClient) -> None:
    body = client.get("/api/v1/sources").json()

    by_key = {source["key"]: source for source in body}
    assert by_key["fixture"]["implemented"] is True
    assert by_key["fixture"]["enabled"] is True
    assert by_key["dubizzle_eg"]["implemented"] is True
    assert by_key["zillow"]["implemented"] is False


def test_trigger_requires_the_admin_key(client: TestClient) -> None:
    response = client.post("/api/v1/sources/fixture/runs")

    assert response.status_code == 401


def test_trigger_rejects_a_wrong_admin_key(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sources/fixture/runs", headers={"X-Admin-Key": "wrong"}
    )

    assert response.status_code == 401


def test_trigger_returns_202_with_a_run_id(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sources/fixture/runs", headers={"X-Admin-Key": ADMIN_KEY}
    )

    assert response.status_code == 202
    body = response.json()
    assert body["source_key"] == "fixture"
    assert body["id"]

    # The background task runs before TestClient returns, so the run is finished.
    follow_up = client.get(f"/api/v1/runs/{body['id']}")
    assert follow_up.status_code == 200
    assert follow_up.json()["status"] in {"SUCCESS", "PARTIAL", "FAILED"}


def test_trigger_on_an_unimplemented_source_returns_501(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sources/zillow/runs", headers={"X-Admin-Key": ADMIN_KEY}
    )

    assert response.status_code == 501
    assert response.json()["error"] == "DataSourceNotImplementedError"


def test_trigger_on_an_unknown_source_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sources/nope/runs", headers={"X-Admin-Key": ADMIN_KEY}
    )

    assert response.status_code == 404


def test_missing_run_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
