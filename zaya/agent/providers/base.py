"""Contrato dos adaptadores de provedor de modelo."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from zaya.agent.types import ChatMessage


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    system: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = 0.3
    max_tokens: int = 4096
    # Prefixo estavel de ``system`` que pode ser cacheado pelo provedor.
    cache_prefix: str = ""
    # Marca tambem a ultima mensagem: o prefixo da conversa passa a ser
    # reaproveitado no turno seguinte (Anthropic).
    cache_messages: bool = False


@dataclass
class TextDelta:
    text: str


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class ToolCallDelta:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class Finish:
    reason: str = ""


@dataclass
class Usage:
    """Uso normalizado de tokens, independente do provedor.

    ``input_tokens`` inclui tokens cacheados; ``cached_tokens`` e o quanto
    veio de cache (leitura) e ``cache_write_tokens`` o que foi gravado.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )

    @property
    def uncached_input(self) -> int:
        return max(0, self.input_tokens - self.cached_tokens)

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, int] | None) -> Usage:
        data = data or {}
        return cls(
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
            cached_tokens=int(data.get("cached_tokens") or 0),
            cache_write_tokens=int(data.get("cache_write_tokens") or 0),
            total_tokens=int(data.get("total_tokens") or 0),
        )


@dataclass
class UsageDelta:
    usage: Usage = field(default_factory=Usage)


Delta = TextDelta | ReasoningDelta | ToolCallDelta | Finish | UsageDelta


class Provider(Protocol):
    name: str
    base_url: str

    def stream(self, request: ChatRequest) -> AsyncIterator[Delta]: ...

    async def list_models(self) -> list[str]: ...

    async def ping(self) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...
