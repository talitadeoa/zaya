"""Testa o consumidor de eventos do turno com o stream real do OpenCode."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from zora.controller.orchestrator import Orchestrator
from zora.opencode.client import OpenCodeError
from zora.opencode.events import iter_sse_json

FIXTURES = Path(__file__).parent / "fixtures" / "opencode"


class FakeUI:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.tools: list[tuple[str, str]] = []
        self.diffs: list[tuple[str, str]] = []
        self.stream_started = False
        self.stream_ended = False
        self.warnings: list[str] = []
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
        pass

    def warning(self, text: str) -> None:
        self.warnings.append(text)

    def permission(self, tool: str, patterns: list[str]) -> str:
        self.permissions.append((tool, patterns))
        return "once"


class FakeClient:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str, str, bool]] = []
        self.aborted: list[str] = []

    async def session_status(self, session_id: str):
        return {"type": "idle"}

    async def abort(self, session_id: str) -> None:
        self.aborted.append(session_id)

    async def reply_permission(
        self, session_id: str, permission_id: str, answer: str, v2: bool = False
    ) -> None:
        self.replies.append((session_id, permission_id, answer, v2))


async def _fixture_events(name: str = "stream.sse.txt") -> list[dict]:
    path = FIXTURES / name

    async def lines():
        for line in path.read_text(encoding="utf-8").splitlines():
            yield line

    return [event async for event in iter_sse_json(lines())]


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


@pytest.mark.asyncio
async def test_consume_events_from_fixture(cfg):
    orch, ui, client = _make_orchestrator(cfg)
    queue: asyncio.Queue[dict] = asyncio.Queue()
    for event in await _fixture_events():
        queue.put_nowait(event)

    answer = await orch._consume_events(queue)

    assert answer == "Olá, mundo"
    assert "".join(ui.tokens) == "Olá, mundo"
    assert ui.stream_started and ui.stream_ended
    assert ui.tools == [("editando", "a.py")]
    assert len(ui.diffs) == 1
    assert ui.diffs[0][0] == "a.py" and "-x" in ui.diffs[0][1]
    assert ui.permissions == [("edit", ["a.py"])]
    assert client.replies == [("ses_abc", "per_1", "once", False)]
    assert orch._last_assistant_message_id == "msg_1"


@pytest.mark.asyncio
async def test_consume_events_raises_on_session_error(cfg):
    orch, _ui, _client = _make_orchestrator(cfg)
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait(
        {"type": "session.error", "properties": {"error": {"name": "APIError"}}}
    )

    with pytest.raises(OpenCodeError):
        await orch._consume_events(queue)


@pytest.mark.asyncio
async def test_consume_events_from_v2_fixture(cfg):
    orch, ui, client = _make_orchestrator(cfg)
    queue: asyncio.Queue[dict] = asyncio.Queue()
    for event in await _fixture_events("stream.v2.sse.txt"):
        queue.put_nowait(event)

    answer = await orch._consume_events(queue)

    assert answer == "Olá, mundo"
    assert "".join(ui.tokens) == "Olá, mundo"
    assert ui.stream_started and ui.stream_ended
    assert ui.tools == [("editando", "a.py")]
    assert len(ui.diffs) == 1
    assert ui.diffs[0][0] == "a.py" and "-x" in ui.diffs[0][1]
    assert ui.permissions == [("edit", ["a.py"])]
    assert client.replies == [("ses_abc", "per_1", "once", True)]
    assert orch._last_assistant_message_id == "msg_2"


@pytest.mark.asyncio
async def test_consume_events_v2_execution_failed(cfg):
    orch, _ui, _client = _make_orchestrator(cfg)
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "session.execution.failed",
            "data": {
                "sessionID": "ses_abc",
                "error": {"type": "provider.no-route", "message": "Model unavailable"},
            },
        }
    )

    with pytest.raises(OpenCodeError, match=r"provider\.no-route"):
        await orch._consume_events(queue)
