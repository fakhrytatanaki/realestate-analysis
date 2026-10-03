"""Ollama (cloud or self-hosted) as a :class:`StructuredLlm`.

Ollama Cloud does not support structured outputs (the ``format`` JSON-schema
parameter), so structure is obtained another way:

1. **Tool calling.** The schema is offered as the parameters of a single tool
   the prompt asks the model to call; cloud models trained for tools honour it.
2. **JSON fallback.** If the model answers in prose instead, or the server
   rejects tools for this model, the first JSON object in the reply is used, and
   tools are not offered again for the life of the client.

Either way the caller validates the result; this adapter only gets a dict out.
Concurrency is capped (default one request at a time, the free tier's limit).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from realestate.domain.exceptions import LlmError
from realestate.domain.ports.llm import LlmMessage, LlmResponse, StructuredLlm
from realestate.domain.ports.log_provider import LogProvider

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class OllamaSettings:
    base_url: str = "https://ollama.com"
    api_key: str | None = None
    model: str = "gemma4:31b"
    max_concurrency: int = 1
    timeout_seconds: float = 600.0
    temperature: float = 0.0
    seed: int = 7
    max_retries: int = 4
    use_tools: bool = True
    num_ctx: int | None = None


class OllamaLlm(StructuredLlm):
    def __init__(
        self,
        settings: OllamaSettings,
        *,
        log: LogProvider,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._log = log.bind(component="llm", model=settings.model)
        self._semaphore = asyncio.Semaphore(max(1, settings.max_concurrency))
        self._use_tools = settings.use_tools
        self._client: httpx.AsyncClient | None = None

    @property
    def model(self) -> str:
        return self._settings.model

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self._settings.api_key:
                headers["Authorization"] = f"Bearer {self._settings.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self._settings.base_url.rstrip("/"),
                timeout=self._settings.timeout_seconds,
                headers=headers,
                transport=self._transport,
            )
        return self._client

    async def complete(
        self,
        messages: Sequence[LlmMessage],
        *,
        schema: Mapping[str, Any],
        tool_name: str,
        tool_description: str,
    ) -> LlmResponse:
        async with self._semaphore:
            body = self._body(messages, schema, tool_name, tool_description)
            started = time.monotonic()
            try:
                payload = await self._post(body)
            except _ToolsUnsupportedError:
                await self._log.warning("model rejected tools; using JSON-only replies")
                self._use_tools = False
                body = self._body(messages, schema, tool_name, tool_description)
                payload = await self._post(body)
            latency_ms = int((time.monotonic() - started) * 1000)

        message = payload.get("message") or {}
        data = _from_tool_calls(message.get("tool_calls"), tool_name)
        if data is None:
            data = extract_json_object(str(message.get("content") or ""))
        raw = json.dumps(
            {"content": message.get("content"), "tool_calls": message.get("tool_calls")},
            ensure_ascii=False,
        )
        return LlmResponse(
            data=data,
            raw_text=raw,
            model=self.model,
            tokens_in=int(payload.get("prompt_eval_count") or 0),
            tokens_out=int(payload.get("eval_count") or 0),
            latency_ms=latency_ms,
            error=None if data is not None else "no JSON object in the reply",
        )

    def _body(
        self,
        messages: Sequence[LlmMessage],
        schema: Mapping[str, Any],
        tool_name: str,
        tool_description: str,
    ) -> dict[str, Any]:
        chat = [{"role": message.role, "content": message.content} for message in messages]
        options: dict[str, Any] = {
            "temperature": self._settings.temperature,
            "seed": self._settings.seed,
        }
        if self._settings.num_ctx:
            options["num_ctx"] = self._settings.num_ctx
        body: dict[str, Any] = {
            "model": self.model,
            "messages": chat,
            "stream": False,
            "options": options,
        }
        if self._use_tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool_description,
                        "parameters": dict(schema),
                    },
                }
            ]
            chat[-1]["content"] += f"\n\nAnswer by calling the `{tool_name}` tool exactly once."
        else:
            chat[-1]["content"] += (
                "\n\nAnswer with ONE JSON object and nothing else, matching this JSON schema:\n"
                + json.dumps(schema, ensure_ascii=False)
            )
        return body

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        last_error = "no attempt made"
        for attempt in range(1, self._settings.max_retries + 1):
            try:
                response = await self.client.post("/api/chat", json=body)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    try:
                        return dict(response.json())
                    except ValueError as exc:
                        last_error = f"invalid JSON from server: {exc}"
                elif (
                    response.status_code == 400
                    and "tool" in response.text.lower()
                    and "tools" in body
                ):
                    raise _ToolsUnsupportedError(response.text)
                elif response.status_code in (401, 403):
                    raise LlmError(
                        f"Ollama rejected the credentials ({response.status_code}); "
                        "set [llm] api_key or OLLAMA_API_KEY"
                    )
                elif response.status_code not in _RETRYABLE:
                    raise LlmError(f"Ollama returned {response.status_code}: {response.text[:500]}")
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if attempt < self._settings.max_retries:
                backoff = min(5.0 * 2 ** (attempt - 1), 120.0)
                await self._log.warning(
                    "llm request failed, retrying",
                    attempt=attempt,
                    error=last_error,
                    backoff_seconds=backoff,
                )
                await asyncio.sleep(backoff)
        raise LlmError(
            f"Ollama request failed after {self._settings.max_retries} attempts: {last_error}"
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class _ToolsUnsupportedError(Exception):
    pass


def _from_tool_calls(tool_calls: Any, tool_name: str) -> dict[str, Any] | None:
    if not isinstance(tool_calls, list):
        return None
    for call in tool_calls:
        function = (call or {}).get("function") or {}
        if function.get("name") not in (tool_name, None):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            arguments = extract_json_object(arguments)
        if isinstance(arguments, dict):
            return arguments
    return None


def extract_json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in free text, tolerating code fences and chatter."""
    candidates = [match.group(1) for match in _FENCE.finditer(text)] + [text]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        start = candidate.find("{")
        while start != -1:
            try:
                value, _ = decoder.raw_decode(candidate, start)
            except ValueError:
                start = candidate.find("{", start + 1)
                continue
            if isinstance(value, dict):
                return value
            start = candidate.find("{", start + 1)
    return None
