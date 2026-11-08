from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class TTSBackend(ABC):
    name = "base"

    @abstractmethod
    async def synthesize(self, text: str) -> Path:
        """Gera o audio em disco sem reproduzir."""
        raise NotImplementedError

    @abstractmethod
    async def play(self, path: Path) -> None:
        """Reproduz um audio ja sintetizado."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    def cleanup(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    async def speak(self, text: str) -> None:
        path = await self.synthesize(text)
        try:
            await self.play(path)
        finally:
            self.cleanup(path)
