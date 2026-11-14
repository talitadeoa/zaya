"""Reproducao de audio local, com o player certo para cada plataforma.

No macOS o padrao e o `afplay`. No Linux (inclusive tablet) escolhemos o
primeiro disponivel entre ffplay, paplay, pw-play, mpv e aplay. `aplay` fica
por ultimo porque so toca WAV, enquanto o Edge TTS entrega MP3.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path


class AudioPlayerError(RuntimeError):
    pass


def _candidates() -> list[tuple[str, list[str]]]:
    if sys.platform == "darwin":
        return [("afplay", ["afplay"]), ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"])]
    if sys.platform.startswith("linux"):
        return [
            ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
            ("pw-play", ["pw-play"]),
            ("paplay", ["paplay"]),
            ("mpv", ["mpv", "--no-video", "--really-quiet"]),
            ("aplay", ["aplay", "-q"]),
        ]
    return [("afplay", ["afplay"]), ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"])]


def find_player() -> tuple[str, list[str]] | None:
    """Primeiro player de audio instalado, ou None."""
    for name, command in _candidates():
        if shutil.which(name):
            return name, command
    return None


def player_name() -> str:
    found = find_player()
    return found[0] if found else ""


class AudioPlayer:
    """Toca um arquivo e permite interromper (usado pelo TTS e por avisos)."""

    def __init__(self) -> None:
        found = find_player()
        self._name = found[0] if found else ""
        self._command = found[1] if found else None
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def available(self) -> bool:
        return self._command is not None

    @property
    def name(self) -> str:
        return self._name

    async def play(self, path: Path) -> None:
        if not self._command:
            raise AudioPlayerError(
                "nenhum player de audio encontrado (instale ffmpeg ou pulseaudio-utils)"
            )
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await self._proc.wait()
            if self._proc.returncode not in (0, None) and self._proc.returncode != -15:
                raise AudioPlayerError(
                    f"{self._name} terminou com codigo {self._proc.returncode}"
                )
        finally:
            self._proc = None

    def stop(self) -> None:
        proc = self._proc
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
