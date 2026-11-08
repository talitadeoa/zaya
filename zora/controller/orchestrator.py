"""Orquestrador da Zora: voz -> STT -> contexto -> OpenCode -> resposta/voz."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import re
import select
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from zora.agenda.client import AgendaClient, AgendaError
from zora.audio.recorder import AudioError, ContinuousListener, Recorder
from zora.config import Config
from zora.controller.goals import GoalTracker, goal_block
from zora.finance.quotes import FinanceError, fetch_quote, format_quote
from zora.finance.watchlist import Watchlist
from zora.focus import (
    RITUALS,
    FocusStore,
    moon_details,
    period_label,
    principal_phase_today,
)
from zora.controller.intents import (
    RegisterIntent,
    format_context,
    needs_second_brain_context,
    parse_preference,
    parse_register,
)
from zora.controller.modes import (
    PERSONAS,
    get_persona,
    parse_mode_switch,
    persona_block,
    resolve_mode,
)
from zora.controller.proactive import (
    ReminderSpec,
    classify_voice_control,
    greeting_for,
    in_quiet_hours,
    parse_reminder,
)
from zora.interface.markdown_text import strip_markdown
from zora.interface.terminal import TerminalUI
from zora.memory.store import MemoryStore
from zora.opencode.client import OpenCodeClient, OpenCodeError
from zora.opencode.events import (
    assistant_text,
    event_session_id,
    event_type,
    message_error,
    text_delta,
    tool_activity,
    tool_diff,
)
from zora.secondbrain.index import SecondBrainIndex
from zora.secondbrain.notes import NoteConflict, SecondBrain
from zora.spotify.controller import SpotifyCommand, SpotifyError, execute as execute_spotify
from zora.spotify.intents import parse_spotify_command
from zora.stt.faster_whisper import STTError, WhisperTranscriber
from zora.tasks.store import Task, TaskStore
from zora.tts.base import TTSBackend
from zora.tts.manager import TTSManager, clean_for_speech


class TurnInterrupted(Exception):
    """Interrupcao solicitada pelo usuario durante um turno."""


TOOL_LABELS = {
    "read": "lendo",
    "edit": "editando",
    "write": "escrevendo",
    "bash": "executando",
    "grep": "buscando",
    "glob": "listando",
    "webfetch": "consultando web",
    "task": "delegando",
    "todowrite": "planejando",
}

# Fim de frase para falar o texto conforme ele chega (pontuação + espaço/fim).
_SENTENCE_END = re.compile(r"[.!?\n](\s+|$)")




class Orchestrator:
    def __init__(
        self,
        cfg: Config,
        logger: logging.Logger,
        ui: TerminalUI,
        store: MemoryStore,
        index: SecondBrainIndex,
        brain: SecondBrain,
        tts: TTSManager,
        transcriber: WhisperTranscriber,
        recorder: Recorder,
        client: OpenCodeClient,
    ):
        self.cfg = cfg
        self.logger = logger
        self.ui = ui
        self.store = store
        self.index = index
        self.brain = brain
        self.tts = tts
        self.transcriber = transcriber
        self.recorder = recorder
        self.client = client
        self.directory = cfg.project_directory()
        self.persona = resolve_mode(
            store.get_meta("mode") or cfg.interface.default_mode
        )

        self.conversation_id: int | None = None
        self.session_id: str = ""
        self._voice_ok: bool | None = None
        self._voice_msg = ""
        self._prefs_cache = ""
        self._prefs_mtime: float = -1.0
        self._last_activity = time.monotonic()
        self._busy = False
        self._recording = False
        self._listening = False
        self._voice_mode = bool(cfg.audio.continuous)
        self._pending_input = ""
        self._stream_sink = None
        self._mic_muted = False
        self._continuous_hint_shown = False
        self._permission_active = False
        self._last_listen_at = time.monotonic()
        self._listener: ContinuousListener | None = None
        self._speaking_event = asyncio.Event()
        self._speaking_event.set()
        self._stt_lock = asyncio.Lock()
        self._partial_last = ""
        self._proactive_task: asyncio.Task | None = None
        self._proactive_count = 0
        self._proactive_day = datetime.now().astimezone().date()
        self._last_proactive = 0.0
        self._last_idle_message = ""
        self._initiative_session_id: str | None = None
        self._task_session_id: str | None = None
        self._tasks_next_check = 0.0
        self._task_page_updated_at: str | None = None
        self._agenda_pending_names: list[str] = []
        self.goals = GoalTracker()
        self.watchlist = Watchlist()
        self.focus = FocusStore(cfg.focus.path)
        self.tasks = TaskStore()
        self.agenda = AgendaClient(cfg.agenda)
        self._agenda_next_check = 0.0
        self._agenda_seen: set[str] = set()
        self._agenda_day = None
        self._tts_lock = asyncio.Lock()
        self._user_name_cache: str | None = None
        self._warmup_task: asyncio.Task | None = None
        self._speech_active = False
        self._speech_buffer = ""
        self._speech_pending = ""
        self._speech_queue: asyncio.Queue[str | None] | None = None
        self._speech_task: asyncio.Task | None = None
        self._speech_fed_chars = 0
        self._answer_buffer = ""

    # ------------------------------------------------------------------- start
    async def _bootstrap(self) -> None:
        await self.client.ensure_server()
        if self.cfg.opencode.auto_approve:
            self.ui.warning(
                "auto-aprovação de ferramentas do OpenCode está ATIVA (opencode.auto_approve=true)"
            )
        self._prepare_conversation()
        self.session_id = await self._ensure_session()
        await self._clear_stale_session()
        self._refresh_index_on_start()
        if self._voice_available():
            self._warmup_task = asyncio.create_task(self._warmup_stt())

    async def _warmup_stt(self) -> None:
        try:
            await asyncio.to_thread(self.transcriber.warmup)
        except Exception as exc:  # pragma: no cover
            self.logger.debug("warmup do STT falhou: %s", exc)

    async def _shutdown(self) -> None:
        if self._warmup_task is not None:
            self._warmup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._warmup_task
            self._warmup_task = None
        await self._stop_speech_streaming()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._proactive_task is not None:
            self._proactive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._proactive_task
            self._proactive_task = None
        self.tts.stop()
        self.ui.close()
        await self.client.aclose()

    async def run_once(self, text: str) -> None:
        """Executa uma unica instrucao (util para `zora ask`)."""
        await self._bootstrap()
        try:
            raw = text.strip()
            if not raw:
                return
            if raw.startswith("/"):
                await self._handle_command(raw)
                return
            assert self.conversation_id is not None
            self.store.add_turn(self.conversation_id, "user", raw)
            await self._dispatch(raw)
        finally:
            await self._shutdown()

    async def answer_once(
        self, text: str, speak: bool = False, close: bool = True
    ) -> str:
        """Responde uma instrução externa (ex.: Telegram) e devolve o texto."""
        if not self.session_id:
            await self._bootstrap()
        try:
            raw = text.strip()
            if not raw:
                return ""
            if raw.startswith("/"):
                await self._handle_command(raw)
                return ""
            assert self.conversation_id is not None
            self.store.add_turn(self.conversation_id, "user", raw)
            return await self._dispatch(raw) if speak else await self._dispatch_silent(raw)
        finally:
            if close:
                await self._shutdown()

    async def answer_stream(self, text: str, sink) -> str:
        """Como answer_once, mas envia tokens/eventos para um sink de UI."""
        self._stream_sink = sink
        try:
            return await self.answer_once(text, speak=False, close=False)
        finally:
            self._stream_sink = None

    async def speak_text(self, text: str) -> None:
        await self._speak(text)

    async def aclose(self) -> None:
        await self._shutdown()

    async def _dispatch_silent(self, text: str) -> str:
        mode = parse_mode_switch(text)
        if mode:
            self._switch_mode(mode)
            return f"Modo {self.persona.name} ativado."
        spotify = parse_spotify_command(text)
        if spotify is not None:
            return await self._handle_spotify(spotify, speak=False)
        preference = parse_preference(text)
        if preference is not None:
            await self._handle_preference(preference)
            return ""
        reminder = parse_reminder(text)
        if reminder is not None:
            await self._handle_reminder(reminder)
            return ""
        intent = parse_register(text)
        if intent is not None:
            await self._handle_register(intent, original=text)
            return ""
        return await self._handle_task_impl(text, speak=False)

    async def start(self) -> None:
        await self._bootstrap()

        if self.cfg.interface.show_banner:
            self.ui.banner(
                project=self.directory,
                session_id=self.session_id,
                model=self.cfg.opencode.model,
                sb_notes=self.index.count(),
                voice=self.tts.provider,
                tts_enabled=self.tts.enabled,
            )
        else:
            self.ui.status(
                self.directory,
                self.session_id,
                self.cfg.opencode.model,
                self.index.count(),
                self.tts.provider,
            )

        if self.cfg.interface.show_metas_on_start:
            try:
                await self._show_dashboard()
            except Exception as exc:  # pragma: no cover
                self.logger.warning("painel de metas: %s", exc)

        if self.cfg.proactive.enabled and self.cfg.proactive.startup_greeting:
            try:
                await self._startup_greeting()
            except Exception as exc:  # pragma: no cover
                self.logger.warning("falha na saudação inicial: %s", exc)

        if self.cfg.proactive.enabled:
            self._proactive_task = asyncio.create_task(self._proactive_loop(greet=False))

        try:
            await self._check_focus(datetime.now().astimezone())
        except Exception as exc:  # pragma: no cover
            self.logger.warning("falha ao checar foco: %s", exc)

        if not (self.cfg.audio.continuous and self._voice_available()):
            self.ui.hint(
                "Digite para texto · Enter vazio ou /fala volta à voz · "
                "/ajuda · Ctrl+C interrompe"
            )

        try:
            await self._loop()
        finally:
            await self._shutdown()

    def _prepare_conversation(self) -> None:
        conv = self.store.get_or_create_conversation(str(self.directory))
        self.conversation_id = int(conv["id"])

    def _refresh_index_on_start(self) -> None:
        if not self.cfg.secondbrain.path:
            return
        if not self.brain.root.exists():
            self.ui.warning(
                f"Second Brain não encontrado em {self.brain.root} (use /indexar após configurar)"
            )
            return
        if not (self.cfg.secondbrain.reindex_on_start or self.index.count() == 0):
            return
        try:
            stats = self.index.reindex()
            if stats["indexed"] or stats["removed"]:
                self.ui.info(
                    f"Second Brain indexado: {stats['indexed']} nova(s), "
                    f"{stats['removed']} removida(s), {stats['total']} no total"
                )
        except Exception as exc:  # pragma: no cover - depende do FS
            self.logger.warning("falha ao indexar Second Brain: %s", exc)
            self.ui.warning(f"não foi possível indexar o Second Brain: {exc}")

    async def _ensure_session(self) -> str:
        assert self.conversation_id is not None
        conv = self.store.get_conversation(self.conversation_id)
        sid = conv["opencode_session_id"]
        if self.cfg.opencode.resume_session and sid:
            existing = await self.client.get_session(sid)
            if existing:
                return sid
        session = await self.client.create_session(
            title=f"Zora — {self.directory.name}",
            model=self.client.model,
        )
        self.store.set_session(self.conversation_id, session.id)
        return session.id

    async def _clear_stale_session(self) -> None:
        """Aborta uma execucao presa (sessao em busy) antes de iniciar outra."""
        if not self.session_id:
            return
        try:
            status = await self.client.session_status(self.session_id)
        except OpenCodeError:
            return
        if status and isinstance(status, dict) and status.get("type") != "idle":
            self.logger.warning("sessao presa (%s); abortando antes do proximo turno", status)
            self.ui.warning("liberando uma execução anterior presa…")
            await self.client.abort(self.session_id)
            await asyncio.sleep(0.2)

    # -------------------------------------------------------------------- loop
    async def _loop(self) -> None:
        while True:
            if self._voice_mode and self._voice_available():
                quit_app = await self._continuous_loop()
                if quit_app:
                    return
                continue

            try:
                text = await self._read_user_input()
            except EOFError:
                self.ui.info("até logo.")
                return
            except KeyboardInterrupt:
                self.ui.info("")
                continue

            if text is None:
                continue
            self._mark_activity()
            text = text.strip()
            if not text:
                if self._voice_available():
                    self._voice_mode = True
                    self.cfg.audio.continuous = True
                continue

            self._voice_mode = False
            self.cfg.audio.continuous = False

            width = max(20, self.ui.console.width)
            lines = max(1, (len("Talita: ") + len(text) + width - 1) // width)
            self.ui.erase_input_line(lines)
            if text.startswith("/"):
                if await self._handle_command(text):
                    return
                continue

            self.ui.print_user(text)
            assert self.conversation_id is not None
            self.store.add_turn(self.conversation_id, "user", text)

            try:
                await self._dispatch(text)
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.tts.stop()
                self.ui.warning("interrompido")
                continue

    async def _dispatch(self, text: str) -> str:
        spotify = parse_spotify_command(text)
        if spotify is not None:
            return await self._handle_spotify(spotify, speak=True)
        preference = parse_preference(text)
        if preference is not None:
            await self._handle_preference(preference)
            return ""
        reminder = parse_reminder(text)
        if reminder is not None:
            await self._handle_reminder(reminder)
            return ""
        intent = parse_register(text)
        if intent is not None:
            await self._handle_register(intent, original=text)
            return ""
        return await self._handle_task(text)

    async def _handle_spotify(self, command: SpotifyCommand, speak: bool) -> str:
        try:
            message = await asyncio.to_thread(execute_spotify, command)
        except SpotifyError as exc:
            message = f"Spotify: {exc}"
        self.ui.info(message)
        if speak:
            await self._speak(message)
        return message

    async def _continuous_loop(self) -> bool:
        """Escuta continua real: microfone aberto + segmentacao por VAD/Enter."""
        listener = ContinuousListener(self.cfg.audio)
        try:
            listener.start()
        except AudioError as exc:
            self.ui.error(f"microfone: {exc}")
            self.cfg.audio.continuous = False
            return False

        self._listener = listener
        self._listening = True
        self._voice_mode = True
        self._mic_muted = False
        self._last_listen_at = time.monotonic()
        self._mark_activity()
        monitor_stop = threading.Event()
        monitor = threading.Thread(
            target=self._enter_monitor,
            args=(listener, monitor_stop),
            daemon=True,
        )
        monitor.start()
        if not self._continuous_hint_shown:
            self.ui.hint(
                "Escuta contínua: fale à vontade · [M] mudo · [Esc] interrompe · "
                "[T] ou digitar volta ao texto · o mic desliga no silêncio · "
                "'sair' encerra"
            )
            self._continuous_hint_shown = True
        self.ui.set_status("● ouvindo…")
        partial_task: asyncio.Task | None = None
        last_partial_at = 0.0
        try:
            while self.cfg.audio.continuous:
                now = datetime.now().astimezone()
                if self.cfg.proactive.reminders and self.store.due_reminders(now.isoformat()):
                    listener.pause()
                    try:
                        await self._fire_due_reminders()
                    finally:
                        if not self._mic_muted:
                            listener.resume()

                try:
                    utterance = await asyncio.to_thread(listener.next_utterance, 0.5)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    self._voice_mode = False
                    self.cfg.audio.continuous = False
                    self.ui.clear_status()
                    self.ui.warning("escuta contínua pausada; use /continuo on para retomar")
                    return False

                if utterance is None:
                    self._update_idle_status(listener)
                    self._maybe_auto_mute(listener)
                    if (
                        self.cfg.stt.partial_transcription
                        and listener.is_capturing()
                        and (partial_task is None or partial_task.done())
                        and time.monotonic() - last_partial_at
                        >= self.cfg.stt.partial_interval_seconds
                    ):
                        snapshot = listener.snapshot(self.cfg.stt.partial_window_seconds)
                        minimum = int(self.cfg.audio.sample_rate * 0.7)
                        if snapshot is not None and len(snapshot) >= minimum:
                            last_partial_at = time.monotonic()
                            partial_task = asyncio.create_task(self._partial_transcribe(snapshot))
                    continue

                if partial_task is not None and not partial_task.done():
                    with contextlib.suppress(Exception):
                        await partial_task
                partial_task = None
                self._partial_last = ""
                self._last_listen_at = time.monotonic()

                listener.pause()
                self._recording = True
                try:
                    text = await self._transcribe_audio(utterance)
                    if not text:
                        continue
                    control = classify_voice_control(text)
                    if control == "stop":
                        self._voice_mode = False
                        self.cfg.audio.continuous = False
                        self.ui.info("modo contínuo encerrado; pode digitar.")
                        return False
                    if control == "quit":
                        self.ui.info("até logo.")
                        return True
                    self._mark_activity()
                    assert self.conversation_id is not None
                    self.store.add_turn(self.conversation_id, "user", text)
                    try:
                        await self._dispatch(text)
                    except (KeyboardInterrupt, asyncio.CancelledError):
                        self.tts.stop()
                        self.ui.warning("interrompido")
                finally:
                    self._recording = False
                    if not self._mic_muted:
                        listener.resume()
                        self._last_listen_at = time.monotonic()
        finally:
            self.ui.clear_status()
            if partial_task is not None and not partial_task.done():
                with contextlib.suppress(Exception):
                    await partial_task
            monitor_stop.set()
            monitor.join(timeout=0.6)
            self._listening = False
            self._mic_muted = False
            listener.stop()
            self._listener = None
        return False

    def _enter_monitor(
        self,
        listener: ContinuousListener,
        stop: threading.Event,
    ) -> None:
        """Le teclas no terminal: Enter controla a fala e M pausa/retoma o mic."""
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError):
            return
        use_raw = sys.stdin.isatty()
        saved: list | None = None
        raw_active = False

        def enable_raw() -> None:
            nonlocal saved, raw_active
            if raw_active or not use_raw:
                return
            try:
                import termios
                import tty

                saved = termios.tcgetattr(fd)
                tty.setcbreak(fd)
                raw_active = True
            except Exception:
                saved = None
                raw_active = False

        def disable_raw() -> None:
            nonlocal saved, raw_active
            if not raw_active or saved is None:
                return
            try:
                import termios

                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            except Exception:
                pass
            saved = None
            raw_active = False

        try:
            while not stop.is_set():
                speaking = not self._speaking_event.is_set()
                if (
                    self._recording
                    or self._permission_active
                    or (self._busy and not speaking)
                    or (listener.is_paused() and not self._mic_muted and not speaking)
                ):
                    # Durante um turno o stdin pode estar em uso por um prompt
                    # de permissao: devolve o terminal ao modo normal.
                    disable_raw()
                    time.sleep(0.05)
                    continue
                enable_raw()
                try:
                    ready, _, _ = select.select([fd], [], [], 0.1)
                except (OSError, ValueError):
                    return
                if not ready:
                    continue
                try:
                    data = os.read(fd, 1)
                except (OSError, ValueError):
                    return
                if not data:
                    return
                char = data.decode("utf-8", "ignore")
                if char == "\x1b":
                    try:
                        more, _, _ = select.select([fd], [], [], 0.01)
                    except (OSError, ValueError):
                        more = []
                    if more:
                        # sequencia de seta ou tecla especial: ignora
                        try:
                            os.read(fd, 8)
                        except (OSError, ValueError):
                            pass
                        continue
                    self._interrupt_current()
                    continue
                if char in {"m", "M"}:
                    self._toggle_mute(listener)
                    continue
                if char in {"t", "T"}:
                    self.cfg.audio.continuous = False
                    self.ui.info("voltando ao modo texto; pode digitar.")
                    return
                if char in {"\r", "\n"}:
                    continue
                if char.strip():
                    self.tts.stop()
                    self._voice_mode = False
                    self.cfg.audio.continuous = False
                    self._pending_input = char
                    return
        finally:
            disable_raw()

    def _toggle_mute(self, listener: ContinuousListener) -> None:
        if self._mic_muted:
            self._mic_muted = False
            listener.resume()
            self._last_listen_at = time.monotonic()
            self.ui.success("escuta retomada")
            self.ui.set_status("● ouvindo…")
            return
        self._mic_muted = True
        listener.pause()
        self.ui.success("escuta pausada")
        self.ui.set_status("⏸ escuta pausada", "pressione M para voltar a ouvir")

    def _interrupt_current(self) -> None:
        """Esc: interrompe a fala e a execucao atual, voltando a ouvir."""
        if not self._busy:
            self.tts.stop()
            return
        self.ui.info("interrompendo…")
        self.tts.stop()
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except OSError:
            pass

    async def _read_user_input(self) -> str | None:
        # Se estamos pedindo texto, o modo voz fica desligado ate uma acao explicita.
        self._voice_mode = False
        self.cfg.audio.continuous = False
        prefix = self._pending_input
        self._pending_input = ""
        readline = None
        if prefix:
            try:
                import readline as _readline

                _readline.set_startup_hook(lambda: _readline.insert_text(prefix))
                readline = _readline
            except Exception:
                readline = None
        try:
            raw = await asyncio.to_thread(self.ui.prompt, "Talita")
        finally:
            if readline is not None:
                try:
                    readline.set_startup_hook()
                except Exception:
                    pass
        return raw.strip()

    def _voice_available(self) -> bool:
        if self._voice_ok is None:
            ok, msg = Recorder.available()
            self._voice_ok = ok
            self._voice_msg = msg
            if not ok:
                self.ui.warning(f"voz indisponível: {msg}")
        return bool(self._voice_ok)

    def _maybe_auto_mute(self, listener: ContinuousListener) -> None:
        seconds = self.cfg.audio.auto_mute_seconds
        if seconds <= 0 or self._mic_muted or self._busy or self._recording:
            return
        if not self._speaking_event.is_set() or listener.is_capturing():
            return
        if time.monotonic() - self._last_listen_at < seconds:
            return
        self._mic_muted = True
        listener.pause()
        self.ui.info(
            f"microfone desligado após {int(seconds)}s de silêncio; aperte M para retomar"
        )
        self.ui.set_status("⏸ microfone desligado", "M para retomar")

    def _update_idle_status(self, listener: ContinuousListener) -> None:
        if self._mic_muted:
            return
        if listener.is_capturing():
            if self._partial_last:
                return
            self.ui.set_status("● ouvindo…", "fala detectada")
        else:
            self.ui.set_status("● ouvindo…")

    async def _transcribe_audio(self, audio) -> str | None:
        self.ui.set_status("◐ transcrevendo…")
        async with self._stt_lock:
            try:
                text = await asyncio.to_thread(self.transcriber.transcribe, audio)
            except STTError as exc:
                self.ui.clear_status()
                self.ui.error(str(exc))
                return None
        if not text:
            self.ui.clear_status()
            return None
        self.ui.clear_status()
        self.ui.print_transcript(text)
        return text

    async def _partial_transcribe(self, audio) -> None:
        async with self._stt_lock:
            try:
                text = await asyncio.to_thread(self.transcriber.transcribe, audio, False)
            except Exception as exc:  # pragma: no cover
                self.logger.debug("transcricao parcial falhou: %s", exc)
                return
        if text and text != self._partial_last:
            self._partial_last = text
            self.ui.partial(text)

    async def _capture_voice(self, announce: bool = True) -> str | None:
        await self._speaking_event.wait()
        self._recording = True
        try:
            if announce:
                self.ui.set_status("● ouvindo…", "fale e faça uma pausa para encerrar")
            try:
                audio = await asyncio.to_thread(self.recorder.record_until_silence)
            except AudioError as exc:
                self.ui.clear_status()
                self.ui.error(f"microfone: {exc}")
                return None
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.recorder.stop()
                self.ui.clear_status()
                raise
            if audio is None or len(audio) == 0:
                self.ui.clear_status()
                return None
            return await self._transcribe_audio(audio)
        finally:
            self._recording = False
            self.ui.clear_status()

    async def _voice_input(self) -> str | None:
        text = await self._capture_voice(announce=True)
        if text is None:
            return None
        if not self.cfg.stt.confirm_transcript:
            return text
        try:
            correction = await asyncio.to_thread(self.ui.prompt, "Enter aceitar / correção")
        except EOFError:
            correction = ""
        if correction.strip():
            return correction.strip()
        return text

    # ------------------------------------------------------------------- tasks
    async def _handle_task(self, text: str) -> str:
        self._busy = True
        try:
            return await self._handle_task_impl(text)
        finally:
            self._busy = False

    async def _handle_task_impl(self, text: str, speak: bool = True) -> str:
        mode = parse_mode_switch(text)
        if mode:
            self._switch_mode(mode)
            message = f"Modo {self.persona.name} ativado."
            if speak:
                await self._speak(message)
            return message
        assert self.conversation_id is not None
        await self._clear_stale_session()
        self.ui.set_status("◐ pensando…")
        hits = []
        if self.cfg.secondbrain.auto_context and needs_second_brain_context(text):
            self.ui.info("Consultando Second Brain...")
            try:
                hits = await asyncio.to_thread(
                    self.index.search, text, self.cfg.secondbrain.context_results
                )
            except Exception as exc:  # pragma: no cover
                self.logger.warning("busca no Second Brain falhou: %s", exc)
            if hits:
                self.ui.context_hits(hits)
                self.store.add_turn(
                    self.conversation_id,
                    "context",
                    "Notas usadas: " + ", ".join(hit.rel_path for hit in hits),
                )

        prompt = text
        if hits:
            context = format_context(hits, max_chars=self.cfg.secondbrain.context_chars)
            prompt = (
                f"{context}\n\n---\n\n"
                f"Pedido do usuário: {text}\n\n"
                "Responda em português do Brasil, de forma concisa e direta. "
                "Use o contexto acima somente quando for relevante. "
                "Não leia nem edite arquivos do Second Brain para esta resposta: "
                "o contexto já foi fornecido pela Zora."
            )
        goals = self.goals.detect(text)
        if goals:
            prompt = goal_block(goals) + "\n\n---\n\n" + prompt
            self.store.add_turn(
                self.conversation_id,
                "context",
                "Metas relacionadas: " + ", ".join(goal.label for goal in goals),
            )
        prompt = persona_block(self.persona) + "\n\n---\n\n" + prompt
        preferences = self._preferences_block()
        if preferences:
            prompt = preferences + "\n\n---\n\n" + prompt
        if self.cfg.interface.concise:
            prompt = (
                "[Diretriz da Zora: seja conciso e direto; "
                "use ferramentas apenas quando necessário.]\n\n" + prompt
            )

        streaming = self._start_speech_streaming() if speak else False
        try:
            answer = await self._run_turn(prompt)
        except TurnInterrupted:
            self.ui.clear_status()
            self.ui.warning("execução interrompida; a sessão foi preservada")
            await self._stop_speech_streaming()
            return ""
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.ui.clear_status()
            await self.client.abort(self.session_id)
            self.tts.stop()
            self.ui.warning("execução interrompida")
            await self._stop_speech_streaming()
            return ""
        except OpenCodeError as exc:
            self.ui.clear_status()
            self.ui.error(str(exc))
            if exc.body:
                self.logger.debug("corpo do erro: %s", exc.body)
            await self._stop_speech_streaming()
            return ""

        if not answer:
            self.ui.clear_status()
            self.ui.warning("o agente não retornou texto.")
            await self._stop_speech_streaming()
            return ""

        self.store.add_turn(self.conversation_id, "assistant", answer)
        if streaming:
            self.ui.set_status("▶ falando…")
            await self._finish_speech_streaming(answer)
        elif speak:
            if self.tts.enabled:
                self.ui.set_status("▶ falando…")
            await self._speak(answer)
        self.ui.clear_status()
        return answer

    async def _run_turn(self, text: str) -> str:
        """Executa o turno com Ctrl+C escopado: aborta e devolve o controle."""
        loop = asyncio.get_running_loop()
        interrupt = asyncio.Event()

        def on_sigint(signum, frame):
            loop.call_soon_threadsafe(interrupt.set)

        previous = None
        try:
            previous = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, on_sigint)
        except (ValueError, OSError):
            previous = None

        turn_task = asyncio.create_task(self._turn_impl(text))
        interrupt_task = asyncio.create_task(interrupt.wait())
        try:
            done, _pending = await asyncio.wait(
                {turn_task, interrupt_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if turn_task in done:
                return turn_task.result()

            self.tts.stop()
            await self.client.abort(self.session_id)
            turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await turn_task
            raise TurnInterrupted()
        finally:
            interrupt_task.cancel()
            if previous is not None:
                try:
                    signal.signal(signal.SIGINT, previous)
                except (ValueError, OSError):
                    pass

    async def _turn_impl(self, text: str) -> str:
        assert self.conversation_id is not None
        before = await self.client.latest_assistant(self.session_id)
        before_id = (before or {}).get("info", {}).get("id")

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        stop = asyncio.Event()

        async def pump() -> None:
            try:
                async for event in self.client.stream_events(stop=stop):
                    if event_session_id(event) == self.session_id:
                        await queue.put(event)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # pragma: no cover
                self.logger.warning("stream de eventos caiu: %s", exc)

        pump_task = asyncio.create_task(pump())
        user_message_id = "msg_z" + uuid.uuid4().hex[:24]
        try:
            await self.client.prompt_async(
                self.session_id,
                text,
                model=self.client.model,
                message_id=user_message_id,
            )
            streamed = await self._consume_events(queue, ignore_message_id=user_message_id)
        finally:
            stop.set()
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump_task

        if streamed.strip():
            return streamed

        answer = await self._await_final_message(before_id)
        if answer:
            self.ui.print_markdown(answer)
            return answer
        return ""

    async def _consume_events(
        self,
        queue: asyncio.Queue[dict[str, Any]],
        ignore_message_id: str | None = None,
    ) -> str:
        started = time.monotonic()
        last_event_ts = started
        last_text: dict[str, str] = {}
        last_reasoning: dict[str, str] = {}
        collected: list[str] = []
        seen_tools: set[str] = set()
        wrote_stream = False
        saw_activity = False
        saw_busy = False
        tool_running = False
        self._answer_buffer = ""

        try:
            while True:
                elapsed = time.monotonic() - started
                if elapsed > self.cfg.opencode.request_timeout:
                    self.ui.warning("tempo limite da execução atingido")
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    event = None

                if event is None:
                    try:
                        status = await self.client.session_status(self.session_id)
                    except OpenCodeError:
                        status = None
                    if status and not (isinstance(status, dict) and status.get("type") == "idle"):
                        saw_busy = True
                    idle = (
                        bool(status) and isinstance(status, dict) and status.get("type") == "idle"
                    )
                    if idle and (saw_activity or saw_busy) and elapsed > 0.8:
                        break
                    if idle and not saw_activity and elapsed > 1.5:
                        break
                    if (
                        saw_busy
                        and not tool_running
                        and time.monotonic() - last_event_ts > self.cfg.opencode.stall_timeout
                    ):
                        self.ui.warning("o agente parou de responder; abortando a execução")
                        await self.client.abort(self.session_id)
                        raise OpenCodeError("o agente ficou sem responder (timeout de inatividade)")
                    continue

                last_event_ts = time.monotonic()
                kind = event_type(event)
                props = event.get("properties") or {}

                if kind == "message.part.updated":
                    saw_activity = True
                    part = props.get("part") or {}
                    if ignore_message_id and str(part.get("messageID", "")) == ignore_message_id:
                        continue
                    if part.get("type") == "reasoning" and self.cfg.interface.show_reasoning:
                        part_id = part.get("id", "")
                        previous = last_reasoning.get(part_id, "")
                        _delta, current = text_delta(part, previous)
                        last_reasoning[part_id] = current
                        if current:
                            self.ui.reasoning(current)
                            if self._stream_sink is not None:
                                self._stream_sink.reasoning(current)
                    elif part.get("type") == "text" and not (
                        part.get("synthetic") or part.get("ignored")
                    ):
                        part_id = part.get("id", "")
                        previous = last_text.get(part_id, "")
                        delta, current = text_delta(part, previous)
                        last_text[part_id] = current
                        if delta:
                            collected.append(delta)
                            for chunk in self._feed_display(delta):
                                if not wrote_stream:
                                    self.ui.begin_stream()
                                    wrote_stream = True
                                self.ui.token(chunk)
                                if self._stream_sink is not None:
                                    self._stream_sink.token(chunk)
                            self._feed_speech(delta)
                    elif part.get("type") == "tool":
                        state = part.get("state") or {}
                        tstatus = state.get("status")
                        if tstatus in {"running", "pending"}:
                            tool_running = True
                        elif tstatus in {"completed", "error"}:
                            tool_running = False
                        activity = tool_activity(part)
                        if activity:
                            call_id, tool, detail = activity
                            if call_id not in seen_tools:
                                seen_tools.add(call_id)
                                if not wrote_stream:
                                    label = TOOL_LABELS.get(tool, tool)
                                    self.ui.tool(label, detail)
                                    if self._stream_sink is not None:
                                        self._stream_sink.tool(label, detail)
                        if tstatus in {"completed", "error"} and self.cfg.interface.show_diffs:
                            diff = tool_diff(part)
                            if diff:
                                filename, patch = diff
                                self.ui.diff(filename, patch)
                                if self._stream_sink is not None:
                                    self._stream_sink.diff(filename, patch)
                elif kind == "session.idle":
                    break
                elif kind == "session.error":
                    error = props.get("error") or {}
                    raise OpenCodeError(f"erro na sessão OpenCode: {error}")
                elif kind in {"permission.asked", "permission.v2.asked"}:
                    await self._handle_permission(props, kind)
        finally:
            for chunk in self._flush_display():
                if not wrote_stream:
                    self.ui.begin_stream()
                    wrote_stream = True
                self.ui.token(chunk)
                if self._stream_sink is not None:
                    self._stream_sink.token(chunk)
            if wrote_stream:
                self.ui.end_stream()

        return "".join(collected).strip()

    async def _handle_permission(
        self, props: dict[str, Any], kind: str = "permission.asked"
    ) -> None:
        permission_id = props.get("id") or props.get("requestID")
        tool = props.get("permission") or props.get("action") or "ferramenta"
        patterns = props.get("patterns") or props.get("resources") or []
        v2 = kind == "permission.v2.asked"
        if not permission_id:
            return
        if self.cfg.opencode.auto_approve:
            answer = "once"
            self.logger.info("auto-aprovando permissao %s (%s)", permission_id, tool)
        else:
            self._permission_active = True
            try:
                answer = await asyncio.to_thread(self.ui.permission, str(tool), list(patterns))
            finally:
                self._permission_active = False
        try:
            await self.client.reply_permission(
                self.session_id, str(permission_id), answer, v2=v2
            )
        except OpenCodeError as exc:
            self.logger.warning("falha ao responder permissao %s: %s", permission_id, exc)
            self.ui.warning(
                "não foi possível responder à permissão (ela pode ter expirado). "
                "O OpenCode seguirá com a política padrão."
            )

    async def _await_final_message(self, before_id: str | None) -> str:
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            try:
                message = await self.client.latest_assistant(self.session_id)
            except OpenCodeError:
                message = None
            if message:
                info = message.get("info") or {}
                if info.get("id") != before_id:
                    error = message_error(message)
                    if error:
                        raise OpenCodeError(f"O agente retornou erro: {error}")
                    from zora.opencode.events import assistant_text

                    text = assistant_text(message)
                    if text:
                        return text
            await asyncio.sleep(0.2)
        return ""

    # -------------------------------------------------------------- preferences
    def _preferences_block(self) -> str:
        path = self.brain.preferences_path
        try:
            mtime = path.stat().st_mtime if path.is_file() else -1.0
        except OSError:
            mtime = -1.0
        if mtime != self._prefs_mtime:
            self._prefs_cache = self.brain.load_preferences()
            self._prefs_mtime = mtime
        if not self._prefs_cache:
            return ""
        return (
            "[Preferências de interação do usuário — siga sempre, "
            "sem mencionar este bloco]\n" + self._prefs_cache
        )

    async def _handle_preference(self, text: str) -> None:
        added = await asyncio.to_thread(self.brain.append_preference, text, False)
        if added:
            self._prefs_mtime = -1.0
            self.ui.success(f"preferência aprendida: {text}")
        else:
            self.ui.info("essa preferência já estava registrada.")

    # ----------------------------------------------------------------- register
    async def _handle_register(self, intent: RegisterIntent, original: str) -> None:
        assert self.conversation_id is not None
        content = intent.content.strip()
        if not content:
            content = await self._fallback_register_content()
            if not content:
                self.ui.warning("não encontrei conteúdo para registrar.")
                return
            intent.content = content
        title = intent.title or "Nota sem titulo"
        links = await asyncio.to_thread(self.brain.suggest_links, title, content, 3)
        folder = intent.folder or self.cfg.secondbrain.default_folder
        self.ui.note_preview(title, intent.tags, links, content, folder)
        if self.cfg.secondbrain.confirm_create:
            ok = await asyncio.to_thread(self.ui.confirm, "Criar esta nota no Second Brain?", False)
            if not ok:
                self.ui.info("operação cancelada; nenhuma nota foi criada.")
                return
        try:
            note = await asyncio.to_thread(
                self.brain.create_note,
                title,
                content,
                folder,
                intent.tags,
                links,
                True,
            )
        except NoteConflict as exc:
            self.ui.warning(str(exc))
            return
        except Exception as exc:
            self.ui.error(f"falha ao criar nota: {exc}")
            return

        self.store.record_note(self.conversation_id, intent.kind, str(note.path), note.title)
        self.ui.success(f"nota criada: {note.rel_path}")
        await self._speak(f"Nota criada: {note.title}")

    async def _fallback_register_content(self) -> str:
        assert self.conversation_id is not None
        for turn in reversed(self.store.turns(self.conversation_id, limit=6)):
            if turn["role"] == "assistant":
                return str(turn["text"])
        return ""

    # ----------------------------------------------------------------- commands
    async def _handle_command(self, text: str) -> bool:
        command, _, argument = text.partition(" ")
        command = command.lower().strip()
        argument = argument.strip()

        if command in {"/sair", "/quit", "/exit"}:
            self.ui.info("até logo.")
            return True
        if command in {"/", "/ajuda", "/help"}:
            self.ui.help()
        elif command == "/status":
            await self._show_status()
        elif command == "/novo":
            await self._new_session()
        elif command == "/sessao":
            self.ui.info(f"sessão OpenCode: {self.session_id}")
        elif command == "/buscar":
            if not argument:
                self.ui.warning("uso: /buscar <termo>")
            else:
                hits = await asyncio.to_thread(
                    self.index.search, argument, max(5, self.cfg.secondbrain.context_results)
                )
                self.ui.search_results(hits)
        elif command == "/nota":
            if not argument:
                self.ui.warning("uso: /nota <título>")
            else:
                try:
                    note = await asyncio.to_thread(self.brain.read_note, argument)
                    self.ui.print_note(note)
                except FileNotFoundError as exc:
                    self.ui.warning(str(exc))
        elif command == "/indexar":
            self.ui.info("reindexando Second Brain...")
            try:
                stats = await asyncio.to_thread(self.index.reindex, True)
                self.ui.success(
                    f"{stats['indexed']} nota(s) indexada(s), {stats['total']} no total"
                )
            except Exception as exc:
                self.ui.error(f"falha ao indexar: {exc}")
        elif command == "/modelo":
            await self._switch_model(argument)
        elif command in {"/modo", "/perfil", "/persona"}:
            self._switch_mode(argument)
        elif command in {"/metas", "/objetivos", "/painel"}:
            await self._show_dashboard()
        elif command in {"/bolsa", "/acao", "/ações"}:
            await self._handle_quote(argument)
        elif command in {"/carteira", "/watchlist", "/watch"}:
            await self._handle_watchlist(argument)
        elif command in {"/foco", "/focos"}:
            await self._handle_focus(argument)
        elif command in {"/tarefas", "/tarefa"}:
            await self._handle_tasks(argument)
        elif command == "/voz":
            self._switch_voice(argument)
        elif command == "/interromper":
            await self.client.abort(self.session_id)
            self.tts.stop()
            self.ui.info("interrupção enviada.")
        elif command in {"/lembrar", "/lembrete"}:
            if not argument:
                self.ui.warning("uso: /lembrar <texto> em 30 minutos")
            else:
                spec = parse_reminder(argument, require_prefix=False)
                if spec is None:
                    self.ui.warning("não entendi o horário. Ex.: /lembrar beber água em 30 minutos")
                else:
                    await self._handle_reminder(spec)
        elif command == "/lembretes":
            self.ui.reminders(self.store.pending_reminders())
        elif command == "/cancelar":
            try:
                reminder_id = int(argument)
            except ValueError:
                self.ui.warning("uso: /cancelar <id>")
            else:
                if self.store.cancel_reminder(reminder_id):
                    self.ui.success(f"lembrete #{reminder_id} cancelado")
                else:
                    self.ui.warning(f"lembrete #{reminder_id} não encontrado")
        elif command == "/proativo":
            self._toggle_proactive(argument)
        elif command in {"/continuo", "/contínuo"}:
            self._toggle_continuous(argument)
        elif command in {"/fala", "/ouvir"}:
            self._toggle_continuous("on")
        elif command in {"/texto", "/digitar"}:
            self._toggle_continuous("off")
        elif command == "/contexto":
            self._toggle_context(argument)
        elif command in {"/preferencia", "/preferência"}:
            if not argument:
                self.ui.warning("uso: /preferencia <texto>")
            else:
                await self._handle_preference(argument)
        elif command in {"/preferencias", "/preferências"}:
            text = self.brain.load_preferences()
            self.ui.print_markdown(text or "(sem preferências registradas)", "Preferências")
        elif command == "/confirmar":
            self._toggle_create_confirmation(argument)
        elif command in {"/autoaprovar", "/autoapprove"}:
            self._toggle_auto_approve(argument)
        elif command == "/historico":
            self._show_history()
        else:
            self.ui.warning(f"comando desconhecido: {command} (use /ajuda)")
        return False

    async def _show_status(self) -> None:
        health = await self.client.health()
        sb_stats = self.index.stats()
        mem = self.store.stats()
        self.ui.status(
            self.directory,
            self.session_id,
            self.cfg.opencode.model,
            sb_stats["notes"],
            self.tts.provider,
        )
        self.ui.info(f"servidor: {self.client.endpoint} · saudável={bool(health)}")
        self.ui.info(
            f"memória operacional: {mem['conversations']} conversa(s), "
            f"{mem['turns']} turno(s), {mem['records']} registro(s)"
        )
        pending = self.store.reminder_stats()["pending"]
        self.ui.info(
            f"proativo: {'ativo' if self.cfg.proactive.enabled else 'desativado'} · "
            f"{pending} lembrete(s) pendente(s)"
        )
        self.ui.info(f"second brain: {self.brain.root} · {sb_stats}")
        self.ui.info(f"modo ativo: {self.persona.name} ({self.persona.key})")

    async def _new_session(self) -> None:
        assert self.conversation_id is not None
        conv = self.store.create_conversation(str(self.directory), title="Nova conversa")
        self.conversation_id = int(conv["id"])
        session = await self.client.create_session(
            title=f"Zora — {self.directory.name}", model=self.client.model
        )
        self.store.set_session(self.conversation_id, session.id)
        self.session_id = session.id
        self.ui.success(f"nova sessão: {session.id}")

    async def _switch_model(self, argument: str) -> None:
        if not argument:
            self.ui.warning("uso: /modelo <provider/model>")
            return
        from zora.opencode.models import ModelRef

        try:
            model = ModelRef.parse(argument)
        except ValueError as exc:
            self.ui.error(str(exc))
            return
        self.cfg.opencode.model = model.slug
        self.store.set_meta("model", model.slug)
        self.ui.success(f"modelo da próxima mensagem: {model.slug}")

    async def _show_dashboard(self) -> None:
        moon = moon_details()
        note = ""
        phase = principal_phase_today(datetime.now().astimezone() + timedelta(days=1))
        if phase:
            _key, name, moment = phase
            try:
                manifestation = await self.agenda.lunation_note(
                    moment.date().isoformat()
                )
            except AgendaError:
                manifestation = ""
            note = f"Amanhã: {name}"
            if manifestation:
                note += f" — manifestações na Agenda: {manifestation}"
        self.ui.dashboard(self.focus.current, self.goals.all(), moon, note)

    async def _handle_quote(self, argument: str) -> None:
        ticker = argument.strip().split()[0] if argument.strip() else ""
        if not ticker:
            self.ui.warning("uso: /bolsa <ticker> (ex.: PETR4, AAPL)")
            return
        try:
            quote = await fetch_quote(ticker)
        except FinanceError as exc:
            self.ui.error(str(exc))
            return
        self.ui.info(format_quote(quote))

    async def _handle_watchlist(self, argument: str) -> None:
        parts = argument.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub in {"add", "adicionar", "+"}:
            if not rest:
                self.ui.warning("uso: /carteira add <ticker> ou /carteira add Nome=TICKER")
                return
            if "=" in rest:
                label, ticker = rest.split("=", 1)
            else:
                label, ticker = rest, rest
            item = self.watchlist.add(label, ticker)
            self.ui.success(f"{item.label} ({item.ticker}) na carteira")
            return
        if sub in {"remove", "remover", "del", "-"}:
            if not rest:
                self.ui.warning("uso: /carteira remover <ticker>")
                return
            if self.watchlist.remove(rest):
                self.ui.success(f"{rest.upper()} removido da carteira")
            else:
                self.ui.warning(f"{rest.upper()} não estava na carteira")
            return
        items = self.watchlist.all()
        if not items:
            self.ui.info("carteira vazia; use /carteira add <ticker>")
            return
        results = await asyncio.gather(
            *(fetch_quote(item.ticker) for item in items), return_exceptions=True
        )
        rows: list[dict[str, str]] = []
        for item, result in zip(items, results, strict=False):
            if isinstance(result, Exception):
                rows.append(
                    {
                        "label": item.label,
                        "ticker": item.ticker,
                        "price": "?",
                        "change": "?",
                    }
                )
                continue
            price = f"{result.price:.2f}" if result.price is not None else "?"
            change = (
                f"{result.change_pct:+.2f}%"
                if result.change_pct is not None
                else "?"
            )
            rows.append(
                {
                    "label": item.label,
                    "ticker": result.symbol,
                    "price": f"{result.currency} {price}".strip(),
                    "change": change,
                }
            )
        self.ui.watchlist(rows)

    async def _handle_focus(self, argument: str) -> None:
        parts = argument.strip().split(maxsplit=2)
        sub = parts[0].lower() if parts else ""
        if sub in {"definir", "novo", "set"}:
            if len(parts) < 3:
                self.ui.warning("uso: /foco definir semanal|mensal|trimestral <texto>")
                return
            period_raw = parts[1].lower()
            period = {
                "semanal": "week",
                "semana": "week",
                "mensal": "month",
                "mes": "month",
                "mês": "month",
                "trimestral": "quarter",
                "trimestre": "quarter",
            }.get(period_raw)
            if period is None:
                self.ui.warning("período inválido; use semanal, mensal ou trimestral")
                return
            text = parts[2].strip()
            goals = [part.strip() for part in re.split(r"[;,]", text) if part.strip()]
            if len(goals) <= 1:
                goals = []
            focus = self.focus.set(period, text, goals)
            self.ui.success(f"foco {period_label(focus.period)} definido até {focus.end}")
            await self._show_dashboard()
            return
        if sub in {"limpar", "clear", "remover"}:
            self.focus.clear()
            self.ui.success("foco limpo")
            return
        focus = self.focus.current
        if focus is None:
            self.ui.info("nenhum foco definido; use /foco definir semanal <texto>")
        else:
            self.ui.info(
                f"foco {period_label(focus.period)}: {focus.label} "
                f"({focus.start} → {focus.end})"
            )
            for goal in focus.goals:
                self.ui.info(f"  • {goal}")
        detail = moon_details()
        self.ui.info(
            f"{detail.moon_emoji} {detail.phase_name} · "
            f"{detail.sign_symbol} {detail.sign_name} · "
            f"{detail.element_emoji} {detail.element} · {detail.phase_emoji}"
        )

    async def _check_focus(self, now: datetime) -> None:
        if not self.cfg.focus.enabled:
            return
        phase = principal_phase_today(now)
        if self.cfg.focus.prompt_on_moon and phase is not None:
            key, name, moment = phase
            today = moment.date().isoformat()
            if self.focus.last_moon_prompt() != today:
                self.focus.set_last_moon_prompt(today)
                ritual = RITUALS[key][1]
                manifestation = ""
                try:
                    manifestation = await self.agenda.lunation_note(today)
                except AgendaError as exc:
                    self.logger.warning("lunacao: %s", exc)
                message = f"Hoje é {name}. Ritual do ciclo: {ritual}."
                if manifestation:
                    message += f" Na Agenda, suas manifestações: {manifestation}."
                message += " Quer definir ou ajustar o foco?"
                await self._say_proactive(message)
                return
        if self.focus.is_expired(now):
            await self._say_proactive(
                "Seu foco atual venceu. Quer definir um novo foco para o próximo ciclo?"
            )

    def _find_task(self, query: str) -> Task | None:
        query = query.strip().lower()
        if not query:
            return None
        for task in self.tasks.all():
            if task.id.startswith(query) or query in task.title.lower():
                return task
        return None

    async def _handle_tasks(self, argument: str) -> None:
        parts = argument.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub in {"limpar", "clean", "limpar-concluidas"}:
            count = self.tasks.clear_done()
            self.ui.success(f"{count} tarefa(s) concluída(s) removida(s)")
            return
        if sub in {"sync", "sincronizar", "atualizar"}:
            try:
                pages = await self.agenda.pages(30)
            except AgendaError as exc:
                self.ui.error(str(exc))
                return
            page = next(
                (
                    item
                    for item in pages
                    if "tarefa" in str(item.get("title") or "").lower()
                ),
                None,
            )
            if page is None:
                self.ui.warning("não achei uma página de tarefas na Agenda")
                return
            synced = self.tasks.sync_text(str(page.get("content") or ""))
            self.ui.success(
                f"{len(synced)} tarefa(s) sincronizada(s) de '{page.get('title')}'"
            )
        elif sub in {"fazer", "executar", "run"}:
            task = self._find_task(rest)
            if task is None:
                self.ui.warning("uso: /tarefas fazer <id ou trecho do título>")
                return
            self.tasks.set_status(task.id, "in_progress")
            prompt = (
                f"Tarefa de longo prazo registrada na Agenda: {task.title}\n\n"
                "Execute isso no repositório atual, com mudanças mínimas e testes. "
                "Ao terminar, explique o que fez e o que ainda falta."
            )
            await self._handle_task_impl(prompt, speak=True)
            return
        elif sub in {"concluir", "done", "feito"}:
            task = self._find_task(rest)
            if task is None:
                self.ui.warning("uso: /tarefas concluir <id ou trecho do título>")
                return
            self.tasks.set_status(task.id, "done")
            self.ui.success(f"tarefa concluída: {task.title}")
            return

        tasks = self.tasks.all()
        if not tasks:
            self.ui.info("nenhuma tarefa registrada; use /tarefas sync")
            return
        symbols = {"done": "✓", "in_progress": "◐", "pending": "•"}
        for task in tasks:
            symbol = symbols.get(task.status, "•")
            self.ui.info(f"{symbol} [{task.status}] {task.id} — {task.title}")
            if task.notes:
                self.ui.info(f"   {task.notes}")

    def _switch_mode(self, argument: str) -> None:
        if not argument:
            self.ui.info(f"modo atual: {self.persona.name} ({self.persona.key})")
            for persona in PERSONAS.values():
                self.ui.info(f"  {persona.key}: {persona.name} — {persona.summary}")
            return
        persona = get_persona(argument)
        if persona is None:
            options = ", ".join(PERSONAS)
            self.ui.warning(f"modo desconhecido: {argument} (opções: {options})")
            return
        self.persona = persona
        self.store.set_meta("mode", persona.key)
        self.ui.success(f"modo ativo: {persona.name} — {persona.summary}")

    def _switch_voice(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "ligar"}:
            self.tts.enabled = True
            self.ui.success("voz ativada")
        elif arg in {"off", "desligar"}:
            self.tts.enabled = False
            self.ui.success("voz desativada")
        elif arg in {"edge", "say", "none"}:
            self.tts.set_provider(arg)
            self.ui.success(f"provedor de voz: {arg}")
        elif not arg:
            self.ui.info(
                f"voz={'ligada' if self.tts.enabled else 'desligada'} · provedor={self.tts.provider}"
            )
        else:
            self.ui.warning("uso: /voz on|off|edge|say|none")

    def _toggle_context(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.secondbrain.auto_context = arg == "on"
        else:
            self.cfg.secondbrain.auto_context = not self.cfg.secondbrain.auto_context
        state = "ativado" if self.cfg.secondbrain.auto_context else "desativado"
        self.ui.success(f"contexto automático do Second Brain {state}")

    def _toggle_create_confirmation(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.secondbrain.confirm_create = arg == "on"
        else:
            self.cfg.secondbrain.confirm_create = not self.cfg.secondbrain.confirm_create
        state = "pedindo confirmação" if self.cfg.secondbrain.confirm_create else "sem confirmação"
        self.ui.success(f"criação de nota: {state}")

    def _toggle_auto_approve(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.opencode.auto_approve = arg == "on"
        else:
            self.cfg.opencode.auto_approve = not self.cfg.opencode.auto_approve
        if self.cfg.opencode.auto_approve:
            self.ui.warning(
                "auto-aprovação de ferramentas ATIVA: o agente pode executar "
                "comandos e editar arquivos sem perguntar."
            )
        else:
            self.ui.success("auto-aprovação de ferramentas desativada")

    def _show_history(self) -> None:
        assert self.conversation_id is not None
        turns = self.store.turns(self.conversation_id, limit=10)
        if not turns:
            self.ui.info("sem histórico nesta conversa.")
            return
        for turn in turns:
            text = str(turn["text"])
            if len(text) > 300:
                text = text[:300] + "..."
            self.ui.console.print(f"[bold]{escape_user(turn['role'])}[/]: {escape_user(text)}")

    # --------------------------------------------------------------- proativas
    def _mark_activity(self) -> None:
        self._last_activity = time.monotonic()

    def _user_name(self) -> str:
        if self._user_name_cache is None:
            preferences = self.brain.load_preferences()
            match = re.search(r"Nome preferido:\s*([^\n.]+)", preferences, re.IGNORECASE)
            self._user_name_cache = match.group(1).strip() if match else ""
        return self._user_name_cache

    async def _handle_reminder(self, spec: ReminderSpec) -> None:
        reminder_id = self.store.add_reminder(spec.label, spec.due.isoformat(timespec="seconds"))
        when = spec.due.strftime("%d/%m às %H:%M")
        self.ui.success(f"lembrete #{reminder_id} para {when}: {spec.label}")
        await self._speak(f"Combinado. Vou te lembrar de {spec.label} {when}.")

    def _toggle_continuous(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.audio.continuous = arg == "on"
        else:
            self.cfg.audio.continuous = not self.cfg.audio.continuous
        self._voice_mode = self.cfg.audio.continuous
        if self.cfg.audio.continuous:
            self.ui.success("modo contínuo ativado; fale à vontade")
        else:
            self.ui.success("modo contínuo desativado; use Enter para falar")

    def _toggle_proactive(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.proactive.enabled = arg == "on"
        else:
            self.cfg.proactive.enabled = not self.cfg.proactive.enabled
        if self.cfg.proactive.enabled:
            if self._proactive_task is None or self._proactive_task.done():
                self._proactive_task = asyncio.create_task(self._proactive_loop(greet=False))
            self.ui.success("iniciativa proativa ativada")
        else:
            self.ui.success("iniciativa proativa desativada (lembretes já criados ficam salvos)")

    async def _proactive_loop(self, greet: bool = True) -> None:
        cfg = self.cfg.proactive
        if not cfg.enabled:
            return
        await asyncio.sleep(1.0)
        if greet and cfg.startup_greeting:
            await self._startup_greeting()
        while True:
            await asyncio.sleep(max(5.0, cfg.check_interval_seconds))
            if not self.cfg.proactive.enabled:
                continue
            if self._busy or self._recording or self._listening:
                continue
            now = datetime.now().astimezone()
            if in_quiet_hours(now, cfg.quiet_hours):
                continue
            try:
                if cfg.reminders:
                    await self._fire_due_reminders()
                await self._check_agenda(now)
                await self._check_task_page(now)
                await self._check_focus(now)
                if cfg.idle_checkin:
                    idle_for = (time.monotonic() - self._last_activity) / 60.0
                    if idle_for >= cfg.idle_minutes and self._can_idle_checkin(now):
                        await self._say_proactive(await self._generate_initiative_message())
                        self._last_activity = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                self.logger.warning("falha na rotina proativa: %s", exc)

    async def _check_agenda(self, now: datetime) -> None:
        cfg = self.cfg.agenda
        if not cfg.enabled or not self.agenda.enabled:
            return
        if time.monotonic() < self._agenda_next_check:
            return
        self._agenda_next_check = time.monotonic() + max(5.0, cfg.poll_minutes * 60.0)
        try:
            tasks = await self.agenda.pending_today(now.date().isoformat())
        except AgendaError as exc:
            self.logger.warning("agenda: %s", exc)
            return
        if now.date() != self._agenda_day:
            self._agenda_day = now.date()
            self._agenda_seen = set()
        ids = {str(task.get("id")) for task in tasks if task.get("id")}
        new_ids = ids - self._agenda_seen
        self._agenda_seen = ids
        self._agenda_pending_names = [
            str(task.get("text") or "tarefa") for task in tasks
        ]
        if not new_ids:
            return
        new_tasks = [task for task in tasks if str(task.get("id")) in new_ids]
        names = [str(task.get("text") or "tarefa") for task in new_tasks]
        shown = names[: cfg.max_items]
        if len(names) > len(shown):
            shown.append(f"e mais {len(names) - len(shown)}")
        if ids == new_ids:
            message = (
                f"Na agenda de hoje você tem {len(names)} tarefa(s) pendente(s): "
                + ", ".join(shown)
                + "."
            )
        else:
            message = "Novas tarefas na agenda: " + ", ".join(shown) + "."
        if not cfg.notify_pending:
            self.logger.debug("agenda: %s", message)
            return
        await self._say_proactive(message)

    async def _check_task_page(self, now: datetime) -> None:
        cfg = self.cfg.agenda
        if not cfg.enabled or not self.agenda.enabled or cfg.tasks_poll_seconds <= 0:
            return
        if time.monotonic() < self._tasks_next_check:
            return
        self._tasks_next_check = time.monotonic() + max(5.0, cfg.tasks_poll_seconds)
        try:
            meta = await self.agenda.pages_meta(30)
        except AgendaError as exc:
            self.logger.warning("tarefas: %s", exc)
            return
        meta_page = next(
            (item for item in meta if "tarefa" in str(item.get("title") or "").lower()),
            None,
        )
        if meta_page is None:
            return
        updated_at = str(meta_page.get("updated_at") or "")
        if updated_at and updated_at == self._task_page_updated_at:
            return
        try:
            pages = await self.agenda.pages(30)
        except AgendaError as exc:
            self.logger.warning("tarefas: %s", exc)
            return
        page = next(
            (item for item in pages if "tarefa" in str(item.get("title") or "").lower()),
            None,
        )
        if page is None:
            return
        self._task_page_updated_at = updated_at or str(page.get("updated_at") or "")
        before = {task.id for task in self.tasks.all()}
        synced = self.tasks.sync_text(str(page.get("content") or ""))
        new_tasks = [task for task in synced if task.id not in before]
        for task in new_tasks:
            plan = task.notes or await self._plan_task(task.title)
            self.tasks.set_status(task.id, "in_progress", plan or "")
            self.logger.debug("nova tarefa sincronizada: %s", task.title)

    async def _plan_task(self, title: str) -> str:
        prompt = (
            "Monte um plano curto (até 5 passos) para executar esta tarefa no "
            f"projeto {self.directory}: {title}. Não execute nada e não use "
            "ferramentas; responda apenas o plano, em português."
        )
        system = "Você é a Zora. Seja objetiva e prática."
        try:
            if not self._task_session_id:
                session = await self.client.create_session(
                    title="Zora — tarefas", model=self.client.model
                )
                self._task_session_id = session.id
            response = await asyncio.wait_for(
                self.client.prompt_sync(self._task_session_id, prompt, system=system),
                timeout=25.0,
            )
            text = assistant_text(response) if isinstance(response, dict) else ""
            return " ".join(text.split()).strip()
        except Exception as exc:  # pragma: no cover - depende do modelo/rede
            self.logger.warning("plano da tarefa falhou: %s", exc)
            return ""

    async def _fire_due_reminders(self) -> None:
        if not self.cfg.proactive.reminders:
            return
        now = datetime.now().astimezone()
        if in_quiet_hours(now, self.cfg.proactive.quiet_hours):
            return
        for row in self.store.due_reminders(now.isoformat()):
            await self._say_proactive(f"Lembrete: {row['text']}", count=False)
            self.store.mark_reminder_spoken(int(row["id"]))

    def _can_idle_checkin(self, now: datetime) -> bool:
        cfg = self.cfg.proactive
        if self._proactive_day != now.date():
            self._proactive_day = now.date()
            self._proactive_count = 0
        if self._proactive_count >= cfg.daily_max:
            return False
        elapsed = (time.monotonic() - self._last_proactive) / 60.0
        return elapsed >= cfg.min_interval_minutes

    async def _say_proactive(self, text: str, count: bool = True) -> None:
        self.ui.proactive(text)
        if count:
            self._proactive_count += 1
            self._last_proactive = time.monotonic()
        await self._speak(text)

    def _idle_message(self) -> str:
        options = [
            "Ainda estou por aqui. Quer continuar de onde paramos?",
            "Se quiser, posso resumir o que fizemos até agora.",
            "Quer retomar algum projeto ou revisar suas metas?",
            "Estou disponível se quiser pensar em algo junto.",
            "Como está seu ânimo agora?",
            "Quer só conversar um pouco?",
            "Posso te ajudar a destravar alguma coisa?",
            "O que está ocupando sua cabeça agora?",
            "Quer um café e uma pausa?",
            "Tô por aqui, sem pressa.",
            "Como foi seu dia até agora?",
        ]
        choices = [option for option in options if option != self._last_idle_message]
        message = random.choice(choices or options)
        self._last_idle_message = message
        return message

    async def _generate_initiative_message(self, greeting: bool = False) -> str:
        now = datetime.now().astimezone()
        hora = now.strftime("%H:%M")
        pending = self.store.pending_reminders()
        agenda = ", ".join(self._agenda_pending_names[:5]) or "nenhuma"
        metas = ", ".join(goal.label for goal in self.goals.all()) or "nenhuma"
        last = self._last_idle_message or "(nenhuma)"
        if greeting:
            instruction = (
                "É a primeira mensagem do dia. Cumprimente a Talita de forma "
                "carinhosa, acolhedora e espontânea."
            )
        else:
            instruction = (
                "Inicie uma conversa com a Talita de forma carinhosa, acolhedora "
                "e espontânea, como quem está por perto."
            )
        prompt = (
            f"Contexto: agora são {hora} ({greeting_for(now)}). "
            f"Modo ativo: {self.persona.name}. "
            f"Lembretes pendentes: {len(pending)}. "
            f"Tarefas pendentes na agenda: {agenda}. "
            f"Metas: {metas}. "
            f"Última mensagem de iniciativa: {last}.\n\n"
            "Pode mencionar metas ou tarefas quando for genuinamente relevante ou "
            "houver algo novo/urgente. Evite repetir pendências por hábito.\n\n"
            f"{instruction} Responda APENAS com a frase, em português do Brasil, "
            "com no máximo 25 palavras. Não use listas, emojis ou aspas. Não diga "
            "que é uma IA e não repita a última mensagem. Não precisa usar o nome "
            "dela toda vez."
        )
        system = (
            "Você é a Zora, assistente pessoal da Talita. Seja calorosa, "
            "carinhosa e espontânea, sem exageros."
        )
        try:
            if not self._initiative_session_id:
                session = await self.client.create_session(
                    title="Zora — iniciativa", model=self.client.model
                )
                self._initiative_session_id = session.id
            response = await asyncio.wait_for(
                self.client.prompt_sync(
                    self._initiative_session_id, prompt, system=system
                ),
                timeout=20.0,
            )
            text = assistant_text(response) if isinstance(response, dict) else ""
            text = " ".join(text.split()).strip().strip('"').strip("'")
            if text:
                self._last_idle_message = text
                return text
        except Exception as exc:  # pragma: no cover - depende do modelo/rede
            self.logger.warning("iniciativa via LLM falhou: %s", exc)
            if self._initiative_session_id:
                with contextlib.suppress(Exception):
                    await self.client.abort(self._initiative_session_id)
        return self._idle_message()

    async def _startup_greeting(self) -> None:
        now = datetime.now().astimezone()
        message = await self._generate_initiative_message(greeting=True)
        pending = self.store.pending_reminders()
        if pending:
            message += f" Você tem {len(pending)} lembrete(s) pendente(s)."
        if in_quiet_hours(now, self.cfg.proactive.quiet_hours):
            self.ui.proactive(message)
            return
        await self._say_proactive(message, count=False)

    # ------------------------------------------------------------------- voice
    def _start_speech_streaming(self) -> bool:
        """Fala a resposta por frases conforme o texto faz streaming."""
        if not self.tts.enabled:
            return False
        self._speech_buffer = ""
        self._speech_pending = ""
        self._speech_fed_chars = 0
        self._speech_active = True
        self._speech_queue = asyncio.Queue()
        self._speech_task = asyncio.create_task(self._speech_worker())
        return True

    def _feed_speech(self, text: str) -> None:
        if not self._speech_active or not text:
            return
        self._speech_buffer += text
        for sentence in self._flush_sentences():
            clean = clean_for_speech(sentence)
            if clean:
                self._speech_fed_chars += len(clean)
                self._enqueue_speech(clean)

    def _enqueue_speech(self, text: str) -> None:
        self._speech_pending = ""
        if self._speech_queue is not None and text:
            self._speech_queue.put_nowait(text)

    def _flush_sentences(self) -> list[str]:
        buf = self._speech_buffer
        out: list[str] = []
        while True:
            match = _SENTENCE_END.search(buf)
            if match is None:
                break
            sentence = buf[: match.start() + 1].strip()
            buf = buf[match.end() :]
            if sentence:
                out.append(sentence)
        self._speech_buffer = buf
        return out

    def _take_display_units(self, force: bool = False) -> list[str]:
        """Separa o texto ja exibivel em frases/linhas (preserva quebras)."""
        buf = self._answer_buffer
        units: list[str] = []
        while True:
            match = _SENTENCE_END.search(buf)
            if match is None:
                break
            end = match.end()
            units.append(buf[:end])
            buf = buf[end:]
        if force:
            if buf.strip():
                units.append(buf)
            buf = ""
        elif len(buf) > 240:
            cut = buf.rfind(" ", 0, 240)
            if cut <= 0:
                cut = 240
            units.append(buf[: cut + 1])
            buf = buf[cut + 1 :]
        self._answer_buffer = buf
        return units

    def _feed_display(self, delta: str) -> list[str]:
        self._answer_buffer += delta
        cleaned: list[str] = []
        for unit in self._take_display_units():
            text = strip_markdown(unit)
            if text.strip():
                cleaned.append(text)
        return cleaned

    def _flush_display(self) -> list[str]:
        cleaned: list[str] = []
        for unit in self._take_display_units(force=True):
            text = strip_markdown(unit)
            if text.strip():
                cleaned.append(text)
        return cleaned

    async def _speech_worker(self) -> None:
        queue = self._speech_queue
        if queue is None:
            return
        chunk = await queue.get()
        prepared = await self._prepare_speech(chunk)
        play_task: asyncio.Task | None = None
        try:
            while chunk is not None:
                play_task = asyncio.create_task(self._play_prepared(prepared))
                nxt = await queue.get()
                prepared_next = await self._prepare_speech(nxt)
                await play_task
                play_task = None
                if nxt is not None and self.cfg.tts.sentence_pause_ms > 0:
                    await asyncio.sleep(self.cfg.tts.sentence_pause_ms / 1000.0)
                chunk = nxt
                prepared = prepared_next
        finally:
            if play_task is not None and not play_task.done():
                play_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await play_task

    async def _prepare_speech(
        self, chunk: str | None
    ) -> tuple[TTSBackend, Path] | None:
        if chunk is None:
            return None
        try:
            return await self.tts.prepare(chunk)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.tts.stop()
            raise
        except Exception as exc:  # pragma: no cover
            self.logger.warning("falha no TTS: %s", exc)
            return None

    async def _play_prepared(self, prepared: tuple[TTSBackend, Path] | None) -> None:
        if prepared is None:
            return
        async with self._tts_lock:
            self._speaking_event.clear()
            try:
                await self.tts.play_prepared(prepared)
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.tts.stop()
                raise
            except Exception as exc:  # pragma: no cover
                self.logger.warning("falha na reproducao do TTS: %s", exc)
            finally:
                self._speaking_event.set()

    async def _finish_speech_streaming(self, fallback: str) -> None:
        if not self._speech_active:
            return
        tail = self._speech_buffer.strip()
        clean_tail = clean_for_speech(tail) if tail else ""
        if clean_tail:
            self._speech_fed_chars += len(clean_tail)
            self._speech_pending = f"{self._speech_pending} {clean_tail}".strip()
        if self._speech_fed_chars == 0 and fallback:
            clean_tail = clean_for_speech(fallback)
            self._speech_fed_chars += len(clean_tail)
            self._speech_pending = f"{self._speech_pending} {clean_tail}".strip()
        queue = self._speech_queue
        if queue is not None:
            if self._speech_pending:
                await queue.put(self._speech_pending)
                self._speech_pending = ""
            await queue.put(None)
            task = self._speech_task
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._speech_active = False
        self._speech_task = None
        self._speech_queue = None
        self._speech_buffer = ""
        self._speech_pending = ""

    async def _stop_speech_streaming(self) -> None:
        if not self._speech_active:
            return
        self._speech_active = False
        self.tts.stop()
        task = self._speech_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._speech_task = None
        self._speech_queue = None
        self._speech_buffer = ""
        self._speech_pending = ""

    async def _speak(self, text: str) -> None:
        if not self.tts.enabled:
            return
        listener = self._listener
        resume_after = False
        if (
            listener is not None
            and not self._mic_muted
            and not self._recording
            and not listener.is_paused()
        ):
            listener.pause()
            resume_after = True
        try:
            async with self._tts_lock:
                self._speaking_event.clear()
                try:
                    await self.tts.speak(text)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    self.tts.stop()
                    raise
                except Exception as exc:  # pragma: no cover
                    self.logger.warning("falha no TTS: %s", exc)
                finally:
                    self._speaking_event.set()
        finally:
            if resume_after and listener is not None and not self._mic_muted:
                listener.resume()
                self._last_listen_at = time.monotonic()


def escape_user(text: str) -> str:
    from rich.markup import escape

    return escape(str(text))
