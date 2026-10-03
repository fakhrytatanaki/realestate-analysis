"""Contact and runtime-field redaction for compact induction views.

This transforms the prompt copy only; archived bytes and deterministic field
extraction remain independent of the model's view.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

_SENSITIVE = re.compile(
    r"phone|telephone|email|contact|whatsapp|password|secret|credential|token|session|"
    r"cookie|authorization|apikey|applicationid|searchkey|runtimeconfig",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\w)(?:\+?20[\s.-]?)?01[0125](?:[\s.-]?\d){8}(?!\w)")
_INTERNATIONAL_PHONE = re.compile(r"(?<!\w)\+\d(?:[\s().-]?\d){7,14}(?!\w)")


def sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE.search(re.sub(r"[^a-zA-Z]", "", key)))


def redact_text(text: str) -> str:
    """Redact contact text before truncating it or placing it in a prompt."""
    if "@" in text:
        text = _EMAIL.sub("[redacted]", text)
    text = _PHONE.sub("[redacted]", text)
    return _INTERNATIONAL_PHONE.sub("[redacted]", text)


def redact_attribute(value: str) -> str:
    decoded = unquote(value)
    if decoded.lower().startswith(("tel:", "mailto:", "javascript:")):
        return "[redacted]"
    if redact_text(decoded) != decoded:
        return "[redacted]"
    try:
        data = json.loads(decoded)
    except ValueError:
        # Query strings and handlers can hold credentials without being JSON.
        if re.search(r"(?:token|api[_-]?key|session|password|secret)=", decoded, re.IGNORECASE):
            return "[redacted]"
        return redact_text(value)
    return json.dumps(redact_data(data), ensure_ascii=False)


def redact_data(value: Any, *, depth: int = 0) -> Any:
    if depth > 10:
        return "..."
    if isinstance(value, dict):
        return {
            key: redact_data(child, depth=depth + 1)
            for key, child in value.items()
            if not sensitive_key(key)
        }
    if isinstance(value, list):
        return [redact_data(child, depth=depth + 1) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
