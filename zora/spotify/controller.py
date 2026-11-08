"""Controle do Spotify no macOS via AppleScript."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class SpotifyError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpotifyCommand:
    action: str
    value: int | None = None


APP = "Spotify"


def is_installed() -> bool:
    candidates = [
        Path("/Applications/Spotify.app"),
        Path.home() / "Applications" / "Spotify.app",
    ]
    return any(path.exists() for path in candidates)


def _run(script: str) -> str:
    if not shutil.which("osascript"):
        raise SpotifyError("osascript nao encontrado")
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise SpotifyError("o Spotify demorou a responder") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise SpotifyError(message or "falha ao controlar o Spotify")
    return result.stdout.strip()


def _tell(command: str) -> str:
    return _run(f'tell application "{APP}" to {command}')


def status() -> str:
    if not is_installed():
        raise SpotifyError("Spotify nao instalado neste Mac")
    try:
        state = _tell("player state as string")
    except SpotifyError:
        return "Spotify fechado"
    if state == "stopped":
        return "Spotify parado"
    if state == "paused":
        return "Spotify pausado"
    try:
        track = _tell("name of current track")
        artist = _tell("artist of current track")
    except SpotifyError:
        return "Spotify tocando"
    return f"Tocando: {track} — {artist}"


def execute(command: SpotifyCommand) -> str:
    if not is_installed():
        raise SpotifyError("Spotify nao instalado neste Mac")
    action = command.action
    if action == "toggle":
        _tell("playpause")
        return "Play/pause enviado ao Spotify"
    if action == "play":
        _tell("play")
        return "Spotify tocando"
    if action == "pause":
        _tell("pause")
        return "Spotify pausado"
    if action == "next":
        _tell("next track")
        return "Próxima faixa"
    if action == "previous":
        _tell("previous track")
        return "Faixa anterior"
    if action == "volume_set":
        value = max(0, min(100, int(command.value or 0)))
        _tell(f"set sound volume to {value}")
        return f"Volume do Spotify em {value}%"
    if action == "volume_up":
        current = int(_tell("sound volume") or 50)
        value = min(100, current + 10)
        _tell(f"set sound volume to {value}")
        return f"Volume do Spotify em {value}%"
    if action == "volume_down":
        current = int(_tell("sound volume") or 50)
        value = max(0, current - 10)
        _tell(f"set sound volume to {value}")
        return f"Volume do Spotify em {value}%"
    if action == "status":
        return status()
    raise SpotifyError(f"acao desconhecida: {action}")
