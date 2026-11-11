"""Testa o consumidor de eventos do turno com os eventos tipados do agente."""

from __future__ import annotations

import asyncio
import logging

import pytest

from zaya.agent.errors import AgentError
from zaya.agent.events import (
    PermissionAsked,
    ReasoningDelta,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from zaya.controller.orchestrator import Orchestrator


class FakeUI:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.tools: list[tuple[str, str]] = []
        self.diffs: list[tuple[str, str]] = []
        self.stream_started = False
        self.stream_ended = False
        self.warnings: list[str] = []
        self.reasoning_text: list[str] = []
        self.permissions: list[tuple[str, list[str]]] = []

    def begin_stream(self) -> None:
        self.stream_started = True

    def end_stream(self) -> None:
        self.stream_ended = True

    def token(self, text: str) -> None:
        self.tokens.append(text)

    def tool(self, name: str, detail: str = "") -> None:
        self.tools.append((name, detail))

    def diff(self, filename: str, patch: str) -> None:
        self.diffs.append((filename, patch))

    def reasoning(self, text: str) -> None:
        self.reasoning_text.append(text)

    def warning(self, text: str) -> None:
        self.warnings.append(text)

    def permission(self, tool: str, patterns: list[str]) -> str:
        self.permissions.append((tool, patterns))
        return "once"


class FakeClient:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str, str]] = []
        self.aborted: list[str] = []

    async def abort(self, session_id: str) -> None:
        self.aborted.append(session_id)

    async def reply_permission(
        self, session_id: str, permission_id: str, answer: str, v2: bool = False
    ) -> None:
        self.replies.append((session_id, permission_id, answer))


def _make_orchestrator(cfg) -> tuple[Orchestrator, FakeUI, FakeClient]:
    orch = object.__new__(Orchestrator)
    ui = FakeUI()
    client = FakeClient()
    orch.cfg = cfg
    orch.logger = logging.getLogger("test.turns")
    orch.ui = ui
    orch.client = client
    orch.session_id = "ses_abc"
    orch._stream_sink = None
    orch._answer_buffer = ""
    orch._speech_active = False
    orch._last_assistant_message_id = None
    return orch, ui, client


def _events() -> list:
    return [
        TurnStarted(session_id="ses_abc", message_id="msg_1"),
        TextDelta(session_id="ses_abc", text="Olá", message_id="msg_1"),
        ReasoningDelta(session_id="ses_abc", text="pensando", message_id="msg_1"),
        TextDelta(session_id="ses_abc", text=", mundo", message_id="msg_1"),
        ToolStarted(session_id="ses_abc", call_id="c1", name="edit", detail="a.py"),
        ToolFinished(
            session_id="ses_abc",
            call_id="c1",
            name="edit",
            status="completed",
            diff_name="a.py",
            diff="--- a/a.py\n+++ b/a.py",
        ),
        PermissionAsked(
            session_id="ses_abc",
            request_id="per_1",
            tool="edit",
            detail="a.py",
            resources=["a.py"],
        ),
        TurnCompleted(session_id="ses_abc", message_id="msg_1", text="Olá, mundo"),
    ]


@pytest.mark.asyncio
async def test_consume_events_tipados(cfg):
    orch, ui, client = _make_orchestrator(cfg)
    queue: asyncio.Queue = asyncio.Queue()
    for event in _events():
        queue.put_nowait(event)

    answer = await orch._consume_events(queue)

    assert answer == "Olá, mundo"
    assert "".join(ui.tokens) == "Olá, mundo"
    assert ui.stream_started and ui.stream_ended
    assert ui.tools == [("editando", "a.py")]
    assert ui.diffs == [("a.py", "--- a/a.py\n+++ b/a.py")]
    assert ui.permissions == [("edit", ["a.py"])]
    assert ui.reasoning_text[-1] == "pensando"
    assert client.replies == [("ses_abc", "per_1", "once")]
    assert orch._last_assistant_message_id == "msg_1"


@pytest.mark.asyncio
async def test_consume_events_falha_do_turno(cfg):
    orch, ui, _client = _make_orchestrator(cfg)
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(TurnFailed(session_id="ses_abc", message="provedor fora do ar"))

    with pytest.raises(AgentError, match="provedor fora do ar"):
        await orch._consume_events(queue)
    assert not ui.stream_started


@pytest.mark.asyncio
async def test_auto_approve_nao_pergunta(cfg):
    cfg.agent.auto_approve = True
    orch, ui, client = _make_orchestrator(cfg)
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(
        PermissionAsked(
            session_id="ses_abc",
            request_id="per_1",
            tool="bash",
            detail="ls",
            resources=["ls"],
        )
    )
    queue.put_nowait(TurnCompleted(session_id="ses_abc", message_id="m", text="ok"))

    assert await orch._consume_events(queue) == "ok"
    assert ui.permissions == []
    assert client.replies == [("ses_abc", "per_1", "once")]
