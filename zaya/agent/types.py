"""Tipos neutros do agente: modelo, sessao, mensagem e ferramenta.

Nada aqui conhece o formato de um provedor especifico. Os adaptadores em
``zaya.agent.providers`` traduzem estes tipos para HTTP e os eventos de volta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelRef:
    """Referencia de modelo no formato ``provider/model``.

    ``provider`` aqui e o nome do adaptador configurado (deepseek, openai,
    anthropic, ...), nao necessariamente o fornecedor do modelo.
    """

    provider_id: str
    model_id: str

    @classmethod
    def parse(cls, value: str) -> ModelRef:
        value = (value or "").strip()
        if not value:
            raise ValueError("modelo vazio")
        if "/" not in value:
            raise ValueError(f"modelo invalido '{value}': use provider/model")
        provider, _, model = value.partition("/")
        if not provider or not model:
            raise ValueError(f"modelo invalido '{value}': use provider/model")
        return cls(provider_id=provider, model_id=model)

    @property
    def slug(self) -> str:
        return f"{self.provider_id}/{self.model_id}"


@dataclass
class SessionInfo:
    id: str
    title: str = ""
    directory: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionInfo:
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            directory=str(data.get("directory") or ""),
            raw=data,
        )


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    """Mensagem no formato neutro usado pelo loop do agente."""

    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatMessage:
        calls = [
            ToolCall(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                arguments=dict(item.get("arguments") or {}),
            )
            for item in data.get("tool_calls") or []
        ]
        return cls(
            role=str(data.get("role") or "user"),
            content=str(data.get("content") or ""),
            tool_calls=calls,
            tool_call_id=str(data.get("tool_call_id") or ""),
            name=str(data.get("name") or ""),
        )


@dataclass
class AssistantMessage:
    id: str
    text: str
    reasoning: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def content(self) -> list[dict[str, str]]:
        """Compatibilidade: formato ``content`` de uma mensagem de texto."""
        return [{"type": "text", "text": self.text}]
