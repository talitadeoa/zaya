"""Focos voláteis (semana/mês/trimestre) e fases da lua."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import astronomy

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

ZODIAC_SYMBOLS = ["♈", "♉", "♊", "♋", "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓"]
ZODIAC_NAMES = [
    "Áries",
    "Touro",
    "Gêmeos",
    "Câncer",
    "Leão",
    "Virgem",
    "Libra",
    "Escorpião",
    "Sagitário",
    "Capricórnio",
    "Aquário",
    "Peixes",
]
ELEMENTS = ["Fogo", "Terra", "Ar", "Água"]
ELEMENT_EMOJI = {"Fogo": "🔥", "Terra": "🌍", "Ar": "💨", "Água": "💧"}
MOON_EMOJI = {
    "new": "🌑",
    "waxing_crescent": "🌒",
    "first_quarter": "🌓",
    "waxing_gibbous": "🌔",
    "full": "🌕",
    "waning_gibbous": "🌖",
    "last_quarter": "🌗",
    "waning_crescent": "🌘",
}
PHASE_EMOJI = {
    "new": "🌱",
    "waxing_crescent": "🌳",
    "first_quarter": "🌳",
    "waxing_gibbous": "🌳",
    "full": "🍎",
    "waning_gibbous": "🧹",
    "last_quarter": "🧹",
    "waning_crescent": "🧹",
}

_PERIOD_DAYS = {"week": 7, "month": 30, "quarter": 90}
_PERIOD_LABEL = {"week": "semanal", "month": "mensal", "quarter": "trimestral"}


def _moon_moment(now: datetime | None = None) -> tuple[astronomy.Time, datetime]:
    tz = ZoneInfo("America/Sao_Paulo")
    local = (now or datetime.now(tz)).astimezone(tz)
    noon_local = datetime(local.year, local.month, local.day, 12, 0, tzinfo=tz)
    noon_utc = noon_local.astimezone(UTC)
    moment = astronomy.Time.Make(
        noon_utc.year,
        noon_utc.month,
        noon_utc.day,
        noon_utc.hour,
        noon_utc.minute,
        noon_utc.second + noon_utc.microsecond / 1_000_000,
    )
    return moment, local


def moon_phase(now: datetime | None = None) -> tuple[str, str, float]:
    """Retorna (chave, nome, iluminacao 0..1) usando a mesma base da Agenda."""
    moment, _local = _moon_moment(now)
    fraction = (astronomy.MoonPhase(moment) / 360.0) % 1.0
    index = round(fraction * 8) % 8
    key = PHASES[index]
    illumination = max(
        0.0, min(1.0, astronomy.Illumination(astronomy.Body.Moon, moment).phase_fraction)
    )
    return key, RITUALS[key][0], illumination


@dataclass(frozen=True)
class MoonDetails:
    phase_key: str
    phase_name: str
    illumination: float
    moon_emoji: str
    phase_emoji: str
    sign_symbol: str
    sign_name: str
    element: str
    element_emoji: str


def moon_details(now: datetime | None = None) -> MoonDetails:
    key, name, illumination = moon_phase(now)
    moment, _local = _moon_moment(now)
    longitude = astronomy.EclipticGeoMoon(moment).lon
    index = int(longitude // 30.0) % 12
    element = ELEMENTS[index % 4]
    return MoonDetails(
        phase_key=key,
        phase_name=name,
        illumination=illumination,
        moon_emoji=MOON_EMOJI[key],
        phase_emoji=PHASE_EMOJI[key],
        sign_symbol=ZODIAC_SYMBOLS[index],
        sign_name=ZODIAC_NAMES[index],
        element=element,
        element_emoji=ELEMENT_EMOJI[element],
    )


def principal_phase_today(
    now: datetime | None = None,
) -> tuple[str, str, datetime] | None:
    """Retorna a fase principal que acontece hoje (se houver)."""
    tz = ZoneInfo("America/Sao_Paulo")
    local = (now or datetime.now(tz)).astimezone(tz)
    today = local.date()
    start_local = datetime(today.year, today.month, today.day, tzinfo=tz)
    start_utc = start_local.astimezone(UTC)
    start = astronomy.Time.Make(
        start_utc.year, start_utc.month, start_utc.day, start_utc.hour, 0, 0
    )
    for angle, key in (
        (0, "new"),
        (90, "first_quarter"),
        (180, "full"),
        (270, "last_quarter"),
    ):
        event = astronomy.SearchMoonPhase(angle, start, 2.0)
        if event is None:
            continue
        moment = event.Utc().replace(tzinfo=UTC).astimezone(tz)
        if moment.date() == today:
            return key, RITUALS[key][0], moment
    return None


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
