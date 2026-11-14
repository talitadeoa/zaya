"""Motor do agente independente: sessoes, loop de ferramentas e eventos.

O ``AgentClient`` e o unico ponto que o resto da Zaya conhece. Ele fala com um
``Provider`` (OpenAI-compatible ou Anthropic), executa ferramentas locais e
publica eventos tipados consumidos pela interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from zaya.agent.errors import AgentCancelled, AgentError, ToolError
from zaya.agent.events import (
    Event,
    PermissionAsked,
    ToolFinished,
    ToolStarted,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from zaya.agent.events import (
    ReasoningDelta as EventReasoning,
)
from zaya.agent.events import (
    TextDelta as EventText,
)
from zaya.agent.providers import (
    PRESETS,
    ChatRequest,
    Finish,
    Provider,
    ProviderError,
    ReasoningDelta,
    TextDelta,
    ToolCallDelta,
    Usage,
    UsageDelta,
    build_provider,
    describe_provider,
    normalize_provider,
    resolve_api_key,
)
from zaya.agent.session import AgentSession, SessionStore
from zaya.agent.tools import ToolContext, ToolResult, ToolSet, build_tools
from zaya.agent.types import (
    AssistantMessage,
    ChatMessage,
    ModelRef,
    SessionInfo,
    ToolCall,
)
from zaya.config import Config


class AgentClient:
    def __init__(self, cfg: Config, directory: Path | None = None):
        self.cfg = cfg
        self.directory = (directory or cfg.project_directory()).resolve()
        self._provider: Provider | None = None
        self._events: asyncio.Queue[Event] = asyncio.Queue()
        self._sessions: dict[str, AgentSession] = {}
        self._store = SessionStore(cfg.resolved_data_dir() / "sessions")
        self._tool_context = ToolContext(
            self.directory, cfg.agent.allow_outside_project
        )
        self.tools: ToolSet = (
            build_tools(self._tool_context) if cfg.agent.tools else ToolSet()
        )
        self._always_approved: set[str] = set()
        self._logger = logging.getLogger("zaya.agent")

    # ------------------------------------------------------------------ config
    @property
    def endpoint(self) -> str:
        configured = (self.cfg.agent.base_url or "").strip()
        if configured:
            return configured.rstrip("/")
        return describe_provider(self.cfg.agent.provider).get("base_url", "")

    @property
    def model(self) -> ModelRef:
        return ModelRef(
            provider_id=normalize_provider(self.cfg.agent.provider),
            model_id=self.cfg.agent.model,
        )

    def _ensure_provider(self) -> Provider:
        if self._provider is not None:
            return self._provider
        name = normalize_provider(self.cfg.agent.provider)
        preset = PRESETS.get(name)
        if preset and preset.env_keys and not resolve_api_key(
            self.cfg.agent.provider, self.cfg.agent.api_key
        ):
            keys = " ou ".join(("ZAYA_AGENT_API_KEY", *preset.env_keys))
            raise AgentError(
                f"chave de API ausente para {name}: defina {keys} no ambiente/.env"
            )
        self._provider = build_provider(self.cfg.agent)
        return self._provider

    async def ensure_ready(self) -> None:
        """Valida a configuracao e prepara o provedor (sem chamada de rede)."""
        self._ensure_provider()

    async def health(self) -> dict[str, Any] | None:
        try:
            provider = self._ensure_provider()
            info = await asyncio.wait_for(provider.ping(), timeout=8.0)
        except (AgentError, ProviderError, OSError, TimeoutError):
            return None
        return {
            "provider": normalize_provider(self.cfg.agent.provider),
            "model": self.cfg.agent.model,
            "models": info.get("models"),
        }

    async def list_models(self) -> list[str]:
        provider = self._ensure_provider()
        try:
            return await asyncio.wait_for(provider.list_models(), timeout=15.0)
        except TimeoutError as exc:
            raise AgentError("tempo limite ao consultar a lista de modelos") from exc
        except ProviderError as exc:
            raise AgentError(str(exc)) from exc

    # ---------------------------------------------------------------- sessions
    def _session(self, session_id: str) -> AgentSession | None:
        session = self._sessions.get(session_id)
        if session is not None:
            return session
        session = self._store.load(session_id)
        if session is not None:
            self._sessions[session_id] = session
        return session

    def _require(self, session_id: str) -> AgentSession:
        session = self._session(session_id)
        if session is None:
            raise AgentError(f"sessao nao encontrada: {session_id}")
        return session

    def _save(self, session: AgentSession) -> None:
        self._store.save(session)

    async def create_session(
        self, title: str | None = None, model: ModelRef | None = None
    ) -> SessionInfo:
        session_id = f"ses_{secrets.token_hex(12)}"
        session = AgentSession(
            id=session_id,
            title=title or "",
            directory=str(self.directory),
        )
        self._sessions[session_id] = session
        self._save(session)
        return SessionInfo(
            id=session.id,
            title=session.title,
            directory=session.directory,
        )

    async def get_session(self, session_id: str) -> SessionInfo | None:
        session = self._session(session_id)
        if session is None:
            return None
        return SessionInfo(
            id=session.id,
            title=session.title,
            directory=session.directory,
        )

    async def delete_session(self, session_id: str) -> None:
        session = self._session(session_id)
        if session is not None and session.task is not None:
            session.cancel.set()
            session.task.cancel()
        self._sessions.pop(session_id, None)
        self._store.delete(session_id)

    async def set_title(self, session_id: str, title: str) -> None:
        session = self._require(session_id)
        session.title = title
        self._save(session)

    async def list_sessions(self) -> list[SessionInfo]:
        ids = set(self._store.list_ids()) | set(self._sessions)
        result = []
        for session_id in sorted(ids):
            session = self._session(session_id)
            if session is not None:
                result.append(
                    SessionInfo(
                        id=session.id,
                        title=session.title,
                        directory=session.directory,
                    )
                )
        return result

    async def session_status(self, session_id: str) -> dict[str, str]:
        session = self._session(session_id)
        busy = bool(session and session.busy)
        return {"type": "busy" if busy else "idle"}

    async def is_idle(self, session_id: str) -> bool:
        return (await self.session_status(session_id)).get("type") == "idle"

    def usage(self, session_id: str) -> dict[str, Any]:
        """Tokens do ultimo turno, acumulado e historico da sessao."""
        session = self._session(session_id)
        if session is None:
            return {"last": {}, "total": {}}
        return {
            "last": dict(session.usage_last),
            "total": dict(session.usage_total),
            "history": [dict(item) for item in session.usage_history],
        }

    async def abort(self, session_id: str) -> None:
        session = self._session(session_id)
        if session is None:
            return
        session.cancel.set()
        for future in session.permissions.values():
            if not future.done():
                future.set_result("reject")
        session.permissions.clear()
        if session.task is not None and not session.task.done():
            session.task.cancel()

    # ------------------------------------------------------------------ prompts
    async def prompt_async(
        self,
        session_id: str,
        text: str,
        model: ModelRef | None = None,
        agent: str | None = None,
        system: str | None = None,
        message_id: str | None = None,
        cache_prefix: str = "",
        tool_names: tuple[str, ...] | None = None,
        history_text: str | None = None,
    ) -> None:
        session = self._require(session_id)
        self._ensure_provider()
        if session.busy:
            raise AgentError("a sessao ja esta executando; aguarde ou interrompa")
        self._drain_events()
        session.busy = True
        task = asyncio.create_task(
            self._run_turn(
                session,
                text,
                system or "",
                allow_tools=self.cfg.agent.tools,
                emit=self._emit,
                already_busy=True,
                model=model,
                cache_prefix=cache_prefix,
                tool_names=tool_names,
                history_text=history_text,
            )
        )
        session.task = task
        task.add_done_callback(self._consume_task)

    async def prompt_sync(
        self,
        session_id: str,
        text: str,
        model: ModelRef | None = None,
        agent: str | None = None,
        system: str | None = None,
        message_id: str | None = None,
        allow_tools: bool = False,
        cache_prefix: str = "",
        history_text: str | None = None,
    ) -> AssistantMessage:
        session = self._require(session_id)
        self._ensure_provider()
        if session.busy:
            raise AgentError("a sessao ja esta executando")
        session.busy = True
        return await self._run_turn(
            session,
            text,
            system or "",
            allow_tools=allow_tools,
            emit=lambda _event: None,
            already_busy=True,
            model=model,
            cache_prefix=cache_prefix,
            history_text=history_text,
        )

    def _emit(self, event: Event) -> None:
        self._events.put_nowait(event)

    def _drain_events(self) -> None:
        """Descarta eventos pendentes de um turno anterior interrompido."""
        while True:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                return

    @staticmethod
    def _consume_task(task: asyncio.Task) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    TRIM_MARKER = "[... truncado no histórico"

    @classmethod
    def _trim_text(cls, value: str, max_chars: int) -> str:
        if len(value) <= max_chars:
            return value
        if cls.TRIM_MARKER in value[-200:]:
            return value
        original = len(value)
        marker = f"\n\n{cls.TRIM_MARKER}: {original} caracteres]"
        keep = max(0, max_chars - len(marker))
        return value[:keep].rstrip() + marker

    @classmethod
    def _trim_value(cls, value: Any, max_chars: int) -> Any:
        if isinstance(value, str):
            return cls._trim_text(value, max_chars)
        if isinstance(value, list):
            return [cls._trim_value(item, max_chars) for item in value]
        if isinstance(value, dict):
            return {
                key: cls._trim_value(item, max_chars)
                for key, item in value.items()
            }
        return value

    @classmethod
    def _trim_history_payloads(cls, session: AgentSession, max_chars: int) -> None:
        """Enxuga resultados e argumentos grandes de ferramenta no historico.

        O modelo ve tudo no turno em que a ferramenta roda. Depois disso,
        guardar 100 KB de arquivo (no resultado ou no ``write``/``edit``) so
        infla os turnos seguintes.
        """
        if max_chars <= 0:
            return
        for message in session.messages:
            if message.role == "tool":
                message.content = cls._trim_text(message.content, max_chars)
                continue
            if message.role == "assistant" and message.tool_calls:
                for call in message.tool_calls:
                    call.arguments = cls._trim_value(call.arguments, max_chars)

    @staticmethod
    def _record_usage(session: AgentSession, usage: Usage) -> None:
        data = usage.to_dict()
        session.usage_last = data
        if usage.total_tokens or usage.input_tokens or usage.output_tokens:
            session.usage_total = (
                Usage.from_dict(session.usage_total) + usage
            ).to_dict()
            session.usage_history.append(data)
            del session.usage_history[:-50]

    async def _compact_history(self, session: AgentSession) -> Usage:
        """Mantem o historico curto; devolve o custo do resumo (se houve)."""
        limit = self.cfg.agent.max_history_messages
        max_chars = self.cfg.agent.max_history_chars
        if limit <= 0 and max_chars <= 0:
            return Usage()
        # Sessoes antigas podem ter payloads grandes; enxuga antes de medir.
        self._trim_history_payloads(session, self.cfg.agent.tool_result_chars)
        split = session.compaction_split(limit, max_chars)
        if split is None:
            return Usage()
        cutoff, prefix = split
        mode = (self.cfg.agent.history_summary or "local").strip().lower()
        summary = ""
        usage = Usage()
        if mode == "llm":
            try:
                summary, usage = await asyncio.wait_for(
                    self._summarize_history(prefix, session.summary),
                    timeout=20.0,
                )
            except Exception as exc:  # pragma: no cover - depende do provedor
                self._logger.warning(
                    "resumo do histórico via LLM falhou; usando local: %s", exc
                )
        if not summary:
            summary = session.local_summary(
                prefix, session.summary, self.cfg.agent.summary_chars
            )
        if not summary:
            return usage
        if len(summary) > self.cfg.agent.summary_chars:
            summary = (
                summary[: self.cfg.agent.summary_chars].rstrip() + "…"
            )
        session.apply_compaction(cutoff, summary)
        self._logger.debug(
            "historico compactado (%s): %d mensagens, resumo com %d chars",
            mode,
            len(session.messages),
            len(session.summary),
        )
        return usage

    @staticmethod
    def _history_transcript(prefix: list[ChatMessage], limit: int = 6000) -> str:
        lines: list[str] = []
        for message in prefix:
            if message.role not in {"user", "assistant"}:
                continue
            text = " ".join((message.content or "").split())
            if not text:
                continue
            if len(text) > 500:
                text = text[:500].rstrip() + "…"
            speaker = "Talita" if message.role == "user" else "Zaya"
            lines.append(f"{speaker}: {text}")
        body = "\n".join(lines)
        if len(body) > limit:
            body = body[-limit:]
            position = body.find("\n")
            body = body[position + 1 :] if position >= 0 else body
        return body

    def _summary_model(self) -> ModelRef | None:
        value = (self.cfg.agent.history_summary_model or "").strip()
        if not value:
            return None
        if "/" in value:
            return ModelRef.parse(value)
        return ModelRef(
            provider_id=normalize_provider(self.cfg.agent.provider),
            model_id=value,
        )

    async def _summarize_history(
        self, prefix: list[ChatMessage], previous: str
    ) -> tuple[str, Usage]:
        transcript = self._history_transcript(prefix)
        if not transcript:
            return "", Usage()
        limit = self.cfg.agent.summary_chars
        parts = [
            "Resuma a conversa abaixo para servir de memória dos próximos turnos.",
            "Preserve objetivos, decisões, fatos, nomes, números, prazos, "
            "pendências e preferências da usuária. Descarte saudações e repetições.",
            f"Responda em português do Brasil, com no máximo {limit} caracteres.",
        ]
        if previous.strip():
            parts.append(f"\nResumo anterior:\n{previous.strip()}")
        parts.append(f"\nConversa:\n{transcript}")
        model_ref = self._summary_model()
        request = ChatRequest(
            model=model_ref.model_id if model_ref else self.cfg.agent.model,
            messages=[ChatMessage(role="user", content="\n".join(parts))],
            system=(
                "Você é um resumidor fiel e conciso. Não invente fatos nem "
                "opiniões; apenas consolide o que a conversa registra."
            ),
            tools=[],
            temperature=0.2,
            max_tokens=900,
        )
        provider = self._ensure_provider()
        text_parts: list[str] = []
        usage = Usage()
        async for delta in provider.stream(request):
            if isinstance(delta, TextDelta):
                text_parts.append(delta.text)
            elif isinstance(delta, UsageDelta):
                usage += delta.usage
        summary = " ".join("".join(text_parts).split()).strip()
        return summary, usage

    async def _run_turn(
        self,
        session: AgentSession,
        text: str,
        system: str,
        *,
        allow_tools: bool,
        emit: Callable[[Event], None],
        already_busy: bool = False,
        model: ModelRef | None = None,
        cache_prefix: str = "",
        tool_names: tuple[str, ...] | None = None,
        history_text: str | None = None,
    ) -> AssistantMessage:
        if not already_busy:
            if session.busy:
                raise AgentError("a sessao ja esta executando")
            session.busy = True
        session.cancel.clear()
        message_id = f"msg_{secrets.token_hex(12)}"
        emit(TurnStarted(session_id=session.id, message_id=message_id))
        user_index = -1
        clean_text = history_text if history_text is not None else text
        turn_usage = Usage()
        try:
            turn_usage = await self._compact_history(session)
            session.messages.append(ChatMessage(role="user", content=text))
            user_index = len(session.messages) - 1
            session.allowed_tools = (
                set(tool_names) if tool_names is not None else set(self.tools.names())
            )
            system_text = system
            if session.summary:
                system_text = (
                    f"{system_text}\n\n" if system_text else ""
                ) + (
                    "[Resumo do histórico anterior — contexto comprimido, "
                    f"use se necessário]\n{session.summary}"
                )
            specs = self.tools.specs() if allow_tools else []
            if allow_tools and tool_names is not None:
                allowed = set(tool_names)
                specs = [spec for spec in specs if spec.get("name") in allowed]
            reasoning_all: list[str] = []
            provider = self._ensure_provider()
            for step in range(self.cfg.agent.max_steps + 1):
                if session.cancel.is_set():
                    raise AgentCancelled("turno interrompido")
                if step >= self.cfg.agent.max_steps:
                    raise AgentError(
                        f"limite de {self.cfg.agent.max_steps} passos atingido"
                    )
                request = ChatRequest(
                    model=model.model_id if model else self.cfg.agent.model,
                    messages=list(session.messages),
                    system=system_text,
                    tools=specs,
                    temperature=self.cfg.agent.temperature,
                    max_tokens=self.cfg.agent.max_tokens,
                    cache_prefix=(
                        cache_prefix if self.cfg.agent.cache_prompt else ""
                    ),
                    cache_messages=bool(cache_prefix)
                    and self.cfg.agent.cache_prompt,
                )
                text_parts: list[str] = []
                reasoning_parts: list[str] = []
                pending: dict[int, dict[str, str]] = {}
                async for delta in provider.stream(request):
                    if session.cancel.is_set():
                        raise AgentCancelled("turno interrompido")
                    if isinstance(delta, TextDelta):
                        text_parts.append(delta.text)
                        emit(
                            EventText(
                                session_id=session.id,
                                text=delta.text,
                                message_id=message_id,
                            )
                        )
                    elif isinstance(delta, ReasoningDelta):
                        reasoning_parts.append(delta.text)
                        emit(
                            EventReasoning(
                                session_id=session.id,
                                text=delta.text,
                                message_id=message_id,
                            )
                        )
                    elif isinstance(delta, ToolCallDelta):
                        entry = pending.setdefault(
                            delta.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if delta.id:
                            entry["id"] = delta.id
                        if delta.name:
                            entry["name"] += delta.name
                        entry["arguments"] += delta.arguments
                    elif isinstance(delta, UsageDelta):
                        turn_usage += delta.usage
                    elif isinstance(delta, Finish):
                        continue

                answer = "".join(text_parts).strip()
                reasoning = "".join(reasoning_parts)
                if reasoning:
                    reasoning_all.append(reasoning)
                calls = self._tool_calls(pending)
                session.messages.append(
                    ChatMessage(
                        role="assistant",
                        content=answer,
                        tool_calls=calls,
                    )
                )
                if not calls:
                    session.last_message_id = message_id
                    emit(
                        TurnCompleted(
                            session_id=session.id,
                            message_id=message_id,
                            text=answer,
                            reasoning="".join(reasoning_all),
                            usage=turn_usage.to_dict(),
                        )
                    )
                    return AssistantMessage(
                        id=message_id,
                        text=answer,
                        reasoning="".join(reasoning_all),
                    )
                for call in calls:
                    result = await self._run_tool(session, call, allow_tools, emit)
                    session.messages.append(
                        ChatMessage(
                            role="tool",
                            tool_call_id=call.id,
                            content=result.content,
                        )
                    )
            raise AgentError("o agente nao concluiu o turno")
        except AgentCancelled:
            emit(TurnCancelled(session_id=session.id))
            raise
        except AgentError as exc:
            emit(TurnFailed(session_id=session.id, message=str(exc)))
            raise
        except Exception as exc:  # pragma: no cover - depende de rede/provedor
            emit(TurnFailed(session_id=session.id, message=str(exc)))
            raise AgentError(f"falha no agente: {exc}") from exc
        finally:
            # O prompt do turno pode carregar contexto recuperado; no
            # historico fica apenas a mensagem original da usuaria.
            if 0 <= user_index < len(session.messages):
                stored = session.messages[user_index]
                if stored.role == "user" and clean_text != text:
                    stored.content = clean_text
            self._record_usage(session, turn_usage)
            self._trim_history_payloads(
                session, self.cfg.agent.tool_result_chars
            )
            # Uma interrupcao no meio das ferramentas deixa `tool_calls` sem
            # resultado; o provedor recusaria todos os turnos seguintes.
            session.repair_tool_pairs()
            session.busy = False
            session.cancel.clear()
            session.task = None
            session.permissions.clear()
            # Mantem o catalogo permitido entre turnos: a capacidade nao muda
            # por turno, so o conjunto de ferramentas registradas.
            # Nao ressuscita uma sessao apagada durante o turno.
            if self._sessions.get(session.id) is session:
                self._save(session)

    @staticmethod
    def _tool_calls(pending: dict[int, dict[str, str]]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for index in sorted(pending):
            entry = pending[index]
            name = entry.get("name", "").strip()
            if not name:
                continue
            raw = entry.get("arguments", "").strip() or "{}"
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError:
                arguments = {"_invalid_json": raw}
            if not isinstance(arguments, dict):
                arguments = {"_invalid_json": raw}
            calls.append(
                ToolCall(
                    id=entry.get("id") or f"call_{index}_{secrets.token_hex(4)}",
                    name=name,
                    arguments=arguments,
                )
            )
        return calls

    async def _run_tool(
        self,
        session: AgentSession,
        call: ToolCall,
        allow_tools: bool,
        emit: Callable[[Event], None],
    ) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None:
            return ToolResult.failure(f"ferramenta desconhecida: {call.name}")
        if not allow_tools:
            return ToolResult.failure(
                f"ferramenta {call.name} indisponivel nesta chamada"
            )
        if (
            session.allowed_tools is not None
            and call.name not in session.allowed_tools
        ):
            return ToolResult.failure(
                f"ferramenta {call.name} indisponivel neste turno"
            )
        if tool.requires_approval and not self.cfg.agent.auto_approve:
            if call.name not in self._always_approved:
                decision = await self._ask_permission(session, tool, call, emit)
                if decision == "reject":
                    return ToolResult.failure("o usuario negou a execucao")
                if decision == "always":
                    self._always_approved.add(call.name)
        detail = tool.summary(call.arguments)
        emit(
            ToolStarted(
                session_id=session.id,
                call_id=call.id,
                name=call.name,
                detail=detail,
            )
        )
        try:
            result = await asyncio.to_thread(tool.handler, call.arguments, self._tool_context)
        except ToolError as exc:
            result = ToolResult.failure(str(exc))
        except Exception as exc:  # pragma: no cover - depende do ambiente
            self._logger.warning("ferramenta %s falhou: %s", call.name, exc, exc_info=True)
            result = ToolResult.failure(f"erro inesperado em {call.name}: {exc}")
        emit(
            ToolFinished(
                session_id=session.id,
                call_id=call.id,
                name=call.name,
                status="completed" if result.ok else "error",
                error="" if result.ok else result.content,
                diff_name=result.diff_name,
                diff=result.diff,
            )
        )
        return result

    async def _ask_permission(
        self,
        session: AgentSession,
        tool: Any,
        call: ToolCall,
        emit: Callable[[Event], None],
    ) -> str:
        loop = asyncio.get_running_loop()
        request_id = f"per_{secrets.token_hex(6)}"
        future: asyncio.Future[str] = loop.create_future()
        session.permissions[request_id] = future
        detail = tool.summary(call.arguments)
        emit(
            PermissionAsked(
                session_id=session.id,
                request_id=request_id,
                tool=call.name,
                detail=detail,
                resources=[detail] if detail else [],
            )
        )
        try:
            return await future
        finally:
            session.permissions.pop(request_id, None)

    async def reply_permission(
        self,
        session_id: str,
        permission_id: str,
        response: str,
        *,
        v2: bool | None = None,
    ) -> None:
        if response not in {"once", "always", "reject"}:
            raise ValueError(f"resposta de permissao invalida: {response}")
        session = self._session(session_id)
        if session is None:
            return
        future = session.permissions.pop(permission_id, None)
        if future is not None and not future.done():
            future.set_result(response)

    # ------------------------------------------------------------------- eventos
    async def stream_events(
        self, stop: asyncio.Event | None = None
    ) -> AsyncIterator[Event]:
        while not (stop and stop.is_set()):
            try:
                event = await asyncio.wait_for(self._events.get(), timeout=0.5)
            except TimeoutError:
                continue
            yield event

    def _latest_assistant(self, session_id: str) -> AssistantMessage | None:
        session = self._session(session_id)
        if session is None:
            return None
        for message in reversed(session.messages):
            if message.role == "assistant" and message.content.strip():
                return AssistantMessage(
                    id=session.last_message_id,
                    text=message.content,
                )
        return None

    async def latest_assistant(self, session_id: str) -> AssistantMessage | None:
        return self._latest_assistant(session_id)

    async def latest_assistant_text(self, session_id: str) -> str:
        message = self._latest_assistant(session_id)
        return message.text if message else ""

    # ------------------------------------------------------------------- fim
    async def aclose(self) -> None:
        for session in list(self._sessions.values()):
            if session.task is not None and not session.task.done():
                session.cancel.set()
                session.task.cancel()
            self._save(session)
        if self._provider is not None:
            await self._provider.aclose()
            self._provider = None
