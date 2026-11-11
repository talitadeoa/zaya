from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pytest
from textual.widgets import Input, OptionList

from zaya.controller.modes import PERSONAS
from zaya.interface import textual_app
from zaya.interface.textual_app import (
    _HELP,
    CollapsibleCard,
    TextualSink,
    ZayaTextualApp,
)
from zaya.interface.textual_screens import CommandPalette, ModePicker, SessionPicker


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
            agent=SimpleNamespace(model_slug=lambda: "x/y"),
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
        self.mode_name = "Zaya"
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
    app = ZayaTextualApp(_answer)
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
    app = ZayaTextualApp(_answer)
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

    app = ZayaTextualApp(broken)
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
    app = ZayaTextualApp(_answer)
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
    app = ZayaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        written: list[str] = []
        app._write_note = lambda text: written.append(text)

        event = SimpleNamespace(value="/ajuda", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)

    assert written == [_HELP]


@pytest.mark.asyncio
async def test_quit_command_exits_app():
    app = ZayaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        exits: list[object] = []
        app.exit = lambda code=None: exits.append(code)

        event = SimpleNamespace(value="/sair", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)

    assert exits == [None]


@pytest.mark.asyncio
async def test_plain_text_is_sent_to_answer():
    app = ZayaTextualApp(_answer)
    received: list[str] = []

    async def fake_submit(text: str) -> None:
        received.append(text)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.submit_text = fake_submit

        event = SimpleNamespace(value="olá zaya", input=SimpleNamespace(value=""))
        await app.on_input_submitted(event)
        await asyncio.sleep(0)
        await pilot.pause()

    assert received == ["olá zaya"]


@pytest.mark.asyncio
async def test_voice_toggle_without_voice_reports_unavailable():
    app = ZayaTextualApp(_answer)
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
    app = ZayaTextualApp(_answer)
    app._set_status("antes do mount")
    assert app._status_base == "antes do mount"


@pytest.mark.asyncio
async def test_set_status_is_safe_after_unmount():
    app = ZayaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._set_status("durante")
        assert app._status_base == "durante"
    app._set_status("depois")
    assert app._status_base == "depois"


@pytest.mark.asyncio
async def test_startup_sem_modal_de_modo():
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1  # nenhum modal na abertura
        assert app._exception is None


@pytest.mark.asyncio
async def test_tab_abre_modal_de_modo_sem_trocar():
    voice = _FakeVoice()
    app = ZayaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)
        assert voice.mode_key == "assistente"
        assert voice.selected_modes == []
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert voice.mode_key == "assistente"


@pytest.mark.asyncio
async def test_modebar_mostra_modo_e_sessoes_clicavel():
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        texto = app._modebar.renderable
        assert "Zaya" in str(texto)
        assert "Tab modo" not in str(texto)
        assert "Sessões" not in str(texto)
        assert "Sessões" in str(app.query_one("#session-button").renderable)

        await pilot.click("#session-button")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)


@pytest.mark.asyncio
async def test_shortcuts_work_with_voice_and_modals(monkeypatch):
    monkeypatch.setattr(textual_app, "ContinuousListener", _FakeListener)
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
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
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
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
async def test_paleta_busca_e_enter_abre_o_comando():
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)

        field = app.screen.query_one("#palette-input", Input)
        field.value = "modo"
        await pilot.pause()
        options = app.screen.query_one("#palette-list", OptionList)
        assert options.option_count == 1

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)


@pytest.mark.asyncio
async def test_paleta_setas_escolhem_comando():
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        options = app.screen.query_one("#palette-list", OptionList)
        assert options.highlighted == 0

        await pilot.press("down")
        await pilot.pause()
        assert options.highlighted == 1

        await pilot.press("up")
        await pilot.pause()
        assert options.highlighted == 0


@pytest.mark.asyncio
async def test_push_to_talk_submits_transcribed_text(monkeypatch):
    monkeypatch.setattr(textual_app, "ContinuousListener", _FakeListener)
    voice = _FakeVoice()
    voice.recorder = SimpleNamespace(
        record_until_silence=lambda on_state=None, wait_timeout=None: np.zeros(
            1600, dtype=np.float32
        )
    )
    voice.transcriber = SimpleNamespace(transcribe=lambda audio, vad_filter=None: "olá por voz")
    app = ZayaTextualApp(_answer, voice=voice)

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
    app = ZayaTextualApp(_answer)
    async with app.run_test() as pilot:
        await pilot.pause()
        received: list[str] = []

        async def fake_submit(text: str) -> None:
            received.append(text)

        app.submit_text = fake_submit
        event = SimpleNamespace(value="oi", input=SimpleNamespace(value="", id="palette-input"))
        await app.on_input_submitted(event)
        await asyncio.sleep(0)
        await pilot.pause()

    assert received == []


@pytest.mark.asyncio
async def test_troca_de_modo_usa_worker_serializado(monkeypatch):
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
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
        app._switch_mode("investidora")
        await pilot.pause()

    assert captured.get("group") == "modo"
    assert captured.get("exclusive") is True


@pytest.mark.asyncio
async def test_menu_button_abre_paleta_de_atalhos():
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#menu-button")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


@pytest.mark.asyncio
async def test_tab_abre_modal_mesmo_digitando():
    voice = _FakeVoice()
    app = ZayaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)
        assert voice.mode_key == "assistente"
        assert voice.selected_modes == []


@pytest.mark.asyncio
async def test_click_modebar_abre_modal_de_modo():
    voice = _FakeVoice()
    app = ZayaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#modebar")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)
        assert voice.mode_key == "assistente"
        assert voice.selected_modes == []


@pytest.mark.asyncio
async def test_clicar_painel_recolhe_e_expande():
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        painel = app.query_one(".dashboard-panel", CollapsibleCard)
        assert painel.expanded is True

        await pilot.click(".dashboard-panel")
        await pilot.pause()
        assert painel.expanded is False

        await pilot.click(".dashboard-panel")
        await pilot.pause()
        assert painel.expanded is True


@pytest.mark.asyncio
async def test_f5_mostra_e_oculta_lua_e_metas():
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        paineis = list(app.query(".dashboard-panel"))
        assert paineis
        assert all(widget.display for widget in paineis)

        await pilot.press("f5")
        await pilot.pause()
        assert not any(widget.display for widget in paineis)
        assert "ocultas" in app._status_base

        await pilot.press("f5")
        await pilot.pause()
        assert all(widget.display for widget in paineis)


@pytest.mark.asyncio
async def test_f4_alterna_mouse_para_selecionar_texto():
    app = ZayaTextualApp(_answer, voice=_FakeVoice())
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        original = app._driver
        calls = {"disable": 0, "enable": 0}

        class _FakeDriver:
            def _disable_mouse_support(self) -> None:
                calls["disable"] += 1

            def _enable_mouse_support(self) -> None:
                calls["enable"] += 1

        app._driver = _FakeDriver()
        try:
            app.action_toggle_mouse()
            assert calls["disable"] == 1
            assert app._mouse_enabled is False
            assert "seleção ligada" in app._status_base

            app.action_toggle_mouse()
            assert calls["enable"] == 1
            assert app._mouse_enabled is True
        finally:
            app._driver = original
        await pilot.pause()


@pytest.mark.asyncio
async def test_modal_de_modo_aplica_modo_escolhido():
    voice = _FakeVoice()
    app = ZayaTextualApp(_answer, voice=voice)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.screen, ModePicker)
        await pilot.press("up")
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert voice.mode_key == "investidora"
        assert voice.selected_modes == ["investidora"]
