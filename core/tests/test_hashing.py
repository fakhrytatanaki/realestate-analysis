"""Content-hash change detection."""

from __future__ import annotations

from decimal import Decimal

from realestate.domain.hashing import compute_content_hash
from tests.conftest import make_draft


def test_identical_drafts_hash_identically() -> None:
    assert compute_content_hash(make_draft()) == compute_content_hash(make_draft())


def test_price_change_changes_the_hash() -> None:
    before = compute_content_hash(make_draft(amount=Decimal("1000")))
    after = compute_content_hash(make_draft(amount=Decimal("1100")))

    assert before != after


def test_attribute_ordering_does_not_change_the_hash() -> None:
    """A source that reshuffles its JSON must not look like a changed ad."""
    first = make_draft(attributes={"floor": 3, "furnished": True})
    second = make_draft(attributes={"furnished": True, "floor": 3})

    assert compute_content_hash(first) == compute_content_hash(second)


def test_coordinate_change_changes_the_hash() -> None:
    before = compute_content_hash(make_draft(latitude=30.0, longitude=31.0))
    after = compute_content_hash(make_draft(latitude=30.5, longitude=31.0))

    assert before != after


def test_hash_is_hex_sha256() -> None:
    digest = compute_content_hash(make_draft())

    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")
