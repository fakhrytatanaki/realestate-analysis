"""Listing search and lookup."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from realestate.api.deps import QueryServiceDep
from realestate.application.dto.common import Page
from realestate.application.dto.listing import ListingRead
from realestate.application.dto.query import ListingQueryParams
from realestate.application.dto.source import RawDocumentRead

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("", response_model=Page[ListingRead], summary="Search aggregated listings")
async def search_listings(
    params: Annotated[ListingQueryParams, Query()],
    queries: QueryServiceDep,
) -> Page[ListingRead]:
    """Filter listings across every source.

    Supply ``lat``, ``lon`` and ``radius_km`` together for a radius search; the
    response then carries ``distance_km`` on each item and accepts
    ``sort=distance``.
    """
    page = await queries.search(params.to_domain())
    return Page[ListingRead].build(
        page, [ListingRead.from_domain(listing) for listing in page.items]
    )


@router.get("/{listing_id}", response_model=ListingRead, summary="Fetch one listing")
async def get_listing(listing_id: UUID, queries: QueryServiceDep) -> ListingRead:
    return ListingRead.from_domain(await queries.get(listing_id))


@router.get(
    "/{listing_id}/raw",
    response_model=RawDocumentRead,
    summary="Provenance of a listing",
)
async def get_listing_raw_document(
    listing_id: UUID, queries: QueryServiceDep
) -> RawDocumentRead:
    """Metadata for the archived payload this listing was parsed from.

    Returns the blob's locator rather than its bytes: payloads can be large, and
    reading them is an operator task, not a client one.
    """
    return RawDocumentRead.from_domain(await queries.get_raw_document(listing_id))
