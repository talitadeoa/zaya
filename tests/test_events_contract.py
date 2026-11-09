"""Testes de contrato sobre payloads realistas do OpenCode.

As fixtures em tests/fixtures/opencode/ imitam o formato enviado pelo servidor
(SSE de eventos e payload {info, parts}). Se o formato mudar, estes testes
quebram antes de a integracao silenciosamente parar de funcionar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zora.opencode.events import (
    assistant_text,
    event_session_id,
    event_type,
    iter_sse_json,
    message_error,
    session_status_is_idle,
    text_delta,
    tool_activity,
    tool_diff,
)

FIXTURES = Path(__file__).parent / "fixtures" / "opencode"


async def _lines(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        yield line


async def _parse_stream() -> list[dict]:
    return [event async for event in iter_sse_json(_lines(FIXTURES / "stream.sse.txt"))]


@pytest.mark.asyncio
async def test_sse_fixture_parses_expected_events():
    events = await _parse_stream()
    types = [event_type(event) for event in events]

    assert types == [
        "server.connected",
        "session.updated",
        "message.part.updated",
        "message.part.updated",
        "message.part.updated",
        "message.part.updated",
        "permission.asked",
        "session.idle",
    ]
    # todos os eventos de sessao apontam para a mesma sessao
    session_ids = {event_session_id(event) for event in events}
    assert "ses_abc" in session_ids


@pytest.mark.asyncio
async def test_text_parts_reconstruct_full_message():
    events = await _parse_stream()
    parts = [
        event["properties"]["part"]
        for event in events
        if event_type(event) == "message.part.updated"
        and event["properties"]["part"]["type"] == "text"
    ]

    previous = ""
    collected = ""
    for part in parts:
        delta, previous = text_delta(part, previous)
        collected += delta
    assert collected == "Olá, mundo"


@pytest.mark.asyncio
async def test_tool_lifecycle_from_fixture():
    events = await _parse_stream()
    tool_parts = [
        event["properties"]["part"]
        for event in events
        if event_type(event) == "message.part.updated"
        and event["properties"]["part"]["type"] == "tool"
    ]

    running = tool_activity(tool_parts[0])
    assert running == ("call_1", "edit", "a.py")

    completed = tool_activity(tool_parts[1])
    assert completed == ("call_1", "edit", "a.py")

    diff = tool_diff(tool_parts[1])
    assert diff is not None
    filename, patch = diff
    assert filename == "a.py"
    assert "-x" in patch and "+y" in patch


@pytest.mark.asyncio
async def test_permission_and_idle_present():
    events = await _parse_stream()
    permission = next(e for e in events if event_type(e) == "permission.asked")
    assert permission["properties"]["id"] == "per_1"
    assert permission["properties"]["permission"] == "edit"
    assert event_type(events[-1]) == "session.idle"


def test_messages_fixture_contract():
    messages = json.loads((FIXTURES / "messages.json").read_text(encoding="utf-8"))

    assistant = messages[1]
    assert assistant_text(assistant) == "Pronto.\nArquivo revisado."
    assert assistant_text(messages[0]) == "revise o arquivo"

    assert message_error(messages[0]) is None
    assert message_error(messages[2]) == "APIError: rate limit"


@pytest.mark.asyncio
async def test_sse_ignores_invalid_json_and_handles_eof_without_blank_line():
    async def lines():
        yield "data: {nao-e-json}"
        yield ""
        yield 'data: {"type":"session.idle"}'

    events = [event async for event in iter_sse_json(lines())]
    assert events == [{"type": "session.idle"}]


@pytest.mark.asyncio
async def test_sse_handles_crlf():
    async def lines():
        yield 'data: {"type":"server.connected"}\r'
        yield "\r"

    events = [event async for event in iter_sse_json(lines())]
    assert events == [{"type": "server.connected"}]


def test_session_status_idle_variants():
    assert session_status_is_idle({"type": "idle"}) is True
    assert session_status_is_idle({"type": "busy"}) is False
    assert session_status_is_idle("idle") is True
    assert session_status_is_idle(None) is False


def test_text_delta_shrinking_text_keeps_previous():
    delta, current = text_delta({"text": "Olá"}, "Olá mundo")
    assert delta == ""
    assert current == "Olá mundo"
