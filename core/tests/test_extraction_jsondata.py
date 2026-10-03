"""Declarative JSON selection, decode diagnostics and sanitized induction views."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from realestate.application.rules.proposals import TemplateProposal
from realestate.domain.archive import ArchivedDocument
from realestate.domain.enums import PriceType
from realestate.infrastructure.extraction.document import ParsedDocument
from realestate.infrastructure.extraction.engine import HtmlRuleEngine
from realestate.infrastructure.extraction.jsondata import select_values, summarise
from realestate.infrastructure.extraction.normalisers import parse_rental_period


def test_summary_prioritizes_hits_over_larger_seo_arrays() -> None:
    state = {
        "seoTemplates": [{"name": "seo text"}] * 160,
        "algolia": {
            "content": {
                "hits": [{"externalID": "123", "title": "Apartment", "extraFields": {"price": 200}}]
                * 45
            }
        },
    }
    summary = summarise({"state": state, "window.state": state}, max_arrays=1)
    assert "algolia.content.hits" in summary
    assert "array of 45 objects" in summary
    assert "seoTemplates" not in summary


def test_summary_includes_primary_singleton_ahead_of_recommendations() -> None:
    ad = {"externalID": "123", "title": "Apartment", "extraFields": {"price": 200}}
    summary = summarise({"state": {"recommendations": [ad] * 50, "ad": {"data": ad}}}, max_arrays=1)
    assert 'path "ad.data"' in summary
    assert "object. Sample" in summary


def test_prompt_redacts_contacts_and_runtime_before_truncation_without_changing_data() -> None:
    secret = "secret-value-must-never-reach-the-model"
    state = {
        "ad": {
            "data": {
                "externalID": "123",
                "title": "Apartment",
                "extraFields": {"price": 200},
                "contactInfo": {"phoneNumber": "01012345678", "apiKey": secret},
                "description": "Call 01012345678 or seller@example.com",
                "authToken": secret,
                "nested": {"password": secret, "price": 500},
            }
        },
        "credentials": [{"name": secret}] * 50,
    }
    html = f'''<html><head><title>Call 01012345678</title></head><body>
        <h1>Apartment for sale</h1><p>seller@example.com / +201012345678</p>
        <div aria-label="Contact">private owner</div>
        <a href="mailto:seller@example.com">Email</a>
        <a href="https://example.invalid/?apiKey={secret}">link</a>
        <div data-phone="01012345678" data-state='{{"session": "{secret}", "price": 200}}'
             onclick="save('{secret}')">card</div>
        <script>window.state = {json.dumps(state)};</script></body></html>'''
    doc = ArchivedDocument(html.encode(), "https://example.invalid/")
    parsed = ParsedDocument(doc)
    view = HtmlRuleEngine().prompt_view(doc, budget_chars=24000)
    for forbidden in (secret, "01012345678", "seller@example.com", "private owner", "onclick"):
        assert forbidden not in view
    assert "price" in view and "200" in view
    assert "ad.data" in view
    assert len(view) <= 24000
    assert parsed.scripts["state"] == state  # only the prompt copy is redacted


def test_json_decoder_reports_malformed_state_but_never_executes_or_repairs_it() -> None:
    parsed = ParsedDocument(
        ArchivedDocument(
            b'<script>window.state = {"hits":[{"title":"truncated</script>'
            b"<script>var browserConfig = {javascriptOnly: true}; "
            b'window.good = {"price":200};</script>',
            "https://example.invalid/",
        )
    )
    assert "state" not in parsed.scripts
    assert parsed.scripts["good"] == {"price": 200}
    assert parsed.script_problems == ["malformed_json:state", "malformed_json:browserConfig"]


@pytest.mark.parametrize("level", [1, "1", True, None])
def test_array_selection_uses_typed_scalar_equality(level) -> None:
    data = {"location": [{"level": 0, "name": "Egypt"}, {"level": level, "name": "Cairo"}]}
    selected = select_values(data, {"path": "location", "field": "level", "equals": 1})
    assert bool(selected) == (type(level) is int)


def test_duplicate_array_levels_are_ambiguous() -> None:
    data = {"location": [{"level": 1, "name": "Cairo"}, {"level": 1, "name": "Alexandria"}]}
    assert select_values(data, {"path": "location", "field": "level", "equals": 1}) == []


@pytest.mark.parametrize(
    "selection",
    [
        {"path": "location", "field": "level", "equals": {"python": "code"}},
        {"path": "location", "field": "level", "equals": 1, "script": "bad"},
        {"path": "", "field": "level", "equals": 1},
    ],
)
def test_proposal_rejects_unbounded_or_unknown_selection(selection: dict) -> None:
    with pytest.raises(ValidationError):
        TemplateProposal(
            name="detail",
            page_kind="DETAIL",
            conditions=[{"type": "always"}],
            fields={"city": [{"select": selection, "json": "name"}]},
        )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("200 per day", PriceType.PER_NIGHT),
        ("إيجار يومي", PriceType.PER_NIGHT),
        ("annual rent", PriceType.PER_YEAR),
        ("weekly", PriceType.PER_WEEK),
        ("90000 EGP, 3 bedrooms", None),
        ("daily or monthly", None),
        ("per day or per month", None),
    ],
)
def test_rental_period_is_separate_from_the_amount(text: str, expected: PriceType | None) -> None:
    assert parse_rental_period(text) is expected
