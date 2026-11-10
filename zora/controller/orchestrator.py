"""Orquestrador da Zaia: voz -> STT -> contexto -> OpenCode -> resposta/voz.

O comportamento concreto vive em mixins coesos (fala, turnos, voz, comandos,
registro e iniciativa). Esta classe mantem o estado compartilhado, o ciclo de
vida (bootstrap/shutdown) e a sessao/conversa ativa.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from typing import Any

from zora.agenda.client import AgendaClient
from zora.audio.recorder import ContinuousListener, Recorder
from zora.config import Config
from zora.controller.commands import CommandsMixin
from zora.controller.contract import StreamSink
from zora.controller.goals import GoalTracker
from zora.controller.initiative import InitiativeMixin
from zora.controller.memory import MemoryMixin
from zora.controller.modes import PERSONAS, Persona, get_persona, resolve_mode
from zora.controller.registration import RegistrationMixin
from zora.controller.speech import SpeechMixin
from zora.controller.turns import TurnMixin
from zora.controller.voice import VoiceMixin
from zora.finance.watchlist import Watchlist
from zora.focus import FocusStore
from zora.interface.terminal import TerminalUI
from zora.memory.store import MemoryStore
from zora.opencode.client import OpenCodeClient, OpenCodeError
from zora.opencode.events import message_id
from zora.secondbrain.index import SecondBrainIndex
from zora.secondbrain.notes import SecondBrain
from zora.stt.faster_whisper import WhisperTranscriber
from zora.tasks.store import TaskStore
from zora.tts.manager import TTSManager


class Orchestrator(
    SpeechMixin,
    TurnMixin,
    VoiceMixin,
    CommandsMixin,
    MemoryMixin,
    RegistrationMixin,
    InitiativeMixin,
):

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
        self._restart_requested = False
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
        self._memory_task: asyncio.Task[None] | None = None
        self._proactive_count = 0
        self._proactive_day = datetime.now().astimezone().date()
        self._last_proactive = 0.0
        self._last_idle_message = ""
        self._initiative_session_id: str | None = None
        self._task_session_id: str | None = None
        self._zh_session_id: str | None = None
        self._zh_cache: dict[str, str] = {}
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
        self._session_dirty = False
        self._last_assistant_message_id: str | None = None
        # Serializa troca de modo/conversa/sessao. Sem isso, dois workers do TUI
        # podem intercalar os awaits e gravar a sessao de um modo na conversa
        # de outro.
        self._transition_lock = asyncio.Lock()
        # Historico local reinjetado no proximo turno quando a sessao antiga
        # nao existe mais (a conversa continua, o contexto do provedor nao).
        self._resume_context = ""

    async def _bootstrap(self) -> None:
        await self.client.ensure_server()
        if self.cfg.opencode.auto_approve:
            self.ui.warning(
                "auto-aprovação de ferramentas do OpenCode está ATIVA (opencode.auto_approve=true)"
            )
        self._prepare_conversation()
        assert self.conversation_id is not None
        await self._activate_conversation(self.conversation_id, self.persona)
        await self._seed_last_message_id()
        self._refresh_index_on_start()
        if self._voice_available():
            self._warmup_task = asyncio.create_task(self._warmup_stt())

    async def _warmup_stt(self) -> None:
        try:
            await asyncio.to_thread(self.transcriber.warmup)
        except Exception as exc:  # pragma: no cover
            self.logger.debug("warmup do STT falhou: %s", exc)

    async def _shutdown(self) -> None:
        if self._memory_task is not None:
            if not self._memory_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._memory_task), timeout=15.0
                    )
                except Exception:
                    self._memory_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await self._memory_task
            self._memory_task = None
        if (
            self.cfg.memory.enabled
            and self.cfg.memory.on_exit
            and datetime.now().astimezone().strftime("%H:%M") >= self.cfg.memory.at
        ):
            try:
                await asyncio.wait_for(
                    self.consolidate_day(
                        datetime.now().astimezone().date(), timeout_seconds=20.0
                    ),
                    timeout=25.0,
                )
            except Exception as exc:  # pragma: no cover - depende do provedor
                self.logger.warning("consolidacao ao sair falhou: %s", exc)
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
        if not close:
            self._start_memory_worker()
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

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    async def prepare(self) -> None:
        """Sobe servidor/sessão antes do primeiro uso (aquece a TUI)."""
        if not self.session_id:
            await self._bootstrap()
        self._start_memory_worker()

    async def answer_stream(self, text: str, sink: StreamSink | None) -> str:
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
                self.logger.warning("painel de metas: %s", exc, exc_info=True)

        if self.cfg.proactive.enabled and self.cfg.proactive.startup_greeting:
            try:
                await self._startup_greeting()
            except Exception as exc:  # pragma: no cover
                self.logger.warning("falha na saudação inicial: %s", exc, exc_info=True)

        if self.cfg.proactive.enabled:
            self._proactive_task = asyncio.create_task(self._proactive_loop(greet=False))

        self._start_memory_worker()

        try:
            await self._check_focus(datetime.now().astimezone())
        except Exception as exc:  # pragma: no cover
            self.logger.warning("falha ao checar foco: %s", exc, exc_info=True)

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
        conv = self.store.get_or_create_conversation(
            str(self.directory), self.persona.key
        )
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
            self.logger.warning("falha ao indexar Second Brain: %s", exc, exc_info=True)
            self.ui.warning(f"não foi possível indexar o Second Brain: {exc}")

    async def _open_session(
        self, conversation_id: int, persona: Persona
    ) -> tuple[str, bool]:
        """Retoma a sessao da conversa ou cria uma nova.

        Devolve ``(session_id, criada)``. O segundo item diz se a sessao
        anterior sumiu (ou nunca existiu), para que o chamador possa reinjetar
        o historico local no proximo turno.
        """
        conv = self.store.get_conversation(conversation_id)
        sid = conv["opencode_session_id"]
        if self.cfg.opencode.resume_session and sid:
            existing = await self.client.get_session(sid)
            if existing:
                return sid, False
            self.logger.warning(
                "sessao %s da conversa %s nao existe mais; abrindo nova",
                sid,
                conversation_id,
            )
        session = await self.client.create_session(
            title=f"Zaia — {self.directory.name} · {persona.name}",
            model=self.client.model,
        )
        self.store.set_session(conversation_id, session.id)
        return session.id, True

    async def _ensure_session(self) -> str:
        """Mantido para compatibilidade; le o estado atual."""
        assert self.conversation_id is not None
        sid, _created = await self._open_session(self.conversation_id, self.persona)
        return sid

    async def _activate_conversation(
        self,
        conversation_id: int,
        persona: Persona,
        *,
        seed_history: bool = True,
    ) -> None:
        """Troca conversa+persona+sessao como uma unica operacao.

        Tudo o que depende de await acontece antes; o estado compartilhado so e
        alterado no fim, já com a sessao certa. Assim duas trocas simultaneas
        nao se cruzam.
        """
        async with self._transition_lock:
            sid, created = await self._open_session(conversation_id, persona)
            if created and seed_history:
                context = self._history_context(conversation_id)
                if context:
                    self._resume_context = context
                    self.logger.info(
                        "historico da conversa %s sera reinjetado no proximo turno",
                        conversation_id,
                    )
            self.persona = persona
            self.conversation_id = conversation_id
            self.session_id = sid
            self._last_assistant_message_id = None
            self.store.set_meta("mode", persona.key)
            await self._clear_stale_session(sid)

    def _history_context(self, conversation_id: int, limit: int = 24) -> str:
        """Resume os ultimos turnos locais para reconstruir o contexto."""
        lines: list[str] = []
        for row in self.store.turns(conversation_id, limit=limit):
            role = str(row["role"])
            if role not in {"user", "assistant"}:
                continue
            text = " ".join(str(row["text"]).split())
            if not text:
                continue
            if len(text) > 400:
                text = text[:400].rstrip() + "…"
            lines.append(f"{'Talita' if role == 'user' else 'Zaia'}: {text}")
        if not lines:
            return ""
        body = "\n".join(lines)
        if len(body) > 6000:
            body = body[-6000:].split("\n", 1)[-1]
        return (
            "[Contexto de continuidade — histórico local recuperado porque a "
            "sessão anterior não estava disponível. Use apenas quando for "
            "relevante.]\n" + body
        )

    def _take_resume_context(self) -> str:
        context, self._resume_context = self._resume_context, ""
        return context

    async def _clear_stale_session(self, session_id: str | None = None) -> None:
        """Aborta uma execucao presa (sessao em busy) antes de iniciar outra."""
        sid = session_id or self.session_id
        if not sid:
            return
        try:
            status = await self.client.session_status(sid)
        except OpenCodeError:
            return
        if status and isinstance(status, dict) and status.get("type") != "idle":
            self.logger.warning("sessao presa (%s); abortando antes do proximo turno", status)
            self.ui.warning("liberando uma execução anterior presa…")
            await self.client.abort(sid)
            await asyncio.sleep(0.2)

    async def _seed_last_message_id(self) -> None:
        """Cacheia o id da ultima resposta para evitar um GET a cada turno."""
        try:
            message = await self.client.latest_assistant(self.session_id)
        except OpenCodeError:
            return
        current_id = message_id(message) if message else None
        if current_id:
            self._last_assistant_message_id = current_id

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

    def modes(self) -> list[Persona]:
        return list(PERSONAS.values())

    @property
    def mode_key(self) -> str:
        return self.persona.key

    @property
    def mode_name(self) -> str:
        return self.persona.name

    def sessions(self) -> list[dict[str, Any]]:
        rows = self.store.list_conversations(str(self.directory), self.persona.key)
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "id": int(row["id"]),
                    "title": row["title"] or "Sem título",
                    "updated_at": row["updated_at"],
                    "turns": int(row["turn_count"]),
                    "session_id": row["opencode_session_id"] or "",
                    "current": int(row["id"]) == self.conversation_id,
                }
            )
        return result

    def history(self) -> list[tuple[str, str]]:
        if self.conversation_id is None:
            return []
        return [
            (row["role"], row["text"])
            for row in self.store.turns(self.conversation_id, limit=200)
        ]

    async def select_mode(self, key: str) -> None:
        persona = get_persona(key)
        if persona is None:
            return
        conv = self.store.get_or_create_conversation(str(self.directory), persona.key)
        await self._activate_conversation(int(conv["id"]), persona)

    async def select_conversation(self, conversation_id: int) -> None:
        try:
            conv = self.store.get_conversation(conversation_id)
        except KeyError:
            return
        persona = resolve_mode(conv["mode"])
        self.store.set_current(conversation_id)
        await self._activate_conversation(conversation_id, persona)

    async def new_session(self) -> None:
        await self._new_session()

    def mark_session_dirty(self) -> None:
        """Pede uma checagem de sessão presa no próximo turno (ex.: após abortar)."""
        self._session_dirty = True
