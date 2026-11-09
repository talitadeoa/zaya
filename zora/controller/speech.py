"""Fala e streaming de texto (TTS) do orquestrador."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from pathlib import Path

from zora.interface.markdown_text import strip_markdown
from zora.tts.base import TTSBackend
from zora.tts.manager import clean_for_speech

# Fim de frase para falar o texto conforme ele chega (pontuacao + espaco/fim).
_SENTENCE_END = re.compile(r"[.!?\n](\s+|$)")


class SpeechMixin:
    def _start_speech_streaming(self) -> bool:
        """Fala a resposta por frases conforme o texto faz streaming."""
        if not self.tts.enabled:
            return False
        self._speech_buffer = ""
        self._speech_pending = ""
        self._speech_fed_chars = 0
        self._speech_active = True
        self._speech_queue = asyncio.Queue()
        self._speech_task = asyncio.create_task(self._speech_worker())
        return True

    def _feed_speech(self, text: str) -> None:
        if not self._speech_active or not text:
            return
        self._speech_buffer += text
        for sentence in self._flush_sentences():
            clean = clean_for_speech(sentence)
            if clean:
                self._speech_fed_chars += len(clean)
                self._enqueue_speech(clean)

    def _enqueue_speech(self, text: str) -> None:
        self._speech_pending = ""
        if self._speech_queue is not None and text:
            self._speech_queue.put_nowait(text)

    def _flush_sentences(self) -> list[str]:
        buf = self._speech_buffer
        out: list[str] = []
        while True:
            match = _SENTENCE_END.search(buf)
            if match is None:
                break
            sentence = buf[: match.start() + 1].strip()
            buf = buf[match.end() :]
            if sentence:
                out.append(sentence)
        self._speech_buffer = buf
        return out

    def _take_display_units(self, force: bool = False) -> list[str]:
        """Separa o texto ja exibivel em frases/linhas (preserva quebras)."""
        buf = self._answer_buffer
        units: list[str] = []
        while True:
            match = _SENTENCE_END.search(buf)
            if match is None:
                break
            end = match.end()
            units.append(buf[:end])
            buf = buf[end:]
        if force:
            if buf.strip():
                units.append(buf)
            buf = ""
        elif len(buf) > 240:
            cut = buf.rfind(" ", 0, 240)
            if cut <= 0:
                cut = 240
            units.append(buf[: cut + 1])
            buf = buf[cut + 1 :]
        self._answer_buffer = buf
        return units

    def _feed_display(self, delta: str) -> list[str]:
        self._answer_buffer += delta
        cleaned: list[str] = []
        for unit in self._take_display_units():
            text = strip_markdown(unit)
            if text.strip():
                cleaned.append(text)
        return cleaned

    def _flush_display(self) -> list[str]:
        cleaned: list[str] = []
        for unit in self._take_display_units(force=True):
            text = strip_markdown(unit)
            if text.strip():
                cleaned.append(text)
        return cleaned

    async def _speech_worker(self) -> None:
        queue = self._speech_queue
        if queue is None:
            return
        chunk = await queue.get()
        prepared = await self._prepare_speech(chunk)
        play_task: asyncio.Task | None = None
        try:
            while chunk is not None:
                play_task = asyncio.create_task(self._play_prepared(prepared))
                nxt = await queue.get()
                prepared_next = await self._prepare_speech(nxt)
                await play_task
                play_task = None
                if nxt is not None and self.cfg.tts.sentence_pause_ms > 0:
                    await asyncio.sleep(self.cfg.tts.sentence_pause_ms / 1000.0)
                chunk = nxt
                prepared = prepared_next
        finally:
            if play_task is not None and not play_task.done():
                play_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await play_task

    async def _prepare_speech(
        self, chunk: str | None
    ) -> tuple[TTSBackend, Path] | None:
        if chunk is None:
            return None
        try:
            return await self.tts.prepare(chunk)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.tts.stop()
            raise
        except Exception as exc:  # pragma: no cover
            self.logger.warning("falha no TTS: %s", exc)
            return None

    async def _play_prepared(self, prepared: tuple[TTSBackend, Path] | None) -> None:
        if prepared is None:
            return
        async with self._tts_lock:
            self._speaking_event.clear()
            try:
                await self.tts.play_prepared(prepared)
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.tts.stop()
                raise
            except Exception as exc:  # pragma: no cover
                self.logger.warning("falha na reproducao do TTS: %s", exc)
            finally:
                self._speaking_event.set()

    async def _finish_speech_streaming(self, fallback: str) -> None:
        if not self._speech_active:
            return
        tail = self._speech_buffer.strip()
        clean_tail = clean_for_speech(tail) if tail else ""
        if clean_tail:
            self._speech_fed_chars += len(clean_tail)
            self._speech_pending = f"{self._speech_pending} {clean_tail}".strip()
        if self._speech_fed_chars == 0 and fallback:
            clean_tail = clean_for_speech(fallback)
            self._speech_fed_chars += len(clean_tail)
            self._speech_pending = f"{self._speech_pending} {clean_tail}".strip()
        queue = self._speech_queue
        if queue is not None:
            if self._speech_pending:
                await queue.put(self._speech_pending)
                self._speech_pending = ""
            await queue.put(None)
            task = self._speech_task
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._speech_active = False
        self._speech_task = None
        self._speech_queue = None
        self._speech_buffer = ""
        self._speech_pending = ""

    async def _stop_speech_streaming(self) -> None:
        if not self._speech_active:
            return
        self._speech_active = False
        self.tts.stop()
        task = self._speech_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._speech_task = None
        self._speech_queue = None
        self._speech_buffer = ""
        self._speech_pending = ""

    async def _speak(self, text: str) -> None:
        if not self.tts.enabled:
            return
        listener = self._listener
        resume_after = False
        if (
            listener is not None
            and not self._mic_muted
            and not self._recording
            and not listener.is_paused()
        ):
            listener.pause()
            resume_after = True
        try:
            async with self._tts_lock:
                self._speaking_event.clear()
                try:
                    await self.tts.speak(text)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    self.tts.stop()
                    raise
                except Exception as exc:  # pragma: no cover
                    self.logger.warning("falha no TTS: %s", exc)
                finally:
                    self._speaking_event.set()
        finally:
            if resume_after and listener is not None and not self._mic_muted:
                listener.resume()
                self._last_listen_at = time.monotonic()
