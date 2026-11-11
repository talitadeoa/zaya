"""Eventos tipados emitidos pelo loop do agente.

O consumidor (``controller.turns``) so conhece estes tipos; nenhum formato de
provedor ou de servidor vaza para a interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentEvent:
    session_id: str = ""


@dataclass
class TurnStarted(AgentEvent):
    message_id: str = ""


@dataclass
class TextDelta(AgentEvent):
    text: str = ""
    message_id: str = ""


@dataclass
class ReasoningDelta(AgentEvent):
    text: str = ""
    message_id: str = ""


@dataclass
class ToolStarted(AgentEvent):
    call_id: str = ""
    name: str = ""
    detail: str = ""


@dataclass
class ToolFinished(AgentEvent):
    call_id: str = ""
    name: str = ""
    status: str = "completed"  # completed | error
    error: str = ""
    diff_name: str = ""
    diff: str = ""


@dataclass
class PermissionAsked(AgentEvent):
    request_id: str = ""
    tool: str = ""
    detail: str = ""
    resources: list[str] = field(default_factory=list)


@dataclass
class TurnCompleted(AgentEvent):
    message_id: str = ""
    text: str = ""
    reasoning: str = ""
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class TurnFailed(AgentEvent):
    message: str = ""


@dataclass
class TurnCancelled(AgentEvent):
    pass


Event = (
    TurnStarted
    | TextDelta
    | ReasoningDelta
    | ToolStarted
    | ToolFinished
    | PermissionAsked
    | TurnCompleted
    | TurnFailed
    | TurnCancelled
)
