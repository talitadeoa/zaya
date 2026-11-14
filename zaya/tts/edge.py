"""TTS via Edge TTS (online) + reproducao pelo player do sistema."""

from __future__ import annotations

import tempfile
from pathlib import Path

from zaya.audio.player import AudioPlayer
from zaya.tts.base import TTSBackend


class EdgeTTSBackend(TTSBackend):
    name = "edge"

    def __init__(self, voice: str, rate: str, tmp_dir: Path):
        self.voice = voice
        self.rate = rate
        self.tmp_dir = tmp_dir
        self._player = AudioPlayer()

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
        await self._player.play(path)

    def stop(self) -> None:
        self._player.stop()
