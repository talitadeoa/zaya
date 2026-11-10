"""Sink de streaming que liga o orquestrador à interface Textual."""

from __future__ import annotations

import time
from typing import Protocol


class _StreamHost(Protocol):
    def update_stream(self, text: str) -> None: ...

    def _set_status(self, text: str) -> None: ...

    def write_diff(self, filename: str, patch: str) -> None: ...


class TextualSink:
    """Recebe tokens e eventos do orquestrador e atualiza a UI."""

    def __init__(self, app: _StreamHost):
        self.app = app
        self.buffer = ""
        self._last_update = 0.0
        self._last_reasoning = 0.0

    def token(self, text: str) -> None:
        self.buffer += text
        now = time.monotonic()
        if now - self._last_update >= 0.15:
            self._last_update = now
            self.app.update_stream(self.buffer)

    def reasoning(self, text: str) -> None:
        snippet = " ".join((text or "").split())[-90:]
        if not snippet:
            return
        now = time.monotonic()
        if now - self._last_reasoning >= 0.3:
            self._last_reasoning = now
            self.app._set_status(f"◐ {snippet}")

    def tool(self, name: str, detail: str = "") -> None:
        line = f"▸ {name} {detail}".strip()
        self.app._set_status(line)

    def diff(self, filename: str, patch: str) -> None:
        self.app.write_diff(filename, patch)
