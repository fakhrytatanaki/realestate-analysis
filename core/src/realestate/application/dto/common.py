"""Shared response shapes."""

from __future__ import annotations

from pydantic import BaseModel, Field

from realestate.domain.models import Page as DomainPage


class PageMeta(BaseModel):
    """Paging envelope fields."""

    total: int = Field(description="Total rows matching the filters, ignoring paging")
    limit: int = Field(description="Page size that was applied")
    offset: int = Field(description="Row offset of this page")
    has_more: bool = Field(description="Whether another page follows")


class Page[T](BaseModel):
    """A page of results plus its paging metadata."""

    items: list[T]
    meta: PageMeta

    @classmethod
    def build[S](cls, page: DomainPage[S], items: list[T]) -> Page[T]:
        """Wrap already-converted ``items`` in the envelope of a domain page."""
        return cls(
            items=items,
            meta=PageMeta(
                total=page.total,
                limit=page.limit,
                offset=page.offset,
                has_more=page.offset + len(page.items) < page.total,
            ),
        )


class AcceptedResponse(BaseModel):
    """Acknowledgement for work that was queued rather than performed inline."""

    status: str = "accepted"
    detail: str
