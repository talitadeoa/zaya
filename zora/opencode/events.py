"""Interpretacao de eventos SSE e de payloads de mensagem do OpenCode.

Suporta tanto o formato antigo (1.18.x: `properties`/`parts`) quanto o novo
(2.x: `data`/`content`). O servidor v2 e o alvo principal, mas manter os dois
formatos evita que uma sessao antiga retomada quebre a conversa.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import AsyncIterator
from typing import Any

TEXT_PART = "text"
TOOL_PART = "tool"
REASONING_PART = "reasoning"
PATCH_PART = "patch"


async def iter_sse_json(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Converte o stream SSE do OpenCode em dicionarios JSON.

    O OpenCode envia linhas `data: {...}` separadas por linha em branco. Campos
    `event:`, `id:` e comentarios (`:`) sao ignorados.
    """
    data_lines: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue
            continue
        if line.startswith(":"):
            continue
        field, sep, value = line.partition(":")
        if not sep:
            continue
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
    if data_lines:
        try:
            yield json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return


def event_type(event: dict[str, Any]) -> str:
    return str(event.get("type", ""))


def event_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Payload do evento: `data` (v2) ou `properties` (v1)."""
    data = event.get("data")
    if isinstance(data, dict):
        return data
    props = event.get("properties")
    if isinstance(props, dict):
        return props
    return {}


def event_session_id(event: dict[str, Any]) -> str | None:
    payload = event_payload(event)
    sid = payload.get("sessionID")
    if sid:
        return str(sid)
    info = payload.get("info") or {}
    if isinstance(info, dict) and info.get("sessionID"):
        return str(info["sessionID"])
    part = payload.get("part") or {}
    if isinstance(part, dict) and part.get("sessionID"):
        return str(part["sessionID"])
    return None


def text_delta(part: dict[str, Any], previous: str) -> tuple[str, str]:
    """Retorna (delta, texto_atual) para um `text` part cumulativo (v1).

    O servidor pode emitir o texto acumulado ou apenas um fragmento. Tratamos
    os dois casos sem duplicar conteudo.
    """
    current = part.get("text", "") or ""
    if current.startswith(previous):
        return current[len(previous) :], current
    if previous.startswith(current):
        return "", previous
    return current, current


def tool_detail(payload: Any) -> str:
    """Descricao curta de uma chamada de ferramenta a partir do input."""
    if not isinstance(payload, dict):
        return ""
    for key in (
        "command",
        "description",
        "filePath",
        "path",
        "file",
        "filename",
        "pattern",
        "query",
        "url",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def tool_activity(part: dict[str, Any]) -> tuple[str, str, str] | None:
    """Retorna (callID, tool, descricao) quando o part e uma ferramenta em execucao."""
    if part.get("type") != TOOL_PART:
        return None
    state = part.get("state") or {}
    status = state.get("status")
    tool = part.get("tool") or part.get("name") or "tool"
    call_id = part.get("callID") or part.get("id") or ""
    title = state.get("title") or tool_detail(state.get("input"))
    if status in {"running", "pending"}:
        return call_id, tool, title
    if status == "completed":
        return call_id, tool, title or "concluido"
    if status == "error":
        error = state.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("type") or error
        return call_id, tool, f"erro: {error}"
    return None


def tool_diff(part: dict[str, Any]) -> tuple[str, str] | None:
    """Extrai (arquivo, diff) de uma ferramenta concluida, quando houver."""
    if part.get("type") != TOOL_PART:
        return None
    state = part.get("state") or {}
    if state.get("status") not in {"completed", "error"}:
        return None
    tool = str(part.get("tool") or part.get("name") or "")
    payload = state.get("input") or {}
    metadata = state.get("metadata") or {}
    if not isinstance(payload, dict):
        payload = {}
    filename = str(
        payload.get("filePath")
        or payload.get("path")
        or payload.get("file")
        or payload.get("filename")
        or ""
    )
    if isinstance(metadata, dict):
        for key in ("diff", "patch"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return filename or "arquivo", value
    for key in ("patch", "diff"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return filename or "arquivo", value
    if tool in {"edit", "write", "patch"}:
        old = payload.get("oldString")
        new = payload.get("newString", payload.get("content"))
        if isinstance(new, str) and new != old:
            old_text = old if isinstance(old, str) else ""
            name = filename or "arquivo"
            patch = "\n".join(
                difflib.unified_diff(
                    old_text.splitlines(),
                    new.splitlines(),
                    fromfile=f"a/{name}",
                    tofile=f"b/{name}",
                    lineterm="",
                )
            )
            if patch.strip():
                return name, patch
    return None


def assistant_text(message: dict[str, Any]) -> str:
    """Extrai o texto final de uma mensagem do OpenCode.

    v2: `content: [{type: "text", text: ...}]`; v1: `parts`.
    """
    content = message.get("content")
    if isinstance(content, list):
        chunks = [
            str(part.get("text") or "").strip()
            for part in content
            if isinstance(part, dict)
            and part.get("type") == TEXT_PART
            and str(part.get("text") or "").strip()
        ]
        return "\n".join(chunks).strip()

    parts = message.get("parts") or []
    chunks = []
    for part in parts:
        if part.get("type") != TEXT_PART:
            continue
        if part.get("synthetic") or part.get("ignored"):
            continue
        text = (part.get("text") or "").strip()
        if text:
            chunks.append(text)
    if chunks:
        return "\n".join(chunks).strip()
    text = message.get("text")
    return str(text).strip() if isinstance(text, str) else ""


def message_error(message: dict[str, Any]) -> str | None:
    err = message.get("error")
    if err is None:
        info = message.get("info") or {}
        err = info.get("error")
    if not err:
        return None
    if isinstance(err, dict):
        name = err.get("type") or err.get("name") or "erro"
        detail = err.get("message")
        if not detail:
            data = err.get("data") or {}
            detail = data.get("message") if isinstance(data, dict) else None
        return f"{name}: {detail}" if detail else str(name)
    return str(err)


def message_id(message: dict[str, Any]) -> str | None:
    mid = message.get("id")
    if mid:
        return str(mid)
    info = message.get("info") or {}
    mid = info.get("id")
    return str(mid) if mid else None


def message_role(message: dict[str, Any]) -> str | None:
    role = message.get("type")
    if role in {"user", "assistant"}:
        return str(role)
    info = message.get("info") or {}
    role = info.get("role")
    return str(role) if role else None


def structured_error_text(error: Any) -> str:
    """Formata um `Session.StructuredError` (v2) para exibicao."""
    if isinstance(error, dict):
        name = error.get("type") or error.get("name") or "erro"
        detail = error.get("message")
        return f"{name}: {detail}" if detail else str(name)
    return str(error or "erro desconhecido")


def session_status_is_idle(status: Any) -> bool:
    if isinstance(status, dict):
        return status.get("type") == "idle"
    return status == "idle"
