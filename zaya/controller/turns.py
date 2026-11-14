"""Execucao de turnos: roteamento de intencoes e conversa com o agente."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
import uuid
from datetime import datetime
from typing import Any

from zaya.agent.errors import AgentError
from zaya.agent.events import (
    PermissionAsked,
    ReasoningDelta,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from zaya.agent.providers.factory import normalize_provider
from zaya.agent.types import ModelRef
from zaya.controller.contract import OrchestratorContract
from zaya.controller.goals import goal_block
from zaya.controller.intents import (
    format_context,
    needs_second_brain_context,
    parse_preference,
    parse_register,
)
from zaya.controller.modes import parse_mode_switch
from zaya.controller.proactive import parse_reminder
from zaya.controller.prompt import (
    compose_system,
    route_model,
)
from zaya.controller.smalltalk import fast_reply
from zaya.spotify.controller import SpotifyCommand, SpotifyError
from zaya.spotify.controller import execute as execute_spotify
from zaya.spotify.intents import parse_spotify_command


class TurnInterrupted(Exception):
    """Interrupcao solicitada pelo usuario durante um turno."""


TOOL_LABELS = {
    "read": "lendo",
    "edit": "editando",
    "write": "escrevendo",
    "bash": "executando",
    "shell": "executando",
    "grep": "buscando",
    "glob": "listando",
    "webfetch": "consultando web",
    "task": "delegando",
    "todowrite": "planejando",
}


class TurnMixin(OrchestratorContract):
    async def _dispatch_silent(self, text: str) -> str:
        quick = self._quick_reply(text)
        if quick is not None:
            return await self._emit_quick_reply(quick, speak=False)
        mode = parse_mode_switch(text)
        if mode:
            await self._switch_mode(mode)
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

    async def _dispatch(self, text: str) -> str:
        quick = self._quick_reply(text)
        if quick is not None:
            return await self._emit_quick_reply(quick, speak=True)
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

    def _quick_reply(self, text: str) -> str | None:
        """Small talk responde localmente, sem abrir turno no modelo."""
        if not self.cfg.interface.fast_smalltalk:
            return None
        return fast_reply(text, name=self._user_name())

    @staticmethod
    def _is_short_prompt(text: str) -> bool:
        """Pedidos curtos ganham um empurrão para o modelo não divagar."""
        return "\n" not in text and len(text.split()) <= 10

    async def _emit_quick_reply(self, text: str, speak: bool) -> str:
        assert self.conversation_id is not None
        self.store.add_turn(self.conversation_id, "assistant", text)
        if speak:
            self.ui.print_markdown(text)
            await self._speak(text)
        return text

    async def _handle_spotify(self, command: SpotifyCommand, speak: bool) -> str:
        try:
            message = await asyncio.to_thread(execute_spotify, command)
        except SpotifyError as exc:
            message = f"Spotify: {exc}"
        self.ui.info(message)
        if speak:
            await self._speak(message)
        return message

    async def _handle_task(self, text: str) -> str:
        self._busy = True
        try:
            return await self._handle_task_impl(text)
        finally:
            self._busy = False

    async def _handle_task_impl(self, text: str, speak: bool = True) -> str:
        mode = parse_mode_switch(text)
        if mode:
            await self._switch_mode(mode)
            message = f"Modo {self.persona.name} ativado."
            if speak:
                await self._speak(message)
            return message
        assert self.conversation_id is not None
        self._last_error = ""
        if self._session_dirty:
            await self._clear_stale_session()
            self._session_dirty = False
        # Fotografa o par conversa+sessao: uma troca de modo no meio do turno
        # nao pode desviar o prompt nem gravar a resposta na conversa nova.
        conversation_id = self.conversation_id
        session_id = self.session_id
        self.ui.set_status("◐ pensando…")
        fast = self.cfg.interface.fast
        hits = []
        if (
            not fast
            and self.cfg.secondbrain.auto_context
            and needs_second_brain_context(text)
        ):
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
                    conversation_id,
                    "context",
                    "Notas usadas: " + ", ".join(hit.rel_path for hit in hits),
                )

        prompt = text
        resume = self._take_resume_context()
        if resume:
            prompt = f"{resume}\n\n---\n\n{prompt}"
        if hits:
            context = format_context(
                hits,
                max_chars=self.cfg.secondbrain.context_chars,
                max_total_chars=self.cfg.secondbrain.context_max_chars,
                max_items=self.cfg.secondbrain.context_results,
            )
            prompt = (
                f"{context}\n\n---\n\n"
                f"Pedido do usuário: {text}\n\n"
                "Responda em português do Brasil, de forma concisa e direta. "
                "Use o contexto acima somente quando for relevante. "
                "Não leia nem edite arquivos do Second Brain para esta resposta: "
                "o contexto já foi fornecido pela Zaya."
            )
        if not fast:
            goals = self.goals.detect(text)
            if goals:
                prompt = goal_block(goals) + "\n\n---\n\n" + prompt
                self.store.add_turn(
                    conversation_id,
                    "context",
                    "Metas relacionadas: " + ", ".join(goal.label for goal in goals),
                )

        composed = compose_system(
            self.persona,
            self._preferences_block(),
            concise=self.cfg.interface.concise,
            short_prompt=self._is_short_prompt(text),
            fast=fast,
            now=datetime.now().astimezone(),
        )
        model = route_model(
            text,
            self.cfg.agent.fast_model,
            normalize_provider(self.cfg.agent.provider),
        )
        # Sem filtro por turno: ``None`` significa "todo o catalogo", que o
        # runtime resolve para as ferramentas registradas. A capacidade nao
        # varia por modo nem por verbo de acao.
        tool_names = None
        # Tudo que muda por turno (hora, pedido curto, modo rápido) vai no fim
        # da mensagem do usuário. O system fica 100% estável para o cache.
        if composed.dynamic:
            prompt = (
                f"{prompt}\n\n---\n\n"
                "[Contexto do turno — não é um pedido]\n"
                f"{composed.dynamic}"
            )

        streaming = self._start_speech_streaming() if speak else False
        try:
            answer = await self._run_turn(
                prompt,
                system=composed.static,
                session_id=session_id,
                cache_prefix=composed.static,
                model=model,
                tool_names=tool_names,
                history_text=text,
            )
        except TurnInterrupted:
            self._session_dirty = True
            self.ui.clear_status()
            self.ui.warning("execução interrompida; a sessão foi preservada")
            await self._stop_speech_streaming()
            return ""
        except (KeyboardInterrupt, asyncio.CancelledError):
            self._session_dirty = True
            self.ui.clear_status()
            await self.client.abort(session_id)
            self.tts.stop()
            self.ui.warning("execução interrompida")
            await self._stop_speech_streaming()
            return ""
        except AgentError as exc:
            self._session_dirty = True
            self._last_error = str(exc)
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

        self.store.add_turn(conversation_id, "assistant", answer)
        if streaming:
            self.ui.set_status("▶ falando…")
            await self._finish_speech_streaming(answer)
        elif speak:
            if self.tts.enabled:
                self.ui.set_status("▶ falando…")
            await self._speak(answer)
        self.ui.clear_status()
        return answer

    async def _run_turn(
        self,
        text: str,
        system: str | None = None,
        session_id: str | None = None,
        *,
        cache_prefix: str = "",
        model: ModelRef | None = None,
        tool_names: tuple[str, ...] | None = None,
        history_text: str | None = None,
    ) -> str:
        """Executa o turno com Ctrl+C escopado: aborta e devolve o controle."""
        sid = session_id or self.session_id
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

        turn_task = asyncio.create_task(
            self._turn_impl(
                text,
                system,
                sid,
                cache_prefix=cache_prefix,
                model=model,
                tool_names=tool_names,
                history_text=history_text,
            )
        )
        interrupt_task = asyncio.create_task(interrupt.wait())
        try:
            done, _pending = await asyncio.wait(
                {turn_task, interrupt_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if turn_task in done:
                return turn_task.result()

            self.tts.stop()
            await self.client.abort(sid)
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

    async def _turn_impl(
        self,
        text: str,
        system: str | None = None,
        session_id: str | None = None,
        *,
        cache_prefix: str = "",
        model: ModelRef | None = None,
        tool_names: tuple[str, ...] | None = None,
        history_text: str | None = None,
    ) -> str:
        assert self.conversation_id is not None
        sid = session_id or self.session_id

        queue: asyncio.Queue[Any] = asyncio.Queue()
        stop = asyncio.Event()

        async def pump() -> None:
            try:
                async for event in self.client.stream_events(stop=stop):
                    if getattr(event, "session_id", "") == sid:
                        await queue.put(event)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # pragma: no cover
                self.logger.warning("stream de eventos caiu: %s", exc, exc_info=True)

        pump_task = asyncio.create_task(pump())
        user_message_id = "msg_z" + uuid.uuid4().hex[:24]
        try:
            await self.client.prompt_async(
                sid,
                text,
                model=model,
                system=system,
                message_id=user_message_id,
                cache_prefix=cache_prefix,
                tool_names=tool_names,
                history_text=history_text,
            )
            return await self._consume_events(queue, ignore_message_id=user_message_id)
        finally:
            stop.set()
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump_task

    async def _consume_events(
        self,
        queue: asyncio.Queue[Any],
        ignore_message_id: str | None = None,
    ) -> str:
        started = time.monotonic()
        last_event_ts = started
        collected: list[str] = []
        reasoning_text = ""
        wrote_stream = False
        tool_running = False
        self._answer_buffer = ""

        def emit_text(delta: str) -> None:
            nonlocal wrote_stream
            if not delta:
                return
            collected.append(delta)
            for chunk in self._feed_display(delta):
                if not wrote_stream:
                    self.ui.begin_stream()
                    wrote_stream = True
                self.ui.token(chunk)
                if self._stream_sink is not None:
                    self._stream_sink.token(chunk)
            self._feed_speech(delta)

        try:
            while True:
                elapsed = time.monotonic() - started
                if elapsed > self.cfg.agent.request_timeout:
                    self.ui.warning("tempo limite da execução atingido")
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.5)
                except TimeoutError:
                    if (
                        not tool_running
                        and time.monotonic() - last_event_ts
                        > self.cfg.agent.stall_timeout
                    ):
                        self.ui.warning("o agente parou de responder; abortando a execução")
                        await self.client.abort(self.session_id)
                        raise AgentError(
                            "o agente ficou sem responder (timeout de inatividade)"
                        ) from None
                    continue

                last_event_ts = time.monotonic()
                if isinstance(event, TurnStarted):
                    continue
                if isinstance(event, TextDelta):
                    if ignore_message_id and event.message_id == ignore_message_id:
                        continue
                    emit_text(event.text)
                elif isinstance(event, ReasoningDelta):
                    if not self.cfg.interface.show_reasoning:
                        continue
                    reasoning_text += event.text
                    self.ui.reasoning(reasoning_text)
                    if self._stream_sink is not None:
                        self._stream_sink.reasoning(reasoning_text)
                elif isinstance(event, ToolStarted):
                    tool_running = True
                    label = TOOL_LABELS.get(event.name, event.name)
                    self.ui.tool(label, event.detail)
                    if self._stream_sink is not None:
                        self._stream_sink.tool(label, event.detail)
                elif isinstance(event, ToolFinished):
                    tool_running = False
                    if event.diff and self.cfg.interface.show_diffs:
                        filename = event.diff_name or "arquivo"
                        self.ui.diff(filename, event.diff)
                        if self._stream_sink is not None:
                            self._stream_sink.diff(filename, event.diff)
                    if event.status == "error" and event.error:
                        self.logger.debug(
                            "ferramenta %s falhou: %s", event.name, event.error
                        )
                elif isinstance(event, PermissionAsked):
                    await self._handle_permission(event)
                elif isinstance(event, TurnCompleted):
                    if event.message_id:
                        self._last_assistant_message_id = event.message_id
                    if event.usage:
                        self.logger.debug("tokens do turno: %s", event.usage)
                    if not collected and event.text:
                        emit_text(event.text)
                    break
                elif isinstance(event, TurnFailed):
                    raise AgentError(event.message)
                elif isinstance(event, TurnCancelled):
                    break
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

    async def _handle_permission(self, event: PermissionAsked) -> None:
        if not event.request_id:
            return
        if self.cfg.agent.auto_approve:
            answer = "once"
            self.logger.info(
                "auto-aprovando permissao %s (%s)", event.request_id, event.tool
            )
        else:
            self._permission_active = True
            try:
                answer = await asyncio.to_thread(
                    self.ui.permission, event.tool, list(event.resources)
                )
            finally:
                self._permission_active = False
        try:
            await self.client.reply_permission(
                self.session_id, event.request_id, answer
            )
        except (AgentError, ValueError) as exc:
            self.logger.warning(
                "falha ao responder permissao %s: %s", event.request_id, exc
            )
            self.ui.warning(
                "não foi possível responder à permissão (ela pode ter expirado)."
            )
