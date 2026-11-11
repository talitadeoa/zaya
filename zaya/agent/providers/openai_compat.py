"""Adaptador para APIs compativeis com OpenAI (DeepSeek, OpenRouter, Ollama...)."""

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


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str = "",
        timeout: float = 900.0,
        headers: dict[str, str] | None = None,
        include_usage: bool = True,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.include_usage = include_usage
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        if headers:
            self._headers.update(headers)
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(timeout, connect=15.0),
        )

    # ------------------------------------------------------------- serializacao
    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    chunks.append(str(part.get("text") or ""))
            return "".join(chunks)
        return ""

    def _messages(self, request: ChatRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if request.system.strip():
            messages.append({"role": "system", "content": request.system})
        for message in request.messages:
            if message.role == "assistant" and message.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                                },
                            }
                            for call in message.tool_calls
                        ],
                    }
                )
            elif message.role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.content,
                    }
                )
            else:
                messages.append(
                    {"role": message.role, "content": message.content}
                )
        return messages

    def _body(self, request: ChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": self._messages(request),
            "stream": True,
        }
        if request.max_tokens > 0:
            body["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.tools:
            body["tools"] = [{"type": "function", "function": tool} for tool in request.tools]
            body["tool_choice"] = "auto"
        if self.include_usage:
            body["stream_options"] = {"include_usage": True}
        return body

    @staticmethod
    def _usage(payload: dict[str, Any]) -> Usage | None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens") or 0)
        # DeepSeek expoe o cache em campos proprios.
        cached = int(usage.get("prompt_cache_hit_tokens") or cached or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        return Usage(
            input_tokens=prompt,
            output_tokens=completion,
            cached_tokens=cached,
            total_tokens=total,
        )

    # ------------------------------------------------------------------ stream
    async def stream(self, request: ChatRequest) -> AsyncIterator[Delta]:
        url = f"{self.base_url}/chat/completions"
        body = self._body(request)
        try:
            async for delta in self._stream_body(url, body):
                yield delta
        except ProviderError as exc:
            # Alguns endpoints compativeis recusam stream_options. Nesse caso,
            # repete sem usage em vez de derrubar o turno.
            if (
                self.include_usage
                and exc.status_code == 400
                and "stream_options" in (exc.body or "").lower()
            ):
                body.pop("stream_options", None)
                async for delta in self._stream_body(url, body):
                    yield delta
            else:
                raise

    async def _stream_body(
        self, url: str, body: dict[str, Any]
    ) -> AsyncIterator[Delta]:
        latest_usage: Usage | None = None
        try:
            async with self._client.stream("POST", url, json=body) as resp:
                if resp.status_code >= 400:
                    raw = (await resp.aread()).decode("utf-8", errors="replace")
                    raise ProviderError(
                        self._error_message(resp.status_code, raw),
                        status_code=resp.status_code,
                        body=raw[:2000],
                    )
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        if latest_usage is not None:
                            yield UsageDelta(latest_usage)
                        yield Finish("stop")
                        return
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    error = data.get("error")
                    if error:
                        raise ProviderError(str(error))
                    if self.include_usage:
                        usage = self._usage(data)
                        if usage is not None:
                            latest_usage = usage
                    choice = (data.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    if isinstance(delta.get("content"), str) and delta["content"]:
                        yield TextDelta(delta["content"])
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(reasoning, str) and reasoning:
                        yield ReasoningDelta(reasoning)
                    for call in delta.get("tool_calls") or []:
                        function = call.get("function") or {}
                        yield ToolCallDelta(
                            index=int(call.get("index") or 0),
                            id=str(call.get("id") or ""),
                            name=str(function.get("name") or ""),
                            arguments=str(function.get("arguments") or ""),
                        )
                    if choice.get("finish_reason"):
                        yield Finish(str(choice["finish_reason"]))
                if latest_usage is not None:
                    yield UsageDelta(latest_usage)
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
            resp = await self._client.get(f"{self.base_url}/models")
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"nao foi possivel listar modelos de {self.name}: {exc}") from exc
        items = data.get("data") if isinstance(data, dict) else data
        models = []
        for item in items or []:
            identifier = item.get("id") if isinstance(item, dict) else item
            if identifier:
                models.append(str(identifier))
        return sorted(models)

    async def ping(self) -> dict[str, Any]:
        models = await self.list_models()
        return {"provider": self.name, "models": len(models)}

    async def aclose(self) -> None:
        await self._client.aclose()
