"""TTS via Edge TTS (online) + reproducao com afplay (macOS)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from zaya.tts.base import TTSBackend


class EdgeTTSBackend(TTSBackend):
    name = "edge"

    def __init__(self, voice: str, rate: str, tmp_dir: Path):
        self.voice = voice
        self.rate = rate
        self.tmp_dir = tmp_dir
        self._proc: asyncio.subprocess.Process | None = None

    async def synthesize(self, text: str) -> Path:
        import edge_tts

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.tmp_dir, suffix=".mp3", delete=False) as fh:
            tmp = Path(fh.name)
        try:
            communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
            await communicate.save(str(tmp))
        except BaseException:
            self.cleanup(tmp)
            raise
        return tmp

    async def play(self, path: Path) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "afplay",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self._proc.wait()

    def stop(self) -> None:
        proc = self._proc
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
