"""What the LLM is allowed to answer, validated before it becomes a rule.

Deliberately permissive in shape (small models wrap single values in lists, or
forget to) and strict in meaning (enums, compilable regexes, known condition
types). Anything that fails here is sent back to the model as feedback.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from realestate.domain.enums import ListingType, PropertyType

ROUTE_DECISIONS = ("FETCH", "SKIP", "DEFER")
PAGE_KINDS = ("LIST", "DETAIL", "OTHER")
#: ``\\d`` in a regex matches a literal backslash then "d": a JSON escape doubled
#: once too often. It compiles, matches nothing, and fails silently.
_OVERESCAPED = re.compile(r"\\\\[dDsSwWbB.()\[\]+*?]")


def overescaped_regexes(value: Any, path: str = "") -> list[str]:
    """``path: pattern`` for every ``regex``/``pattern`` string escaped twice."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            where = f"{path}.{key}" if path else str(key)
            doubled = isinstance(child, str) and _OVERESCAPED.search(child)
            if key in ("regex", "pattern") and doubled:
                found.append(f"{where}: {child!r}")
            else:
                found.extend(overescaped_regexes(child, where))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(overescaped_regexes(child, f"{path}[{index}]"))
    return found


class NavRuleProposal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    group: int = Field(ge=1)
    pattern: str = Field(min_length=1, max_length=400)
    decision: Literal["FETCH", "SKIP", "DEFER"]
    page_kind: Literal["LIST", "DETAIL", "OTHER"] = "OTHER"
    priority: int = Field(default=50, ge=0, le=100)
    reason: str = ""

    @field_validator("decision", "page_kind", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"regex does not compile: {exc}") from exc
        if value.strip() in {".*", ".+", "^", "$", "^.*$", "http", "https?://"}:
            raise ValueError("pattern matches everything")
        return value


class NavRuleBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rules: list[NavRuleProposal]


class VocabEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pattern: str
    value: str

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        re.compile(value, re.IGNORECASE)
        return value

    @field_validator("value", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


class JsonSelection(BaseModel):
    """Select array members by one scalar field, without executing expressions."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=200)
    equals: str | int | float | bool


class JsonItemFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=200)
    pattern: str = Field(min_length=1, max_length=400)
    select: JsonSelection | None = None

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"regex does not compile: {exc}") from exc
        return value


class TemplateProposal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="template", max_length=80)
    page_kind: Literal["LIST", "DETAIL", "OTHER"]
    conditions: list[dict[str, Any]] = Field(min_length=1)
    items: dict[str, Any] | None = None
    fields: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    links: list[dict[str, Any]] = Field(default_factory=list)
    default_currency: str | None = None
    default_rental_price_type: Literal["PER_MONTH", "UNKNOWN"] = "PER_MONTH"
    required_fields: list[str] = Field(default_factory=list)
    strict_classification: bool = False
    allow_title_price: bool = True
    identity_url_pattern: str | None = Field(default=None, max_length=400)
    vocab: dict[str, list[VocabEntry]] = Field(default_factory=dict)

    @field_validator("items")
    @classmethod
    def _filters(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and "filters" in value:
            if not value.get("script") or not isinstance(value["filters"], list):
                raise ValueError("item filters require JSON items and a list of filters")
            if len(value["filters"]) > 8:
                raise ValueError("at most eight item filters are supported")
            value = {
                **value,
                "filters": [
                    JsonItemFilter.model_validate(rule).model_dump(exclude_none=True)
                    for rule in value["filters"]
                ],
            }
        return value

    @field_validator("identity_url_pattern")
    @classmethod
    def _identity_pattern(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                pattern = re.compile(value)
            except re.error as exc:
                raise ValueError(f"identity URL regex does not compile: {exc}") from exc
            if pattern.groups != 1:
                raise ValueError("identity URL pattern needs exactly one ID capture group")
        return value

    @field_validator("fields")
    @classmethod
    def _selections(cls, value: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        for alternatives in value.values():
            for spec in alternatives:
                if "select" in spec:
                    spec["select"] = JsonSelection.model_validate(spec["select"]).model_dump()
        return value

    @model_validator(mode="after")
    def _required_fields(self) -> TemplateProposal:
        if not set(self.required_fields) <= self.fields.keys():
            raise ValueError("required_fields must refer to declared fields")
        if self.strict_classification and "listing_type" not in self.fields:
            raise ValueError("strict_classification requires a listing_type field")
        return self

    @field_validator("page_kind", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("conditions", "links", mode="before")
    @classmethod
    def _listify(cls, value: Any) -> Any:
        if value is None:
            return []
        return [value] if isinstance(value, dict) else value

    @field_validator("fields", mode="before")
    @classmethod
    def _listify_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            name: ([spec] if isinstance(spec, dict) else spec)
            for name, spec in value.items()
            if spec is not None
        }

    @field_validator("vocab", mode="before")
    @classmethod
    def _vocab(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return {}
        allowed = {
            "listing_type": {item.value for item in ListingType},
            "property_type": {item.value for item in PropertyType},
        }
        cleaned: dict[str, list[Any]] = {}
        for key, entries in value.items():
            if key not in allowed or not isinstance(entries, list):
                continue
            cleaned[key] = [
                entry
                for entry in entries
                if isinstance(entry, dict)
                and str(entry.get("value", "")).upper() in allowed[key]
                and entry.get("pattern")
            ]
        return cleaned

    @field_validator("default_currency", mode="before")
    @classmethod
    def _currency(cls, value: Any) -> Any:
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value.strip()):
            return value.strip().upper()
        return None

    @model_validator(mode="after")
    def _single_escaping(self) -> TemplateProposal:
        doubled = overescaped_regexes(
            {"conditions": self.conditions, "fields": self.fields, "links": self.links}
        )
        if doubled:
            raise ValueError(
                "regex escaped twice (\\\\d matches a backslash, not a digit); use single "
                "escaping such as \\d in: " + "; ".join(doubled[:4])
            )
        return self

    def action(self) -> dict[str, Any]:
        """The ``TEMPLATE`` node action this proposal compiles to."""
        return {
            "page_kind": self.page_kind,
            "items": self.items,
            "fields": self.fields,
            "links": self.links,
            "default_currency": self.default_currency,
            "default_rental_price_type": self.default_rental_price_type,
            "required_fields": self.required_fields,
            "strict_classification": self.strict_classification,
            "allow_title_price": self.allow_title_price,
            "identity_url_pattern": self.identity_url_pattern,
            "name": self.name,
        }

    def condition(self) -> dict[str, Any]:
        return (
            self.conditions[0]
            if len(self.conditions) == 1
            else {"type": "all", "conditions": self.conditions}
        )

    def vocab_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            key: [entry.model_dump() for entry in entries] for key, entries in self.vocab.items()
        }
