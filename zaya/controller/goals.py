"""Detecta quando a conversa toca nas metas pessoais (arquivo local)."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Goal:
    key: str
    label: str
    keywords: tuple[str, ...]
    next_step: str = ""


def default_goals_path() -> Path:
    env = os.environ.get("ZAYA_GOALS_FILE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "zaya" / "goals.json"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


class GoalTracker:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else default_goals_path()
        self._goals = self._load()

    def _load(self) -> list[Goal]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = raw.get("goals") if isinstance(raw, dict) else raw
        goals: list[Goal] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            label = str(item.get("label") or key).strip()
            keywords = tuple(
                _normalize(str(word))
                for word in (item.get("keywords") or [])
                if str(word).strip()
            )
            if not key or not label or not keywords:
                continue
            goals.append(
                Goal(
                    key=key,
                    label=label,
                    keywords=keywords,
                    next_step=str(item.get("next_step") or "").strip(),
                )
            )
        return goals

    def all(self) -> list[Goal]:
        return list(self._goals)

    def detect(self, text: str) -> list[Goal]:
        normalized = _normalize(text or "")
        if not normalized:
            return []
        found: list[Goal] = []
        for goal in self._goals:
            for keyword in goal.keywords:
                pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"
                if re.search(pattern, normalized):
                    found.append(goal)
                    break
        return found


def goal_block(goals: list[Goal]) -> str:
    if not goals:
        return ""
    lines = [f"- {goal.label}: {goal.next_step}" if goal.next_step else f"- {goal.label}" for goal in goals]
    return (
        "[Metas pessoais relacionadas a este pedido — priorize e conecte quando "
        "fizer sentido]\n"
        + "\n".join(lines)
        + "\nSe for natural, proponha um próximo passo concreto; não force a conexão."
    )
