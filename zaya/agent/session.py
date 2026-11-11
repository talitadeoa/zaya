"""Sessao do agente e persistencia simples em JSON."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zaya.agent.types import ChatMessage


def _message_chars(message: ChatMessage) -> int:
    """Estimativa de tamanho da mensagem no prompt, incluindo tool calls."""
    total = len(message.content or "")
    for call in message.tool_calls:
        total += len(
            json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))
        )
    return total


@dataclass
class AgentSession:
    id: str
    title: str = ""
    directory: str = ""
    messages: list[ChatMessage] = field(default_factory=list)
    busy: bool = False
    last_message_id: str = ""
    created_at: float = field(default_factory=time.time)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    permissions: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    task: asyncio.Task | None = None
    # Resumo do historico descartado para manter o contexto curto.
    summary: str = ""
    # Tokens do ultimo turno, acumulado da sessao e historico por turno.
    usage_last: dict[str, int] = field(default_factory=dict)
    usage_total: dict[str, int] = field(default_factory=dict)
    usage_history: list[dict[str, int]] = field(default_factory=list)
    # Ferramentas liberadas no turno atual (None = todas).
    allowed_tools: set[str] | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "directory": self.directory,
            "last_message_id": self.last_message_id,
            "created_at": self.created_at,
            "summary": self.summary,
            "usage_last": self.usage_last,
            "usage_total": self.usage_total,
            "usage_history": self.usage_history,
            "messages": [message.to_dict() for message in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSession:
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            directory=str(data.get("directory") or ""),
            last_message_id=str(data.get("last_message_id") or ""),
            created_at=float(data.get("created_at") or time.time()),
            summary=str(data.get("summary") or ""),
            usage_last=dict(data.get("usage_last") or {}),
            usage_total=dict(data.get("usage_total") or {}),
            usage_history=[
                dict(item)
                for item in data.get("usage_history") or []
                if isinstance(item, dict)
            ],
            messages=[
                ChatMessage.from_dict(item) for item in data.get("messages") or []
            ],
        )

    def _next_user(self, start: int) -> int:
        index = max(0, start)
        while index < len(self.messages) and self.messages[index].role != "user":
            index += 1
        return index

    def _tail_chars(self, start: int) -> int:
        return sum(_message_chars(message) for message in self.messages[start:])

    def compaction_split(
        self, max_messages: int, max_chars: int = 0
    ) -> tuple[int, list[ChatMessage]] | None:
        """Devolve o corte e o prefixo que sera resumido.

        O corte sempre cai numa mensagem ``user``: assim nenhum ``tool`` fica
        orfao do ``assistant`` que pediu a ferramenta. Dispara por numero de
        mensagens **ou** por tamanho total do historico.
        """
        count_exceeded = max_messages > 0 and len(self.messages) > max_messages
        chars_exceeded = max_chars > 0 and self._tail_chars(0) > max_chars
        if not count_exceeded and not chars_exceeded:
            return None

        base = len(self.messages) - max_messages if count_exceeded else 0
        cutoff = self._next_user(base)
        if cutoff >= len(self.messages):
            return None
        if max_chars > 0:
            while self._tail_chars(cutoff) > max_chars:
                following = self._next_user(cutoff + 1)
                if following >= len(self.messages):
                    break
                cutoff = following
        if cutoff <= 0:
            return None
        return cutoff, self.messages[:cutoff]

    @staticmethod
    def local_summary(
        prefix: list[ChatMessage], previous: str = "", summary_chars: int = 1200
    ) -> str:
        """Resumo extrativo deterministico, usado como fallback."""
        lines: list[str] = []
        for message in prefix:
            if message.role not in {"user", "assistant"}:
                continue
            text = " ".join((message.content or "").split())
            if not text:
                continue
            if len(text) > 300:
                text = text[:300].rstrip() + "…"
            speaker = "Talita" if message.role == "user" else "Zaya"
            lines.append(f"{speaker}: {text}")
        if not lines:
            return previous.strip()
        merged = "\n".join(filter(None, [previous.strip(), *lines]))
        if len(merged) > summary_chars:
            trimmed = merged[-summary_chars:]
            position = trimmed.find("\n")
            merged = trimmed[position + 1 :] if position >= 0 else trimmed
        return merged.strip()

    def apply_compaction(self, cutoff: int, summary: str) -> None:
        self.summary = summary.strip()
        self.messages = self.messages[cutoff:]

    def compact(
        self, max_messages: int, summary_chars: int = 1200, max_chars: int = 0
    ) -> bool:
        """Troca o inicio do historico por um resumo extrativo curto."""
        split = self.compaction_split(max_messages, max_chars)
        if split is None:
            return False
        cutoff, prefix = split
        summary = self.local_summary(prefix, self.summary, summary_chars)
        if not summary:
            return False
        self.apply_compaction(cutoff, summary)
        return True


class SessionStore:
    """Guarda as sessoes como um arquivo JSON por id."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, session_id: str) -> Path:
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in {"_", "-"})
        return self.directory / f"{safe}.json"

    def load(self, session_id: str) -> AgentSession | None:
        path = self._path(session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        session = AgentSession.from_dict(data)
        return session if session.id else None

    def save(self, session: AgentSession) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self._path(session.id)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            return

    def delete(self, session_id: str) -> None:
        try:
            self._path(session_id).unlink()
        except OSError:
            pass

    def list_ids(self) -> list[str]:
        try:
            return sorted(path.stem for path in self.directory.glob("*.json"))
        except OSError:
            return []
