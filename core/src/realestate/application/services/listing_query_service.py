"""Read side of the API."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from realestate.domain.exceptions import NotFoundError
from realestate.domain.models import Listing, Page, RawDocument
from realestate.domain.ports.repositories import ListingRepository, RawDocumentRepository
from realestate.domain.query import MAX_LIMIT, ListingQuery


class ListingQueryService:
    """Serves listing searches and lookups.

    Thin by design: the filtering itself belongs to the repository, which is the
    only component that should know how the data is stored.
    """

    def __init__(
        self,
        *,
        listings: ListingRepository,
        documents: RawDocumentRepository,
    ) -> None:
        self._listings = listings
        self._documents = documents

    async def search(self, query: ListingQuery) -> Page[Listing]:
        """Run a search, enforcing the page-size ceiling one final time.

        The DTO caps it too; this guard covers callers that build a
        :class:`ListingQuery` directly, such as the CLI.
        """
        if query.limit > MAX_LIMIT:
            query = replace(query, limit=MAX_LIMIT)
        return await self._listings.search(query)

    async def get(self, listing_id: UUID) -> Listing:
        """Fetch one listing.

        Raises:
            NotFoundError: if no listing has that id.
        """
        listing = await self._listings.get(listing_id)
        if listing is None:
            raise NotFoundError("Listing", listing_id)
        return listing

    async def get_raw_document(self, listing_id: UUID) -> RawDocument:
        """Fetch the archived payload a listing was parsed from.

        Raises:
            NotFoundError: if the listing is unknown or has no provenance record.
        """
        listing = await self.get(listing_id)
        if listing.raw_document_id is None:
            raise NotFoundError("RawDocument for listing", listing_id)
        document = await self._documents.get(listing.raw_document_id)
        if document is None:
            raise NotFoundError("RawDocument", listing.raw_document_id)
        return document

    async def count(self, source_key: str | None = None) -> int:
        return await self._listings.count(source_key)
