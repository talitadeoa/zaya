from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.widgets import Input

from zora.interface.textual_app import (
    _HELP,
    TextualSink,
    ZoraTextualApp,
)


async def _answer(text: str, sink) -> str:
    sink.token(f"eco: {text}")
    return f"eco: {text}"


@pytest.mark.asyncio
async def test_app_textual_responde_sem_erro():
    app = ZoraTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("i")
        field = app.query_one("#input", Input)
        field.value = "oi"
        await pilot.press("enter")
        await pilot.pause()
    assert True


@pytest.mark.asyncio
async def test_submit_text_finishes_with_answer():
    app = ZoraTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        captured: dict[str, str] = {}
        app.finish_stream = lambda text: captured.setdefault("text", text)

        await app.submit_text("oi")
        await pilot.pause()

    assert captured["text"] == "eco: oi"
    assert app._turn_running is False


@pytest.mark.asyncio
async def test_submit_text_reports_answer_errors():
    async def broken(text: str, sink) -> str:
        raise RuntimeError("boom")

    app = ZoraTextualApp(broken)
    async with app.run_test() as pilot:
        await pilot.pause()
        captured: dict[str, str] = {}
        app.finish_stream = lambda text: captured.setdefault("text", text)

        await app.submit_text("oi")
        await pilot.pause()

    assert captured["text"] == "Erro: boom"
    assert app._turn_running is False


@pytest.mark.asyncio
async def test_submit_text_ignores_second_turn_while_running():
    app = ZoraTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        calls: list[str] = []
        app.finish_stream = lambda text: calls.append(text)
        app._turn_running = True

        await app.submit_text("ignorado")
        await pilot.pause()

        assert calls == []
        assert app._turn_running is True
        assert app._status_base.startswith("aguarde")


@pytest.mark.asyncio
async def test_help_command_writes_help_note():
    app = ZoraTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        written: list[str] = []
        app._write_note = lambda text: written.append(text)

        event = SimpleNamespace(value="/ajuda", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)

    assert written == [_HELP]


@pytest.mark.asyncio
async def test_quit_command_exits_app():
    app = ZoraTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        exits: list[object] = []
        app.exit = lambda code=None: exits.append(code)

        event = SimpleNamespace(value="/sair", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)

    assert exits == [None]


@pytest.mark.asyncio
async def test_plain_text_is_sent_to_answer():
    app = ZoraTextualApp(_answer)
    received: list[str] = []

    async def fake_submit(text: str) -> None:
        received.append(text)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.submit_text = fake_submit

        event = SimpleNamespace(value="olá zora", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)
        await asyncio.sleep(0)
        await pilot.pause()

    assert received == ["olá zora"]


@pytest.mark.asyncio
async def test_voice_toggle_without_voice_reports_unavailable():
    app = ZoraTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.action_toggle_voice()

    assert app._status_base.startswith("voz indisponível")


def test_sink_buffers_and_streams():
    updates: list[str] = []
    statuses: list[str] = []
    fake = SimpleNamespace(
        update_stream=updates.append,
        _set_status=statuses.append,
        write_diff=lambda *a: None,
    )
    sink = TextualSink(fake)

    sink.token("olá ")
    sink.token("mundo")
    sink.tool("read", "arquivo")

    assert sink.buffer == "olá mundo"
    assert updates == ["olá "]
    assert statuses and statuses[-1].startswith("▸ read")
