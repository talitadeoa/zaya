"""Consolidacao diaria: transforma os turnos do dia em uma nota de memoria.

A nota vive no Second Brain (Markdown + indice FTS5), entao o dia consolidado
volta automaticamente como contexto nos pedidos seguintes. O resumo e feito
pelo modelo quando possivel; se ele falhar, um resumo local deterministico
garante que nada se perca.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from zaya.config import Config
from zaya.memory.store import MemoryStore
from zaya.opencode.client import OpenCodeClient
from zaya.opencode.events import assistant_text
from zaya.secondbrain.index import SecondBrainIndex
from zaya.secondbrain.notes import SecondBrain

_DECISION_RE = re.compile(
    r"\b(decidi|decidimos|decidido|vamos usar|adotamos|escolhemos|"
    r"ficou definido|fico definido|a partir de agora)\b",
    re.IGNORECASE,
)

_SUMMARY_PROMPT = (
    "Você é a Zaya, a agente pessoal da Talita. Resuma o dia abaixo em Markdown, "
    "em português do Brasil. Comece com um parágrafo de síntese, sem título. "
    "Depois use exatamente estas seções:\n"
    "## Decisões\n"
    "## Aprendizados\n\n"
    "Seja fiel ao que aconteceu; não invente, não use ferramentas e não leia "
    "arquivos. Se não houver decisões ou aprendizados, escreva '—' na seção. "
    "Máximo de 300 palavras.\n\n---\n\n"
)


def day_bounds(day: date) -> tuple[str, str]:
    """Inicio (inclusive) e fim (exclusive) do dia no fuso local, em ISO."""
    tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def parse_day(argument: str, today: date | None = None) -> tuple[date, bool]:
    """Interpreta ``/consolidar [hoje|ontem|AAAA-MM-DD] [forcar]``."""
    today = today or datetime.now().astimezone().date()
    text = (argument or "").strip().lower()
    force = "forcar" in text or "forçar" in text
    for token in text.split():
        try:
            return date.fromisoformat(token), force
        except ValueError:
            continue
    if "ontem" in text:
        return today - timedelta(days=1), force
    if "anteontem" in text:
        return today - timedelta(days=2), force
    return today, force


def _clean(text: object, limit: int = 200) -> str:
    value = " ".join(str(text or "").split())
    if len(value) > limit:
        value = value[:limit].rstrip() + "…"
    return value


def _conversation_rows(turns: list) -> list:
    return [row for row in turns if str(row["role"]) in {"user", "assistant"}]


def format_turns(turns: list, max_turns: int = 120, max_chars: int = 16000) -> str:
    """Formata os turnos para o prompt de resumo, com limites de tamanho."""
    rows = _conversation_rows(turns)[-max_turns:]
    lines: list[str] = []
    for row in rows:
        role = "Talita" if str(row["role"]) == "user" else "Zaya"
        text = _clean(row["text"], 500)
        if not text:
            continue
        mode = str(row["mode"] or "")
        prefix = f"[{mode}] " if mode else ""
        lines.append(f"{prefix}{role}: {text}")
    convo = "\n".join(lines)
    if len(convo) > max_chars:
        convo = convo[:max_chars].rsplit("\n", 1)[0] + "\n[… truncado]"
    return convo


def _fallback_summary(turns: list) -> str:
    users = [row for row in turns if str(row["role"]) == "user"]
    if not users:
        return "Dia sem mensagens registradas."
    modes = sorted({str(row["mode"] or "") for row in users if row["mode"]})
    labels = ", ".join(_mode_label(mode) for mode in modes) or "sem modo"
    return f"{len(users)} mensagem(ns) em {labels}."


def _mode_label(key: str) -> str:
    # Import tardio: evita ciclo com zaya.controller no carregamento do pacote.
    from zaya.controller.modes import get_persona

    persona = get_persona(key)
    if persona is None:
        return key
    return persona.name.removeprefix("Parceira ")


def _decisions(turns: list) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for row in _conversation_rows(turns):
        text = _clean(row["text"], 220)
        if not _DECISION_RE.search(text):
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(text)
        if len(found) >= 12:
            break
    return found


def _topics(turns: list, limit: int = 20) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    for row in turns:
        if str(row["role"]) != "user":
            continue
        text = _clean(row["text"], 160)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        label = _mode_label(str(row["mode"] or ""))
        topics.append(f"{label}: {text}" if label else text)
        if len(topics) >= limit:
            break
    return topics


def _strip_summary_heading(summary: str) -> str:
    """Remove um "## Síntese" inicial para nao duplicar o titulo da nota."""
    text = summary.strip()
    return re.sub(
        r"^#{1,3}\s*S[íi]ntese(?:\s+do\s+dia)?\s*\n+", "", text, flags=re.IGNORECASE
    ).strip()


def compose_note(day: date, turns: list, records: list, summary: str = "") -> str:
    """Monta o corpo Markdown da nota de memoria do dia."""
    sintese = _strip_summary_heading(summary)
    parts = ["## Síntese do dia", sintese or _fallback_summary(turns)]

    # Com resumo do modelo, as decisoes ja vem nele; o fallback local as monta.
    decisions = [] if sintese else _decisions(turns)
    if decisions:
        parts.extend(["", "## Decisões", *[f"- {item}" for item in decisions]])

    topics = _topics(turns)
    if topics:
        parts.extend(["", "## Conversas", *[f"- {item}" for item in topics]])

    if records:
        linhas = []
        for row in records:
            kind = str(row["kind"] or "registro")
            title = str(row["title"] or row["path"] or "").strip()
            linhas.append(f"- {kind}: {title}" if title else f"- {kind}")
        if linhas:
            parts.extend(["", "## Registros do dia", *linhas])

    return "\n".join(parts).strip() + "\n"


class DailyMemory:
    """Consolida um dia de conversas em uma nota do Second Brain."""

    def __init__(
        self,
        cfg: Config,
        store: MemoryStore,
        brain: SecondBrain,
        index: SecondBrainIndex,
        client: OpenCodeClient,
        directory: Path,
        logger: logging.Logger | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.brain = brain
        self.index = index
        self.client = client
        self.directory = directory
        self.logger = logger or logging.getLogger(__name__)
        self._running = False

    async def consolidate(
        self,
        day: date | None = None,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> str | None:
        cfg = self.cfg.memory
        if not cfg.enabled or self._running:
            return None
        day = day or datetime.now().astimezone().date()
        key = f"memory_consolidated:{day.isoformat()}"
        if not force and self.store.get_meta(key):
            return None

        start, end = day_bounds(day)
        turns = await asyncio.to_thread(
            self.store.turns_between, start, end, str(self.directory)
        )
        records = await asyncio.to_thread(self.store.records_between, start, end)
        if not turns and not records:
            return None

        self._running = True
        try:
            summary = ""
            if cfg.use_llm:
                summary = await self._summarize(
                    day, turns, timeout_seconds or cfg.llm_timeout_seconds
                )
            body = compose_note(day, turns, records, summary)
            note = await asyncio.to_thread(self._write_note, day, body)
            self.store.set_meta(key, note.rel_path)
            self.store.record_note(None, "memory", note.rel_path, note.title)
            return note.rel_path
        finally:
            self._running = False

    def _write_note(self, day: date, body: str):
        cfg = self.cfg.memory
        title = f"Memória {day.isoformat()}"
        tags = ["memoria", "diario", day.isoformat()]
        existing = self.brain.resolve(f"{cfg.folder}/{title}")
        if existing:
            return self.brain.update_note(str(existing), body, confirm=True, tags=tags)
        return self.brain.create_note(
            title, body, folder=cfg.folder, tags=tags, confirm=True
        )

    async def _summarize(self, day: date, turns: list, timeout_seconds: float) -> str:
        convo = format_turns(
            turns,
            max_turns=self.cfg.memory.max_turns,
            max_chars=self.cfg.memory.max_chars,
        )
        if not convo:
            return ""
        session_id = ""
        try:
            session = await self.client.create_session(title=f"memória {day.isoformat()}")
            session_id = session.id
            message = await asyncio.wait_for(
                self.client.prompt_sync(session_id, _SUMMARY_PROMPT + convo),
                timeout=max(10.0, timeout_seconds),
            )
            return assistant_text(message).strip()
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("resumo diário pelo modelo falhou: %s", exc)
            return ""
        finally:
            if session_id:
                await self.client.delete_session(session_id)
