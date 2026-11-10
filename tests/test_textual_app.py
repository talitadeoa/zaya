from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pytest
from textual.widgets import Input

from zora.controller.modes import PERSONAS
from zora.interface import textual_app
from zora.interface.textual_app import (
    _HELP,
    TextualSink,
    ZaiaTextualApp,
)


class _FakeListener:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.paused = False
        self.stopped = False

    def start(self) -> None:
        pass

    def next_utterance(self, timeout=None):
        time.sleep(0.01)
        return None

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def stop(self) -> None:
        self.stopped = True


class _FakeTTS:
    enabled = True

    def stop(self) -> None:
        pass


class _FakeClient:
    async def abort(self, session_id: str) -> None:
        pass


class _FakeRecorder:
    def record_until_silence(self, on_state=None, wait_timeout=None):
        return None


class _FakeTranscriber:
    def transcribe(self, audio, vad_filter=None) -> str:
        return ""


class _FakeFocus:
    current = None


class _FakeGoals:
    def all(self) -> list:
        return []


class _FakeAgenda:
    async def lunation_note(self, date: str) -> str:
        return ""


class _FakeVoice:
    """Voz mínima para exercitar atalhos sem microfone/rede."""

    def __init__(self) -> None:
        self.cfg = SimpleNamespace(
            audio=SimpleNamespace(
                sample_rate=16000,
                channels=1,
                input_device="",
                silence_ms=5000,
                max_seconds=0.0,
                speech_threshold_db=-40.0,
                min_speech_ms=250.0,
                preroll_ms=300.0,
            ),
            interface=SimpleNamespace(chinese_echo=False),
            opencode=SimpleNamespace(model="x/y"),
        )
        self.tts = _FakeTTS()
        self.client = _FakeClient()
        self.recorder = _FakeRecorder()
        self.transcriber = _FakeTranscriber()
        self.focus = _FakeFocus()
        self.goals = _FakeGoals()
        self.agenda = _FakeAgenda()
        self.session_id = "ses_fake"
        self.mode_key = "assistente"
        self.mode_name = "Zaia"
        self.selected_modes: list[str] = []

    def modes(self) -> list:
        return list(PERSONAS.values())

    def history(self) -> list:
        return []

    def sessions(self) -> list:
        return []

    async def select_mode(self, key: str) -> None:
        self.mode_key = key
        self.selected_modes.append(key)

    async def new_session(self) -> None:
        pass

    async def select_conversation(self, conversation_id: int) -> None:
        pass

    async def speak_text(self, text: str) -> None:
        pass

    async def translate_zh(self, text: str) -> str:
        return ""

    def mark_session_dirty(self) -> None:
        pass


async def _answer(text: str, sink) -> str:
    sink.token(f"eco: {text}")
    return f"eco: {text}"


@pytest.mark.asyncio
async def test_app_textual_responde_sem_erro():
    app = ZaiaTextualApp(_answer)
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
    app = ZaiaTextualApp(_answer)
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

    app = ZaiaTextualApp(broken)
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
    app = ZaiaTextualApp(_answer)
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
    app = ZaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        written: list[str] = []
        app._write_note = lambda text: written.append(text)

        event = SimpleNamespace(value="/ajuda", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)

    assert written == [_HELP]


@pytest.mark.asyncio
async def test_quit_command_exits_app():
    app = ZaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        exits: list[object] = []
        app.exit = lambda code=None: exits.append(code)

        event = SimpleNamespace(value="/sair", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)

    assert exits == [None]


@pytest.mark.asyncio
async def test_plain_text_is_sent_to_answer():
    app = ZaiaTextualApp(_answer)
    received: list[str] = []

    async def fake_submit(text: str) -> None:
        received.append(text)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.submit_text = fake_submit

        event = SimpleNamespace(value="olá zaia", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)
        await asyncio.sleep(0)
        await pilot.pause()

    assert received == ["olá zaia"]


@pytest.mark.asyncio
async def test_voice_toggle_without_voice_reports_unavailable():
    app = ZaiaTextualApp(_answer)
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


def test_set_status_is_safe_before_mount():
    app = ZaiaTextualApp(_answer)
    app._set_status("antes do mount")
    assert app._status_base == "antes do mount"


@pytest.mark.asyncio
async def test_set_status_is_safe_after_unmount():
    app = ZaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._set_status("durante")
        assert app._status_base == "durante"
    app._set_status("depois")
    assert app._status_base == "depois"


@pytest.mark.asyncio
async def test_startup_sem_modal_de_modo():
    app = ZaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1  # nenhum modal na abertura
        assert app._exception is None


@pytest.mark.asyncio
async def test_tab_alterna_o_modo_sem_modal():
    voice = _FakeVoice()
    app = ZaiaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert voice.mode_key == "conversa"
        assert voice.selected_modes == ["conversa"]
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_modebar_mostra_modo_e_atalho():
    app = ZaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        texto = str(app._modebar.render())
        assert "Tab modo" in texto
        assert "Zaia" in texto


@pytest.mark.asyncio
async def test_shortcuts_work_with_voice_and_modals(monkeypatch):
    monkeypatch.setattr(textual_app, "ContinuousListener", _FakeListener)
    app = ZaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()

        keys = [
            "v",
            "f",
            "c",
            "m",
            "t",
            "escape",
            "f1",
            "escape",
            "f2",
            "escape",
            "f3",
            "escape",
            "ctrl+t",
            "ctrl+l",
            "i",
            "escape",
            "/",
            "escape",
        ]
        for key in keys:
            await pilot.press(key)
            await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app._insert_mode is False
        assert app._exception is None


@pytest.mark.asyncio
async def test_palette_escape_closes_without_submitting():
    app = ZaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        submitted: list[str] = []

        async def fake_submit(text: str) -> None:
            submitted.append(text)

        app.submit_text = fake_submit
        await pilot.press("f1")
        await pilot.pause()
        assert len(app.screen_stack) == 2

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert submitted == []


@pytest.mark.asyncio
async def test_push_to_talk_submits_transcribed_text(monkeypatch):
    monkeypatch.setattr(textual_app, "ContinuousListener", _FakeListener)
    voice = _FakeVoice()
    voice.recorder = SimpleNamespace(
        record_until_silence=lambda on_state=None, wait_timeout=None: np.zeros(
            1600, dtype=np.float32
        )
    )
    voice.transcriber = SimpleNamespace(
        transcribe=lambda audio, vad_filter=None: "olá por voz"
    )
    app = ZaiaTextualApp(_answer, voice=voice)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        received: list[str] = []

        async def fake_submit(text: str) -> None:
            received.append(text)

        app.submit_text = fake_submit
        await app._push_to_talk()
        await pilot.pause()

    assert received == ["olá por voz"]
    assert app._exception is None


@pytest.mark.asyncio
async def test_palette_input_does_not_submit_chat():
    app = ZaiaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        received: list[str] = []

        async def fake_submit(text: str) -> None:
            received.append(text)

        app.submit_text = fake_submit
        event = SimpleNamespace(
            value="oi", input=SimpleNamespace(value="", id="palette-input")
        )
        await app.on_input_submitted(event)
        await asyncio.sleep(0)
        await pilot.pause()

    assert received == []


@pytest.mark.asyncio
async def test_troca_de_modo_usa_worker_serializado(monkeypatch):
    app = ZaiaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        captured: dict[str, object] = {}

        def fake_run_worker(work, **kwargs):
            captured.update(kwargs)
            close = getattr(work, "close", None)
            if close is not None:
                close()
            return None

        monkeypatch.setattr(app, "run_worker", fake_run_worker)
        app.action_cycle_mode()
        await pilot.pause()

    assert captured.get("group") == "modo"
    assert captured.get("exclusive") is True
