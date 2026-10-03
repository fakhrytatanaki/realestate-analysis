"""Hand-checked extraction answers: the yardstick for rule changes.

Plausibility checks (selectors match, numbers parse) cannot tell a right
recipe from a wrong one that also populates fields. A gold set can: real
archived pages with the values a person confirmed, scored exactly on the
fields that matter most -- identity, title, amount, currency, sale/rent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from realestate.domain.archive import ArchivedDocument
from realestate.domain.models import ListingDraft

#: Fields compared exactly, in reporting order.
GOLD_FIELDS = ("external_id", "title", "amount", "currency", "listing_type")


@dataclass(frozen=True, slots=True)
class GoldItem:
    external_id: str
    title: str
    amount: Decimal | None
    currency: str | None
    listing_type: str


@dataclass(frozen=True, slots=True)
class GoldDocument:
    ref: str
    document: ArchivedDocument
    items: tuple[GoldItem, ...]


@dataclass(slots=True)
class GoldScore:
    items: int = 0
    exact: int = 0
    misses: Counter[str] = field(default_factory=Counter)

    @property
    def rate(self) -> float:
        return self.exact / self.items if self.items else 1.0

    def add(self, other: GoldScore) -> None:
        self.items += other.items
        self.exact += other.exact
        self.misses.update(other.misses)

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": self.items,
            "exact": self.exact,
            "rate": self.rate,
            "misses": dict(self.misses),
        }


class GoldSet(ABC):
    """Where gold documents come from (labelled files, a table, ...)."""

    @abstractmethod
    async def documents(self, source_key: str) -> list[GoldDocument]:
        """Verified gold documents for one source; empty when there are none."""


def _normalise_title(text: str) -> str:
    return " ".join(text.split()).casefold()


def score(drafts: Sequence[ListingDraft], items: Sequence[GoldItem]) -> GoldScore:
    """Exact-match score of ``drafts`` against the expected ``items``.

    An item counts only if every field in :data:`GOLD_FIELDS` matches; a
    missing draft is a miss on ``external_id``.
    """
    by_id = {draft.external_id: draft for draft in drafts}
    result = GoldScore(items=len(items))
    for item in items:
        draft = by_id.get(item.external_id)
        if draft is None:
            result.misses["external_id"] += 1
            continue
        wrong = [
            name
            for name, ok in (
                ("title", _normalise_title(draft.title) == _normalise_title(item.title)),
                ("amount", draft.price.amount == item.amount),
                (
                    "currency",
                    item.amount is None or draft.price.currency == (item.currency or ""),
                ),
                ("listing_type", draft.listing_type.value == item.listing_type),
            )
            if not ok
        ]
        if wrong:
            result.misses.update(wrong)
        else:
            result.exact += 1
    return result
