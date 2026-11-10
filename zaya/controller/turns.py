"""Execucao de turnos: roteamento de intencoes e conversa com o OpenCode."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
import uuid
from typing import Any

from zaya.controller.contract import OrchestratorContract
from zaya.controller.goals import goal_block
from zaya.controller.intents import (
    format_context,
    needs_second_brain_context,
    parse_preference,
    parse_register,
)
from zaya.controller.modes import parse_mode_switch, persona_block
from zaya.controller.proactive import parse_reminder
from zaya.controller.smalltalk import fast_reply
from zaya.opencode.client import OpenCodeError
from zaya.opencode.events import (
    assistant_text,
    event_payload,
    event_session_id,
    event_type,
    message_error,
    message_id,
    structured_error_text,
    text_delta,
    tool_activity,
    tool_detail,
    tool_diff,
)
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
        """Small talk responde localmente, sem abrir turno no OpenCode."""
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
            context = format_context(hits, max_chars=self.cfg.secondbrain.context_chars)
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
        system_parts = [persona_block(self.persona)]
        preferences = self._preferences_block()
        if preferences:
            system_parts.append(preferences)
        if self.cfg.interface.concise:
            system_parts.append(
                "[Diretriz da Zaya: seja conciso e direto; "
                "use ferramentas apenas quando necessário.]"
            )
        if self.cfg.interface.concise and self._is_short_prompt(text):
            system_parts.append(
                "[Pedido curto: vá direto à resposta, sem raciocínio longo "
                "nem exploração desnecessária.]"
            )
        if fast:
            system_parts.append(
                "[Modo rápido: responda em no máximo 3 frases, sem rodeios nem "
                "contexto extra.]"
            )
        system = "\n\n---\n\n".join(system_parts)

        streaming = self._start_speech_streaming() if speak else False
        try:
            answer = await self._run_turn(prompt, system=system, session_id=session_id)
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
        except OpenCodeError as exc:
            self._session_dirty = True
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
        self, text: str, system: str | None = None, session_id: str | None = None
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

        turn_task = asyncio.create_task(self._turn_impl(text, system, sid))
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
        self, text: str, system: str | None = None, session_id: str | None = None
    ) -> str:
        assert self.conversation_id is not None
        sid = session_id or self.session_id
        before_id = self._last_assistant_message_id

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        stop = asyncio.Event()

        async def pump() -> None:
            try:
                async for event in self.client.stream_events(stop=stop):
                    if event_session_id(event) == sid:
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
                model=self.client.model,
                system=system,
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
        seen_text: set[str] = set()
        seen_reasoning: set[str] = set()
        collected: list[str] = []
        seen_tools: set[str] = set()
        tool_names: dict[str, str] = {}
        tool_inputs: dict[str, dict[str, Any]] = {}
        wrote_stream = False
        saw_activity = False
        saw_busy = False
        tool_running = False
        self._answer_buffer = ""

        def emit_text(delta: str, assistant_message_id: str | None = None) -> None:
            nonlocal wrote_stream
            if not delta:
                return
            if assistant_message_id:
                self._last_assistant_message_id = str(assistant_message_id)
            collected.append(delta)
            for chunk in self._feed_display(delta):
                if not wrote_stream:
                    self.ui.begin_stream()
                    wrote_stream = True
                self.ui.token(chunk)
                if self._stream_sink is not None:
                    self._stream_sink.token(chunk)
            self._feed_speech(delta)

        def emit_tool(call_id: str, tool: str, detail: str) -> None:
            if call_id in seen_tools:
                return
            seen_tools.add(call_id)
            if not wrote_stream:
                label = TOOL_LABELS.get(tool, tool)
                self.ui.tool(label, detail)
                if self._stream_sink is not None:
                    self._stream_sink.tool(label, detail)

        def emit_diff(call_id: str, tool: str, state: dict[str, Any]) -> None:
            if not self.cfg.interface.show_diffs:
                return
            part = {"type": "tool", "id": call_id, "name": tool, "state": state}
            diff = tool_diff(part)
            if diff:
                filename, patch = diff
                self.ui.diff(filename, patch)
                if self._stream_sink is not None:
                    self._stream_sink.diff(filename, patch)

        try:
            while True:
                elapsed = time.monotonic() - started
                if elapsed > self.cfg.opencode.request_timeout:
                    self.ui.warning("tempo limite da execução atingido")
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.5)
                except TimeoutError:
                    event = None

                if event is None:
                    try:
                        status = await self.client.session_status(self.session_id)
                    except OpenCodeError:
                        status = None
                    if status and not (
                        isinstance(status, dict) and status.get("type") == "idle"
                    ):
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
                is_v2 = "data" in event and "properties" not in event
                props = event_payload(event)

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
                        emit_text(delta, part.get("messageID"))
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
                            emit_tool(call_id, tool, detail)
                        if tstatus in {"completed", "error"}:
                            emit_diff(
                                str(part.get("callID") or part.get("id") or ""),
                                str(part.get("tool") or part.get("name") or "tool"),
                                state,
                            )
                elif kind == "session.text.started":
                    saw_activity = True
                elif kind == "session.text.delta":
                    saw_activity = True
                    assistant_id = props.get("assistantMessageID")
                    if ignore_message_id and str(assistant_id) == ignore_message_id:
                        continue
                    key = f"{assistant_id}:{props.get('ordinal', 0)}"
                    seen_text.add(key)
                    emit_text(str(props.get("delta") or ""), assistant_id)
                elif kind == "session.text.ended":
                    saw_activity = True
                    assistant_id = props.get("assistantMessageID")
                    key = f"{assistant_id}:{props.get('ordinal', 0)}"
                    if key not in seen_text:
                        seen_text.add(key)
                        emit_text(str(props.get("text") or ""), assistant_id)
                elif kind == "session.reasoning.started":
                    saw_activity = True
                elif kind == "session.reasoning.delta":
                    saw_activity = True
                    if not self.cfg.interface.show_reasoning:
                        continue
                    assistant_id = props.get("assistantMessageID")
                    key = f"{assistant_id}:{props.get('ordinal', 0)}"
                    seen_reasoning.add(key)
                    current = last_reasoning.get(key, "") + str(props.get("delta") or "")
                    last_reasoning[key] = current
                    if current:
                        self.ui.reasoning(current)
                        if self._stream_sink is not None:
                            self._stream_sink.reasoning(current)
                elif kind == "session.reasoning.ended":
                    saw_activity = True
                    if not self.cfg.interface.show_reasoning:
                        continue
                    assistant_id = props.get("assistantMessageID")
                    key = f"{assistant_id}:{props.get('ordinal', 0)}"
                    if key not in seen_reasoning:
                        current = str(props.get("text") or "")
                        if current:
                            self.ui.reasoning(current)
                            if self._stream_sink is not None:
                                self._stream_sink.reasoning(current)
                elif kind == "session.tool.input.started":
                    saw_activity = True
                    call_id = str(props.get("id") or "")
                    if call_id:
                        tool_names[call_id] = str(props.get("name") or "tool")
                elif kind == "session.tool.called":
                    saw_activity = True
                    tool_running = True
                    call_id = str(props.get("id") or "")
                    tool = tool_names.get(call_id, "tool")
                    payload = props.get("input")
                    if isinstance(payload, dict):
                        tool_inputs[call_id] = payload
                    emit_tool(call_id, tool, tool_detail(payload))
                elif kind == "session.tool.progress":
                    saw_activity = True
                elif kind in {"session.tool.success", "session.tool.failed"}:
                    saw_activity = True
                    tool_running = False
                    call_id = str(props.get("id") or "")
                    tool = tool_names.get(call_id, "tool")
                    metadata = props.get("metadata")
                    tool_state: dict[str, Any] = {
                        "status": "completed" if kind.endswith("success") else "error",
                        "input": tool_inputs.get(call_id, {}),
                        "metadata": metadata if isinstance(metadata, dict) else {},
                    }
                    if kind.endswith("failed"):
                        tool_state["error"] = props.get("error")
                    emit_diff(call_id, tool, tool_state)
                elif kind == "session.execution.failed":
                    error = structured_error_text(props.get("error"))
                    raise OpenCodeError(f"erro na sessão OpenCode: {error}")
                elif kind in {
                    "session.execution.succeeded",
                    "session.execution.interrupted",
                    "session.idle",
                }:
                    saw_activity = True
                    break
                elif kind == "session.error":
                    error = structured_error_text(props.get("error"))
                    raise OpenCodeError(f"erro na sessão OpenCode: {error}")
                elif kind in {"permission.asked", "permission.v2.asked"}:
                    permission_kind = "permission.v2.asked" if is_v2 else kind
                    await self._handle_permission(props, permission_kind)
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
                if message_id(message) != before_id:
                    error = message_error(message)
                    if error:
                        raise OpenCodeError(f"O agente retornou erro: {error}")
                    text = assistant_text(message)
                    if text:
                        current_id = message_id(message)
                        if current_id:
                            self._last_assistant_message_id = current_id
                        return text
            await asyncio.sleep(0.2)
        return ""
