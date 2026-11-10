"""Lembretes em portugues e regras de proatividade (quiet hours, saudacao)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

NUMBER_WORDS: dict[str, float] = {
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "três": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
    "quinze": 15,
    "vinte": 20,
    "trinta": 30,
    "meia": 0.5,
}

UNIT_SECONDS: dict[str, float] = {
    "segundo": 1,
    "segundos": 1,
    "seg": 1,
    "minuto": 60,
    "minutos": 60,
    "min": 60,
    "mins": 60,
    "hora": 3600,
    "horas": 3600,
    "h": 3600,
    "dia": 86400,
    "dias": 86400,
}

PREFIX_RE = re.compile(
    r"^\s*(?:zaya[,\s]+)?"
    r"(?:(?:me\s+)?lembr(?:e|a|ar)(?:-me)?|(?:me\s+)?avise(?:-me)?|lembrete)"
    r"\s*[:,]?\s*(?:de\s+|que\s+)?",
    re.IGNORECASE,
)

_NUM = r"(?:\d+(?:[.,]\d+)?|um|uma|dois|duas|tr[êe]s|quatro|cinco|seis|sete|oito|nove|dez|quinze|vinte|trinta|meia)"

REL_RE = re.compile(
    r"\b(?:em|daqui\s+a|daqui\s+à)\s+(" + _NUM + r")\s*"
    r"(segundos?|seg|minutos?|mins?|min|horas?|h|dias?)\b",
    re.IGNORECASE,
)

AMANHA_RE = re.compile(r"\bamanh[ãa]\b", re.IGNORECASE)
HOJE_RE = re.compile(r"\bhoje\b", re.IGNORECASE)
TIME_RE = re.compile(r"\b(?:às?|as)\s*(\d{1,2})(?::(\d{2}))?\s*h?\b", re.IGNORECASE)
MEIO_DIA_RE = re.compile(r"\b(?:ao\s+|de\s+)?meio[-\s]?dia\b", re.IGNORECASE)
MEIA_NOITE_RE = re.compile(r"\b(?:à\s+|a\s+|de\s+)?meia[-\s]?noite\b", re.IGNORECASE)


@dataclass
class ReminderSpec:
    label: str
    due: datetime


def _to_number(token: str) -> float | None:
    token = token.strip().lower().replace(",", ".")
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token) if "." in token else int(token)
    return NUMBER_WORDS.get(token)


def _unit_seconds(unit: str) -> float | None:
    return UNIT_SECONDS.get(unit.strip().lower())


def _valid_time(hour: int, minute: int) -> bool:
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _at(base: datetime, hour: int, minute: int) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _absolute(body: str, now: datetime) -> tuple[datetime, tuple[int, int]] | None:
    amanha = AMANHA_RE.search(body)
    if amanha:
        base = now + timedelta(days=1)
        hour, minute = 9, 0
        time_span = None
        md = MEIO_DIA_RE.search(body)
        mn = MEIA_NOITE_RE.search(body)
        tm = TIME_RE.search(body)
        if md:
            hour, minute, time_span = 12, 0, md.span()
        elif mn:
            hour, minute, time_span = 0, 0, mn.span()
        elif tm:
            hour, minute = int(tm.group(1)), int(tm.group(2) or 0)
            time_span = tm.span()
        if not _valid_time(hour, minute):
            return None
        end = max(amanha.end(), time_span[1] if time_span else amanha.end())
        return _at(base, hour, minute), (amanha.start(), end)

    hoje = HOJE_RE.search(body)
    if hoje:
        tm = TIME_RE.search(body)
        md = MEIO_DIA_RE.search(body)
        mn = MEIA_NOITE_RE.search(body)
        if md:
            hour, minute, time_span = 12, 0, md.span()
        elif mn:
            hour, minute, time_span = 0, 0, mn.span()
        elif tm:
            hour, minute = int(tm.group(1)), int(tm.group(2) or 0)
            time_span = tm.span()
        else:
            return None
        if not _valid_time(hour, minute):
            return None
        end = max(hoje.end(), time_span[1])
        return _at(now, hour, minute), (hoje.start(), end)

    for regex, hour, minute in (
        (MEIO_DIA_RE, 12, 0),
        (MEIA_NOITE_RE, 0, 0),
    ):
        match = regex.search(body)
        if match:
            due = _at(now, hour, minute)
            if due <= now:
                due += timedelta(days=1)
            return due, match.span()

    match = TIME_RE.search(body)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if not _valid_time(hour, minute):
            return None
        due = _at(now, hour, minute)
        if due <= now:
            due += timedelta(days=1)
        return due, match.span()

    return None


def _clean_label(body: str, span: tuple[int, int]) -> str:
    label = (body[: span[0]] + " " + body[span[1] :]).strip(" ,.;:-")
    label = re.sub(r"\s+", " ", label)
    label = re.sub(r"\s+(?:para|de|às?|as|em)$", "", label, flags=re.IGNORECASE)
    label = label.strip(" ,.;:-")
    return label or "lembrete"


def parse_reminder(
    text: str,
    now: datetime | None = None,
    require_prefix: bool = True,
) -> ReminderSpec | None:
    """Interpreta 'me lembre de X em 30 minutos', 'amanhã às 9h', etc.

    Com `require_prefix=True` (padrao), so aceita frases que comecem com
    "lembre/avise/lembrete" para evitar transformar conversa comum em lembrete.
    """
    if not text or not text.strip():
        return None
    now = now or datetime.now().astimezone()
    raw = text.strip()
    if require_prefix and not PREFIX_RE.match(raw):
        return None
    body = PREFIX_RE.sub("", raw).strip()
    body = re.sub(r"^(?:de|que)\s+", "", body, flags=re.IGNORECASE).strip()
    if not body:
        return None

    match = REL_RE.search(body)
    if match:
        quantity = _to_number(match.group(1))
        seconds = _unit_seconds(match.group(2))
        if quantity is not None and seconds is not None:
            due = now + timedelta(seconds=float(quantity) * seconds)
            return ReminderSpec(label=_clean_label(body, match.span()), due=due)

    absolute = _absolute(body, now)
    if absolute:
        due, span = absolute
        return ReminderSpec(label=_clean_label(body, span), due=due)
    return None


VOICE_STOP_RE = re.compile(
    r"^\s*(?:zaya[,\s]+)?(?:parar|pare|para|pausar|pause|modo\s+texto|texto|voltar)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
VOICE_QUIT_RE = re.compile(
    r"^\s*(?:zaya[,\s]+)?(?:sair|tchau|encerrar|desligar|fechar)\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def classify_voice_control(text: str) -> str | None:
    """Classifica comandos de voz para sair do modo continuo."""
    if VOICE_STOP_RE.match(text or ""):
        return "stop"
    if VOICE_QUIT_RE.match(text or ""):
        return "quit"
    return None


def in_quiet_hours(now: datetime, spec: str) -> bool:
    if not spec or "-" not in spec:
        return False
    start_raw, end_raw = spec.split("-", 1)
    try:
        sh, sm = (int(part) for part in start_raw.strip().split(":", 1))
        eh, em = (int(part) for part in end_raw.strip().split(":", 1))
    except (ValueError, TypeError):
        return False
    if not (_valid_time(sh, sm) and _valid_time(eh, em)):
        return False
    current = now.hour * 60 + now.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def greeting_for(now: datetime) -> str:
    hour = now.hour
    if 5 <= hour < 12:
        return "Bom dia"
    if 12 <= hour < 18:
        return "Boa tarde"
    return "Boa noite"
