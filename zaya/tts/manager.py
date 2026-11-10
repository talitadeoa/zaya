"""Selecao e orquestracao do TTS, com limpeza de Markdown e fallback offline."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from zaya.config import TTSConfig
from zaya.tts.base import TTSBackend
from zaya.tts.edge import EdgeTTSBackend
from zaya.tts.say import SayTTSBackend


def clean_for_speech(text: str, max_chars: int = 1200) -> str:
    if not text:
        return ""
    text = re.sub(r"```.*?```", " bloco de codigo omitido. ", text, flags=re.S)
    text = re.sub(r"~~~.*?~~~", " bloco de codigo omitido. ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~>|#]", "", text)
    text = re.sub(r"\bZaya\b", "Záia", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        position = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        if position > max_chars * 0.5:
            cut = cut[: position + 1]
        text = cut.rstrip() + " ..."
    return text


def split_for_speech(text: str, max_chars: int = 1200) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = max(
            window.rfind(". "),
            window.rfind("! "),
            window.rfind("? "),
            window.rfind("\n"),
        )
        if cut <= 0:
            cut = max_chars
        chunks.append(remaining[: cut + 1].strip())
        remaining = remaining[cut + 1 :].strip()
    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]


class TTSManager:
    def __init__(self, cfg: TTSConfig, logger: logging.Logger, tmp_dir: Path):
        self.cfg = cfg
        self.logger = logger
        self.tmp_dir = tmp_dir
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._backend = self._build_backend(cfg.provider)
        self._fallback: TTSBackend | None = None
        self._stopped = False

    @property
    def provider(self) -> str:
        return self.cfg.provider

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled and self.cfg.provider != "none"

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self.cfg.enabled = value

    def _build_backend(self, provider: str) -> TTSBackend | None:
        if provider == "edge":
            return EdgeTTSBackend(self.cfg.edge_voice, self.cfg.rate, self.tmp_dir)
        if provider == "say":
            return SayTTSBackend(self.cfg.say_voice, self.tmp_dir)
        if provider == "none":
            return None
        self.logger.warning("provedor de TTS desconhecido '%s'; usando edge", provider)
        return EdgeTTSBackend(self.cfg.edge_voice, self.cfg.rate, self.tmp_dir)

    def set_provider(self, provider: str) -> None:
        self.stop()
        self.cfg.provider = provider
        self._backend = self._build_backend(provider)

    async def prepare(self, text: str) -> tuple[TTSBackend, Path] | None:
        """Sintetiza o audio sem reproduzir, para permitir prefetch."""
        if not self.enabled:
            return None
        clean = clean_for_speech(text, self.cfg.max_chars)
        if not clean or self._backend is None:
            return None
        try:
            return self._backend, await self._backend.synthesize(clean)
        except Exception as exc:
            self.logger.warning("TTS '%s' falhou: %s", self._backend.name, exc)
            if self._backend.name != "edge":
                return None
            self._fallback = self._fallback or SayTTSBackend(self.cfg.say_voice, self.tmp_dir)
            try:
                return self._fallback, await self._fallback.synthesize(clean)
            except Exception as fallback_exc:
                self.logger.warning("fallback say falhou: %s", fallback_exc)
                return None

    async def play_prepared(self, prepared: tuple[TTSBackend, Path]) -> None:
        backend, path = prepared
        try:
            await backend.play(path)
        finally:
            backend.cleanup(path)

    async def speak(self, text: str) -> None:
        if not self.enabled:
            return
        clean = clean_for_speech(text, self.cfg.max_chars)
        if not clean:
            return
        self._stopped = False
        for chunk in split_for_speech(clean, self.cfg.max_chars):
            if self._stopped or self._backend is None:
                break
            try:
                await self._backend.speak(chunk)
            except Exception as exc:
                self.logger.warning("TTS '%s' falhou: %s", self._backend.name, exc)
                if self._backend.name != "edge":
                    return
                self._fallback = self._fallback or SayTTSBackend(self.cfg.say_voice, self.tmp_dir)
                try:
                    await self._fallback.speak(chunk)
                except Exception as fallback_exc:
                    self.logger.warning("fallback say falhou: %s", fallback_exc)
                    return

    def stop(self) -> None:
        self._stopped = True
        for backend in (self._backend, self._fallback):
            if backend:
                try:
                    backend.stop()
                except Exception as exc:
                    self.logger.debug("falha ao parar backend TTS: %s", exc)
