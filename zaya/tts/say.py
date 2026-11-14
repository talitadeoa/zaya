"""TTS offline com o comando nativo `say` (macOS)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from zaya.audio.player import AudioPlayer
from zaya.tts.base import TTSBackend


class SayTTSBackend(TTSBackend):
    name = "say"

    def __init__(self, voice: str, tmp_dir: Path):
        self.voice = voice
        self.tmp_dir = tmp_dir
        self._player = AudioPlayer()

    async def synthesize(self, text: str) -> Path:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=self.tmp_dir, suffix=".txt", delete=False
        ) as text_fh:
            text_fh.write(text.encode("utf-8"))
            text_path = Path(text_fh.name)
        with tempfile.NamedTemporaryFile(
            dir=self.tmp_dir, suffix=".aiff", delete=False
        ) as audio_fh:
            audio_path = Path(audio_fh.name)
        try:
            proc = await asyncio.create_subprocess_exec(
                "say",
                "-v",
                self.voice,
                "-f",
                str(text_path),
                "-o",
                str(audio_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"say terminou com codigo {proc.returncode}")
        except BaseException:
            self.cleanup(audio_path)
            raise
        finally:
            self.cleanup(text_path)
        return audio_path

    async def play(self, path: Path) -> None:
        await self._player.play(path)

    def stop(self) -> None:
        self._player.stop()
