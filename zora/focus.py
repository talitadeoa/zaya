"""Focos voláteis (semana/mês/trimestre) e fases da lua."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

PHASES = [
    "new",
    "waxing_crescent",
    "first_quarter",
    "waxing_gibbous",
    "full",
    "waning_gibbous",
    "last_quarter",
    "waning_crescent",
]

RITUALS = {
    "new": ("Lua nova", "plantar intenções e definir o foco do ciclo"),
    "waxing_crescent": ("Lua crescente", "dar o primeiro passo concreto"),
    "first_quarter": ("Quarto crescente", "agir e resolver o que trava"),
    "waxing_gibbous": ("Gibosa crescente", "ajustar e refinar o plano"),
    "full": ("Lua cheia", "revisar, celebrar e enxergar o que está pronto"),
    "waning_gibbous": ("Gibosa minguante", "compartilhar e consolidar"),
    "last_quarter": ("Quarto minguante", "soltar o que não serve e organizar"),
    "waning_crescent": ("Lua minguante", "descansar, refletir e preparar o próximo ciclo"),
}

KEY_PHASES = {"new", "first_quarter", "full", "last_quarter"}

_PERIOD_DAYS = {"week": 7, "month": 30, "quarter": 90}
_PERIOD_LABEL = {"week": "semanal", "month": "mensal", "quarter": "trimestral"}


def moon_phase(now: datetime | None = None) -> tuple[str, str, float]:
    """Retorna (chave, nome, iluminacao 0..1) aproximando o ciclo sinodico."""
    now = now or datetime.now()
    reference = datetime(2000, 1, 6, 18, 14)
    synodic = 29.53058867
    days = (now - reference).total_seconds() / 86400.0
    age = days % synodic
    index = int((age / synodic) * 8 + 0.5) % 8
    key = PHASES[index]
    illumination = (1 - math.cos(2 * math.pi * age / synodic)) / 2
    return key, RITUALS[key][0], illumination


@dataclass
class Focus:
    period: str = "week"
    label: str = ""
    goals: list[str] = field(default_factory=list)
    start: str = ""
    end: str = ""
    created: str = ""


class FocusStore:
    def __init__(self, path: str | Path | None = None):
        self.path = (
            Path(path).expanduser()
            if path
            else Path.home() / ".config" / "zora" / "focus.json"
        )
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @property
    def current(self) -> Focus | None:
        raw = self._data.get("focus")
        if not isinstance(raw, dict):
            return None
        try:
            return Focus(**raw)
        except TypeError:
            return None

    def set(self, period: str, label: str, goals: list[str] | None = None) -> Focus:
        period = period if period in _PERIOD_DAYS else "week"
        now = datetime.now().astimezone()
        end = now + timedelta(days=_PERIOD_DAYS[period])
        focus = Focus(
            period=period,
            label=label.strip(),
            goals=[goal.strip() for goal in (goals or []) if goal.strip()],
            start=now.date().isoformat(),
            end=end.date().isoformat(),
            created=now.isoformat(timespec="seconds"),
        )
        self._data["focus"] = asdict(focus)
        self._save()
        return focus

    def clear(self) -> None:
        self._data.pop("focus", None)
        self._save()

    def is_expired(self, now: datetime | None = None) -> bool:
        focus = self.current
        if focus is None or not focus.end:
            return False
        today = (now or datetime.now()).date().isoformat()
        return today > focus.end

    def last_moon_prompt(self) -> str:
        return str(self._data.get("last_moon_prompt") or "")

    def set_last_moon_prompt(self, value: str) -> None:
        self._data["last_moon_prompt"] = value
        self._save()


def period_label(period: str) -> str:
    return _PERIOD_LABEL.get(period, period)
