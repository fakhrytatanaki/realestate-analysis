"""Port for a language model that answers with structured data.

The domain only knows "send these messages, get back a dict matching this JSON
schema". Which provider, which transport, and how structure is coaxed out of a
model that has no native schema mode are adapter concerns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class LlmMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """A completion. ``data`` is ``None`` when no JSON object could be read."""

    data: dict[str, Any] | None
    raw_text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    error: str | None = None


class StructuredLlm(ABC):
    """A chat model asked for one JSON object per call."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier, recorded with every decision."""

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[LlmMessage],
        *,
        schema: Mapping[str, Any],
        tool_name: str,
        tool_description: str,
    ) -> LlmResponse:
        """Ask for one object matching ``schema``.

        Raises:
            LlmError: when the model is unreachable after retries.
        """
