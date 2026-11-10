"""Fila local de tarefas de longa duracao (sincronizada da Agenda)."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Task:
    id: str
    title: str
    status: str = "pending"
    notes: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def _task_id(title: str) -> str:
    return hashlib.sha1(title.strip().lower().encode("utf-8")).hexdigest()[:10]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def _similar(a: str, b: str, threshold: float = 0.88) -> bool:
    left, right = _normalize(a), _normalize(b)
    if min(len(left), len(right)) < 15:
        return False
    return difflib.SequenceMatcher(None, left, right).ratio() >= threshold


class TaskStore:
    def __init__(self, path: str | Path | None = None):
        self.path = (
            Path(path).expanduser()
            if path
            else Path.home() / ".local" / "share" / "zaya" / "tasks.json"
        )
        self._tasks: dict[str, Task] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in raw.get("tasks", []) if isinstance(raw, dict) else []:
            try:
                task = Task(**item)
            except TypeError:
                continue
            self._tasks[task.id] = task

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tasks": [asdict(task) for task in self._tasks.values()]}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def all(self) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda task: task.updated_at)

    def upsert(
        self, title: str, status: str | None = None, notes: str | None = None
    ) -> Task:
        title = " ".join((title or "").split())
        if not title:
            raise ValueError("titulo vazio")
        task_id = _task_id(title)
        existing = self._tasks.get(task_id)
        if existing is None:
            task = Task(
                id=task_id,
                title=title,
                status=status or "pending",
                notes=notes or "",
            )
            self._tasks[task_id] = task
        else:
            task = existing
            if status:
                task.status = status
            if notes:
                task.notes = notes
            task.updated_at = datetime.now().isoformat(timespec="seconds")
        self._save()
        return task

    def set_status(self, task_id: str, status: str, notes: str = "") -> Task | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = status
        if notes:
            task.notes = notes
        task.updated_at = datetime.now().isoformat(timespec="seconds")
        self._save()
        return task

    def clear_done(self) -> int:
        removed = [task for task in self._tasks.values() if task.status == "done"]
        for task in removed:
            del self._tasks[task.id]
        if removed:
            self._save()
        return len(removed)

    def sync_text(self, text: str) -> list[Task]:
        cleaned = (text or "").replace("<br>", "\n")
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        tasks: list[Task] = []
        for raw in cleaned.splitlines():
            line = raw.strip().strip("-•*\t ")
            line = " ".join(line.split())
            if not line:
                continue
            exact = self._tasks.get(_task_id(line))
            if exact is not None:
                tasks.append(exact)
                continue
            similar = next(
                (task for task in self._tasks.values() if _similar(task.title, line)),
                None,
            )
            if similar is not None:
                similar.title = line
                similar.updated_at = datetime.now().isoformat(timespec="seconds")
                self._save()
                tasks.append(similar)
                continue
            tasks.append(self.upsert(line))
        return tasks
