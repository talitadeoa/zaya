"""Contrato explicito compartilhado entre os mixins do Orchestrator.

Os mixins (fala, turnos, voz, comandos, registro e iniciativa) operam sobre o
mesmo objeto e acessam estado criado em ``Orchestrator.__init__`` alem de
metodos definidos em outros mixins. Sem uma declaracao comum, o mypy analisa
cada classe isoladamente e nao enxerga esse acoplamento.

Esta classe apenas declara os atributos e os metodos cruzados, sem valores e
sem implementacao. Nunca deve ser instanciada; a base concreta continua sendo
o ``Orchestrator``.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from zaya.agenda.client import AgendaClient
from zaya.agent import AgentClient
from zaya.audio.recorder import ContinuousListener, Recorder
from zaya.config import Config
from zaya.controller.goals import GoalTracker
from zaya.controller.intents import RegisterIntent
from zaya.controller.modes import Persona
from zaya.controller.proactive import ReminderSpec
from zaya.finance.watchlist import Watchlist
from zaya.focus import FocusStore
from zaya.interface.terminal import TerminalUI
from zaya.memory.store import MemoryStore
from zaya.secondbrain.index import SecondBrainIndex
from zaya.secondbrain.notes import SecondBrain
from zaya.stt.faster_whisper import WhisperTranscriber
from zaya.tasks.store import TaskStore
from zaya.telegram.client import TelegramClient
from zaya.tts.manager import TTSManager


class StreamSink(Protocol):
    """Destino de streaming consumido durante um turno."""

    def token(self, text: str) -> None: ...

    def reasoning(self, text: str) -> None: ...

    def tool(self, name: str, detail: str = "") -> None: ...

    def diff(self, filename: str, patch: str) -> None: ...


class OrchestratorContract(ABC):
    # --- servicos e contexto ---
    cfg: Config
    logger: logging.Logger
    ui: TerminalUI
    store: MemoryStore
    index: SecondBrainIndex
    brain: SecondBrain
    tts: TTSManager
    transcriber: WhisperTranscriber
    recorder: Recorder
    client: AgentClient
    telegram: TelegramClient
    directory: Path
    persona: Persona
    conversation_id: int | None
    session_id: str

    goals: GoalTracker
    watchlist: Watchlist
    focus: FocusStore
    tasks: TaskStore
    agenda: AgendaClient

    # --- estado de execucao ---
    _voice_ok: bool | None
    _voice_msg: str
    _prefs_cache: str
    _prefs_mtime: float
    _last_activity: float
    _busy: bool
    _restart_requested: bool
    _last_error: str
    _recording: bool
    _listening: bool
    _voice_mode: bool
    _pending_input: str
    _stream_sink: StreamSink | None
    _mic_muted: bool
    _continuous_hint_shown: bool
    _permission_active: bool
    _last_listen_at: float
    _listener: ContinuousListener | None
    _speaking_event: asyncio.Event
    _stt_lock: asyncio.Lock
    _partial_last: str

    # --- iniciativa/agenda ---
    _proactive_task: asyncio.Task[None] | None
    _memory_task: asyncio.Task[None] | None
    _briefing_task: asyncio.Task[None] | None
    _briefing_last_attempt: float
    _market_last_attempt: float
    _proactive_count: int
    _proactive_day: date
    _last_proactive: float
    _last_idle_message: str
    _initiative_session_id: str | None
    _task_session_id: str | None
    _zh_session_id: str | None
    _zh_cache: dict[str, str]
    _tasks_next_check: float
    _task_page_updated_at: str | None
    _agenda_pending_names: list[str]
    _agenda_next_check: float
    _agenda_seen: set[str]
    _agenda_day: date | None

    # --- fala/streaming ---
    _tts_lock: asyncio.Lock
    _user_name_cache: str | None
    _warmup_task: asyncio.Task[None] | None
    _speech_active: bool
    _speech_buffer: str
    _speech_pending: str
    _speech_queue: asyncio.Queue[str | None] | None
    _speech_task: asyncio.Task[None] | None
    _speech_fed_chars: int
    _answer_buffer: str
    _session_dirty: bool
    _last_assistant_message_id: str | None
    _transition_lock: asyncio.Lock
    _resume_context: str
    _tool_scope: set[str]

    # --- memoria diaria ---
    @abstractmethod
    async def consolidate_day(
        self,
        day: date | None = None,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> str | None:
        ...

    @abstractmethod
    async def _maybe_consolidate(self, now: datetime) -> None:
        ...

    @abstractmethod
    async def _handle_consolidate(self, argument: str) -> None:
        ...

    # --- metodos que um mixin chama de outro (implementados nos mixins) ---
    @abstractmethod
    async def _speak(self, text: str) -> None:
        ...

    @abstractmethod
    def _start_speech_streaming(self) -> bool:
        ...

    @abstractmethod
    def _feed_speech(self, text: str) -> None:
        ...

    @abstractmethod
    def _feed_display(self, delta: str) -> list[str]:
        ...

    @abstractmethod
    def _flush_display(self) -> list[str]:
        ...

    @abstractmethod
    async def _finish_speech_streaming(self, fallback: str) -> None:
        ...

    @abstractmethod
    async def _stop_speech_streaming(self) -> None:
        ...

    @abstractmethod
    async def _dispatch(self, text: str) -> str:
        ...

    @abstractmethod
    async def _handle_task_impl(self, text: str, speak: bool = True) -> str:
        ...

    @abstractmethod
    async def _handle_command(self, text: str) -> bool:
        ...

    @abstractmethod
    async def _new_session(self) -> None:
        ...

    @abstractmethod
    async def _show_dashboard(self) -> None:
        ...

    @abstractmethod
    async def _switch_mode(self, argument: str) -> None:
        ...

    @abstractmethod
    def _preferences_block(self) -> str:
        ...

    @abstractmethod
    async def _handle_preference(self, text: str) -> None:
        ...

    @abstractmethod
    async def _handle_register(self, intent: RegisterIntent, original: str) -> None:
        ...

    @abstractmethod
    def _mark_activity(self) -> None:
        ...

    @abstractmethod
    def _user_name(self) -> str:
        ...

    @abstractmethod
    async def _handle_reminder(self, spec: ReminderSpec) -> None:
        ...

    @abstractmethod
    def _toggle_continuous(self, argument: str) -> None:
        ...

    @abstractmethod
    def _toggle_proactive(self, argument: str) -> None:
        ...

    @abstractmethod
    async def _proactive_loop(self, greet: bool = True) -> None:
        ...

    @abstractmethod
    async def _check_focus(self, now: datetime) -> None:
        ...

    @abstractmethod
    async def _fire_due_reminders(self) -> None:
        ...

    @abstractmethod
    async def _startup_greeting(self) -> None:
        ...

    @abstractmethod
    async def _continuous_loop(self) -> bool:
        ...

    @abstractmethod
    async def _read_user_input(self) -> str | None:
        ...

    @abstractmethod
    async def _clear_stale_session(self, session_id: str | None = None) -> None:
        ...

    @abstractmethod
    def _take_resume_context(self) -> str:
        ...

    @abstractmethod
    async def select_mode(self, key: str) -> None:
        ...
