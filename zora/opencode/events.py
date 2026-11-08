"""Interpretacao de eventos SSE e de payloads de mensagem do OpenCode."""

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


def event_session_id(event: dict[str, Any]) -> str | None:
    props = event.get("properties") or {}
    sid = props.get("sessionID")
    if sid:
        return str(sid)
    info = props.get("info") or {}
    if isinstance(info, dict) and info.get("sessionID"):
        return str(info["sessionID"])
    part = props.get("part") or {}
    if isinstance(part, dict) and part.get("sessionID"):
        return str(part["sessionID"])
    return None


def text_delta(part: dict[str, Any], previous: str) -> tuple[str, str]:
    """Retorna (delta, texto_atual) para um `text` part.

    O servidor pode emitir o texto acumulado ou apenas um fragmento. Tratamos
    os dois casos sem duplicar conteudo.
    """
    current = part.get("text", "") or ""
    if current.startswith(previous):
        return current[len(previous) :], current
    if previous.startswith(current):
        return "", previous
    return current, current


def tool_activity(part: dict[str, Any]) -> tuple[str, str, str] | None:
    """Retorna (callID, tool, descricao) quando o part e uma ferramenta em execucao."""
    if part.get("type") != TOOL_PART:
        return None
    state = part.get("state") or {}
    status = state.get("status")
    tool = part.get("tool", "tool")
    title = state.get("title") or ""
    if status in {"running", "pending"}:
        return part.get("callID", ""), tool, title
    if status == "completed":
        return part.get("callID", ""), tool, title or "concluido"
    if status == "error":
        return part.get("callID", ""), tool, f"erro: {state.get('error', '')}"
    return None


def tool_diff(part: dict[str, Any]) -> tuple[str, str] | None:
    """Extrai (arquivo, diff) de uma ferramenta concluida, quando houver."""
    if part.get("type") != TOOL_PART:
        return None
    state = part.get("state") or {}
    if state.get("status") not in {"completed", "error"}:
        return None
    tool = str(part.get("tool") or "")
    payload = state.get("input") or {}
    metadata = state.get("metadata") or {}
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
    """Extrai o texto final de um payload {info, parts} do OpenCode."""
    parts = message.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if part.get("type") != TEXT_PART:
            continue
        if part.get("synthetic") or part.get("ignored"):
            continue
        text = (part.get("text") or "").strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def message_error(message: dict[str, Any]) -> str | None:
    info = message.get("info") or {}
    err = info.get("error")
    if not err:
        return None
    if isinstance(err, dict):
        name = err.get("name") or err.get("type") or "erro"
        data = err.get("data") or {}
        detail = data.get("message") if isinstance(data, dict) else None
        return f"{name}: {detail}" if detail else str(name)
    return str(err)


def session_status_is_idle(status: Any) -> bool:
    if isinstance(status, dict):
        return status.get("type") == "idle"
    return status == "idle"
