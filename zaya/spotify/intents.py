"""Reconhecimento de comandos de Spotify em portugues."""

from __future__ import annotations

import re

from zaya.spotify.controller import SpotifyCommand

_M = r"(?:m[úu]sica|spotify|faixa|track)"
_PREFIX = r"^\s*(?:zaya[,\s]+)?"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            _PREFIX + r"(?:volume|coloca\s+o\s+volume\s+em|define\s+o\s+volume\s+em)"
            r"\s+(?:do\s+spotify\s+)?(?:em\s+)?(\d{1,3})\s*%?\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        "volume_set",
    ),
    (
        re.compile(
            _PREFIX + r"(?:aumenta|aumentar|sobe|subir|aumenta\s+o)\s+(?:o\s+)?volume"
            r"(?:\s+do\s+spotify)?\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        "volume_up",
    ),
    (
        re.compile(
            _PREFIX + r"(?:abaixa|abaixar|diminui|diminuir|baixa|baixar)\s+(?:o\s+)?volume"
            r"(?:\s+do\s+spotify)?\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        "volume_down",
    ),
    (
        re.compile(
            _PREFIX + r"(?:que|qual)\s+m[úu]sica\s+(?:est[áa]\s+)?tocando\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        "status",
    ),
    (
        re.compile(_PREFIX + r"spotify(?:\s+status)?\s*[.!?]?\s*$", re.IGNORECASE),
        "status",
    ),
    (
        re.compile(
            _PREFIX
            + r"(?:pr[óo]xima|pula|pular|avan[çc]a|avan[çc]ar|next)\s+"
            r"(?:a\s+)?" + _M + r"\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        "next",
    ),
    (
        re.compile(
            _PREFIX + r"(?:" + _M + r"\s+)?(?:anterior|previous)\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        "previous",
    ),
    (
        re.compile(
            _PREFIX
            + r"(?:pausa|pause|pausar|para)\s+(?:a\s+|o\s+)?(?:m[úu]sica|spotify)"
            r"\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        "pause",
    ),
    (
        re.compile(
            _PREFIX
            + r"(?:toca|tocar|play|continua|continuar|retoma|retomar)\s+"
            r"(?:a\s+|o\s+)?(?:m[úu]sica|spotify)\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        "play",
    ),
]


def parse_spotify_command(text: str) -> SpotifyCommand | None:
    if not text:
        return None
    cleaned = " ".join(text.strip().split())
    for pattern, action in _PATTERNS:
        match = pattern.match(cleaned)
        if not match:
            continue
        value = None
        if action == "volume_set":
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
        return SpotifyCommand(action=action, value=value)
    return None
