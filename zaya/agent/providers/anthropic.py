"""Adaptador para a API de mensagens da Anthropic."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from zaya.agent.providers.base import (
    ChatRequest,
    Delta,
    Finish,
    ProviderError,
    ReasoningDelta,
    TextDelta,
    ToolCallDelta,
    Usage,
    UsageDelta,
)

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    def __init__(
        self,
        *,
        name: str = "anthropic",
        base_url: str = "https://api.anthropic.com",
        api_key: str = "",
        timeout: float = 900.0,
        version: str = ANTHROPIC_VERSION,
        include_usage: bool = True,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.version = version
        self.include_usage = include_usage
        self._headers = {
            "Content-Type": "application/json",
            "anthropic-version": version,
        }
        if api_key:
            self._headers["x-api-key"] = api_key
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(timeout, connect=15.0),
        )

    # ------------------------------------------------------------- serializacao
    @staticmethod
    def _merge(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for message in messages:
            if merged and merged[-1]["role"] == message["role"]:
                merged[-1]["content"].extend(message["content"])
            else:
                merged.append(message)
        return merged

    def _messages(self, request: ChatRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id,
                                "content": message.content,
                            }
                        ],
                    }
                )
            elif message.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
                if blocks:
                    messages.append({"role": "assistant", "content": blocks})
            else:
                messages.append(
                    {"role": "user", "content": [{"type": "text", "text": message.content}]}
                )
        merged = self._merge(messages)
        if request.cache_messages and merged:
            content = merged[-1].get("content")
            if isinstance(content, list) and content:
                content[-1]["cache_control"] = {"type": "ephemeral"}
        return merged

    def _system(self, request: ChatRequest) -> str | list[dict[str, Any]]:
        """Marca o prefixo estavel com cache_control (prompt caching)."""
        system = request.system.strip()
        prefix = (request.cache_prefix or "").strip()
        if not prefix or not system.startswith(prefix):
            return request.system
        rest = system[len(prefix) :].strip()
        blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": prefix,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if rest:
            blocks.append({"type": "text", "text": rest})
        return blocks

    def _body(self, request: ChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or 4096,
            "messages": self._messages(request),
            "stream": True,
        }
        if request.system.strip():
            body["system"] = self._system(request)
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.tools:
            body["tools"] = [
                {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters") or {"type": "object"},
                }
                for tool in request.tools
            ]
        return body

    # ------------------------------------------------------------------ stream
    async def stream(self, request: ChatRequest) -> AsyncIterator[Delta]:
        url = f"{self.base_url}/v1/messages"
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        cache_write_tokens = 0
        try:
            async with self._client.stream("POST", url, json=self._body(request)) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise ProviderError(
                        self._error_message(resp.status_code, body),
                        status_code=resp.status_code,
                        body=body[:2000],
                    )
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    kind = event.get("type")
                    if kind == "error":
                        error = event.get("error") or {}
                        message = error.get("message") if isinstance(error, dict) else error
                        raise ProviderError(str(message or "erro da Anthropic"))
                    if kind == "message_start":
                        usage = (event.get("message") or {}).get("usage") or {}
                        input_tokens = int(usage.get("input_tokens") or 0)
                        output_tokens = int(usage.get("output_tokens") or 0)
                        cached_tokens = int(
                            usage.get("cache_read_input_tokens") or 0
                        )
                        cache_write_tokens = int(
                            usage.get("cache_creation_input_tokens") or 0
                        )
                    elif kind == "content_block_start":
                        block = event.get("content_block") or {}
                        index = int(event.get("index") or 0)
                        if block.get("type") == "tool_use":
                            yield ToolCallDelta(
                                index=index,
                                id=str(block.get("id") or ""),
                                name=str(block.get("name") or ""),
                            )
                        elif block.get("type") == "text" and block.get("text"):
                            yield TextDelta(str(block["text"]))
                    elif kind == "content_block_delta":
                        index = int(event.get("index") or 0)
                        delta = event.get("delta") or {}
                        dtype = delta.get("type")
                        if dtype == "text_delta" and delta.get("text"):
                            yield TextDelta(str(delta["text"]))
                        elif dtype == "input_json_delta" and delta.get("partial_json"):
                            yield ToolCallDelta(
                                index=index, arguments=str(delta["partial_json"])
                            )
                        elif dtype == "thinking_delta" and delta.get("thinking"):
                            yield ReasoningDelta(str(delta["thinking"]))
                    elif kind == "message_delta":
                        usage = event.get("usage") or {}
                        output_tokens = max(
                            output_tokens, int(usage.get("output_tokens") or 0)
                        )
                        reason = (event.get("delta") or {}).get("stop_reason")
                        if reason:
                            yield Finish(str(reason))
                    elif kind == "message_stop":
                        if self.include_usage:
                            total_input = (
                                input_tokens + cached_tokens + cache_write_tokens
                            )
                            yield UsageDelta(
                                Usage(
                                    input_tokens=total_input,
                                    output_tokens=output_tokens,
                                    cached_tokens=cached_tokens,
                                    cache_write_tokens=cache_write_tokens,
                                    total_tokens=total_input + output_tokens,
                                )
                            )
                        yield Finish("stop")
        except httpx.HTTPError as exc:
            raise ProviderError(f"falha de rede ao falar com {self.name}: {exc}") from exc

    @staticmethod
    def _error_message(status: int, body: str) -> str:
        detail = body.strip()
        try:
            data = json.loads(body)
            error = data.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or error)
            elif error:
                detail = str(error)
        except json.JSONDecodeError:
            pass
        return f"{status}: {detail[:500]}" if detail else f"HTTP {status}"

    # ------------------------------------------------------------------- meta
    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get(
                f"{self.base_url}/v1/models", params={"limit": 100}
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"nao foi possivel listar modelos de {self.name}: {exc}") from exc
        items = data.get("data") if isinstance(data, dict) else data
        models = [str(item.get("id")) for item in items or [] if isinstance(item, dict)]
        return sorted(models)

    async def ping(self) -> dict[str, Any]:
        models = await self.list_models()
        return {"provider": self.name, "models": len(models)}

    async def aclose(self) -> None:
        await self._client.aclose()
