"""Entrada por voz: escuta continua, transcricao e controle de teclado."""

from __future__ import annotations

import asyncio
import contextlib
import os
import select
import signal
import sys
import threading
import time
from datetime import datetime

from zora.audio.recorder import AudioError, ContinuousListener, Recorder
from zora.controller.proactive import classify_voice_control
from zora.stt.faster_whisper import STTError


class VoiceMixin:
    async def _continuous_loop(self) -> bool:
        """Escuta continua real: microfone aberto + segmentacao por VAD/Enter."""
        listener = ContinuousListener(self.cfg.audio)
        try:
            listener.start()
        except AudioError as exc:
            self.ui.error(f"microfone: {exc}")
            self.cfg.audio.continuous = False
            return False

        self._listener = listener
        self._listening = True
        self._voice_mode = True
        self._mic_muted = False
        self._last_listen_at = time.monotonic()
        self._mark_activity()
        monitor_stop = threading.Event()
        monitor = threading.Thread(
            target=self._enter_monitor,
            args=(listener, monitor_stop),
            daemon=True,
        )
        monitor.start()
        if not self._continuous_hint_shown:
            self.ui.hint(
                "Escuta contínua: fale à vontade · [M] mudo · [Esc] interrompe · "
                "[T] ou digitar volta ao texto · o mic desliga no silêncio · "
                "'sair' encerra"
            )
            self._continuous_hint_shown = True
        self.ui.set_status("● ouvindo…")
        partial_task: asyncio.Task | None = None
        last_partial_at = 0.0
        try:
            while self.cfg.audio.continuous:
                now = datetime.now().astimezone()
                if self.cfg.proactive.reminders and self.store.due_reminders(now.isoformat()):
                    listener.pause()
                    try:
                        await self._fire_due_reminders()
                    finally:
                        if not self._mic_muted:
                            listener.resume()

                try:
                    utterance = await asyncio.to_thread(listener.next_utterance, 0.5)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    self._voice_mode = False
                    self.cfg.audio.continuous = False
                    self.ui.clear_status()
                    self.ui.warning("escuta contínua pausada; use /continuo on para retomar")
                    return False

                if utterance is None:
                    self._update_idle_status(listener)
                    self._maybe_auto_mute(listener)
                    if (
                        self.cfg.stt.partial_transcription
                        and listener.is_capturing()
                        and (partial_task is None or partial_task.done())
                        and time.monotonic() - last_partial_at
                        >= self.cfg.stt.partial_interval_seconds
                    ):
                        snapshot = listener.snapshot(self.cfg.stt.partial_window_seconds)
                        minimum = int(self.cfg.audio.sample_rate * 0.7)
                        if snapshot is not None and len(snapshot) >= minimum:
                            last_partial_at = time.monotonic()
                            partial_task = asyncio.create_task(self._partial_transcribe(snapshot))
                    continue

                if partial_task is not None and not partial_task.done():
                    with contextlib.suppress(Exception):
                        await partial_task
                partial_task = None
                self._partial_last = ""
                self._last_listen_at = time.monotonic()

                listener.pause()
                self._recording = True
                try:
                    text = await self._transcribe_audio(utterance)
                    if not text:
                        continue
                    control = classify_voice_control(text)
                    if control == "stop":
                        self._voice_mode = False
                        self.cfg.audio.continuous = False
                        self.ui.info("modo contínuo encerrado; pode digitar.")
                        return False
                    if control == "quit":
                        self.ui.info("até logo.")
                        return True
                    self._mark_activity()
                    assert self.conversation_id is not None
                    self.store.add_turn(self.conversation_id, "user", text)
                    try:
                        await self._dispatch(text)
                    except (KeyboardInterrupt, asyncio.CancelledError):
                        self.tts.stop()
                        self.ui.warning("interrompido")
                finally:
                    self._recording = False
                    if not self._mic_muted:
                        listener.resume()
                        self._last_listen_at = time.monotonic()
        finally:
            self.ui.clear_status()
            if partial_task is not None and not partial_task.done():
                with contextlib.suppress(Exception):
                    await partial_task
            monitor_stop.set()
            monitor.join(timeout=0.6)
            self._listening = False
            self._mic_muted = False
            listener.stop()
            self._listener = None
        return False

    def _enter_monitor(
        self,
        listener: ContinuousListener,
        stop: threading.Event,
    ) -> None:
        """Le teclas no terminal: Enter controla a fala e M pausa/retoma o mic."""
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError):
            return
        use_raw = sys.stdin.isatty()
        saved: list | None = None
        raw_active = False

        def enable_raw() -> None:
            nonlocal saved, raw_active
            if raw_active or not use_raw:
                return
            try:
                import termios
                import tty

                saved = termios.tcgetattr(fd)
                tty.setcbreak(fd)
                raw_active = True
            except Exception:
                saved = None
                raw_active = False

        def disable_raw() -> None:
            nonlocal saved, raw_active
            if not raw_active or saved is None:
                return
            try:
                import termios

                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            except Exception as exc:
                self.logger.debug("falha ao restaurar o terminal: %s", exc)
            saved = None
            raw_active = False

        try:
            while not stop.is_set():
                speaking = not self._speaking_event.is_set()
                if (
                    self._recording
                    or self._permission_active
                    or (self._busy and not speaking)
                    or (listener.is_paused() and not self._mic_muted and not speaking)
                ):
                    # Durante um turno o stdin pode estar em uso por um prompt
                    # de permissao: devolve o terminal ao modo normal.
                    disable_raw()
                    time.sleep(0.05)
                    continue
                enable_raw()
                try:
                    ready, _, _ = select.select([fd], [], [], 0.1)
                except (OSError, ValueError):
                    return
                if not ready:
                    continue
                try:
                    data = os.read(fd, 1)
                except (OSError, ValueError):
                    return
                if not data:
                    return
                char = data.decode("utf-8", "ignore")
                if char == "\x1b":
                    try:
                        more, _, _ = select.select([fd], [], [], 0.01)
                    except (OSError, ValueError):
                        more = []
                    if more:
                        # sequencia de seta ou tecla especial: ignora
                        try:
                            os.read(fd, 8)
                        except (OSError, ValueError):
                            pass
                        continue
                    self._interrupt_current()
                    continue
                if char in {"m", "M"}:
                    self._toggle_mute(listener)
                    continue
                if char in {"t", "T"}:
                    self.cfg.audio.continuous = False
                    self.ui.info("voltando ao modo texto; pode digitar.")
                    return
                if char in {"\r", "\n"}:
                    continue
                if char.strip():
                    self.tts.stop()
                    self._voice_mode = False
                    self.cfg.audio.continuous = False
                    self._pending_input = char
                    return
        finally:
            disable_raw()

    def _toggle_mute(self, listener: ContinuousListener) -> None:
        if self._mic_muted:
            self._mic_muted = False
            listener.resume()
            self._last_listen_at = time.monotonic()
            self.ui.success("escuta retomada")
            self.ui.set_status("● ouvindo…")
            return
        self._mic_muted = True
        listener.pause()
        self.ui.success("escuta pausada")
        self.ui.set_status("⏸ escuta pausada", "pressione M para voltar a ouvir")

    def _interrupt_current(self) -> None:
        """Esc: interrompe a fala e a execucao atual, voltando a ouvir."""
        if not self._busy:
            self.tts.stop()
            return
        self.ui.info("interrompendo…")
        self.tts.stop()
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except OSError:
            pass

    async def _read_user_input(self) -> str | None:
        # Se estamos pedindo texto, o modo voz fica desligado ate uma acao explicita.
        self._voice_mode = False
        self.cfg.audio.continuous = False
        prefix = self._pending_input
        self._pending_input = ""
        readline = None
        if prefix:
            try:
                import readline as _readline

                _readline.set_startup_hook(lambda: _readline.insert_text(prefix))
                readline = _readline
            except Exception:
                readline = None
        try:
            raw = await asyncio.to_thread(self.ui.prompt, "Talita")
        finally:
            if readline is not None:
                try:
                    readline.set_startup_hook()
                except Exception as exc:
                    self.logger.debug("falha ao limpar o readline: %s", exc)
        return raw.strip()

    def _voice_available(self) -> bool:
        if self._voice_ok is None:
            ok, msg = Recorder.available()
            self._voice_ok = ok
            self._voice_msg = msg
            if not ok:
                self.ui.warning(f"voz indisponível: {msg}")
        return bool(self._voice_ok)

    def _maybe_auto_mute(self, listener: ContinuousListener) -> None:
        seconds = self.cfg.audio.auto_mute_seconds
        if seconds <= 0 or self._mic_muted or self._busy or self._recording:
            return
        if not self._speaking_event.is_set() or listener.is_capturing():
            return
        if time.monotonic() - self._last_listen_at < seconds:
            return
        self._mic_muted = True
        listener.pause()
        self.ui.info(
            f"microfone desligado após {int(seconds)}s de silêncio; aperte M para retomar"
        )
        self.ui.set_status("⏸ microfone desligado", "M para retomar")

    def _update_idle_status(self, listener: ContinuousListener) -> None:
        if self._mic_muted:
            return
        if listener.is_capturing():
            if self._partial_last:
                return
            self.ui.set_status("● ouvindo…", "fala detectada")
        else:
            self.ui.set_status("● ouvindo…")

    async def _transcribe_audio(self, audio) -> str | None:
        self.ui.set_status("◐ transcrevendo…")
        async with self._stt_lock:
            try:
                text = await asyncio.to_thread(self.transcriber.transcribe, audio)
            except STTError as exc:
                self.ui.clear_status()
                self.ui.error(str(exc))
                return None
        if not text:
            self.ui.clear_status()
            return None
        self.ui.clear_status()
        self.ui.print_transcript(text)
        return text

    async def _partial_transcribe(self, audio) -> None:
        async with self._stt_lock:
            try:
                text = await asyncio.to_thread(self.transcriber.transcribe, audio, False)
            except Exception as exc:  # pragma: no cover
                self.logger.debug("transcricao parcial falhou: %s", exc)
                return
        if text and text != self._partial_last:
            self._partial_last = text
            self.ui.partial(text)

    async def _capture_voice(self, announce: bool = True) -> str | None:
        await self._speaking_event.wait()
        self._recording = True
        try:
            if announce:
                self.ui.set_status("● ouvindo…", "fale e faça uma pausa para encerrar")
            try:
                audio = await asyncio.to_thread(self.recorder.record_until_silence)
            except AudioError as exc:
                self.ui.clear_status()
                self.ui.error(f"microfone: {exc}")
                return None
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.recorder.stop()
                self.ui.clear_status()
                raise
            if audio is None or len(audio) == 0:
                self.ui.clear_status()
                return None
            return await self._transcribe_audio(audio)
        finally:
            self._recording = False
            self.ui.clear_status()

    async def _voice_input(self) -> str | None:
        text = await self._capture_voice(announce=True)
        if text is None:
            return None
        if not self.cfg.stt.confirm_transcript:
            return text
        try:
            correction = await asyncio.to_thread(self.ui.prompt, "Enter aceitar / correção")
        except EOFError:
            correction = ""
        if correction.strip():
            return correction.strip()
        return text
