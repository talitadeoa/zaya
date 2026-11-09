"""Captura de microfone: push-to-talk e escuta continua com VAD.

Depende de sounddevice (PortAudio). Nenhum audio sai da maquina: a captura
fica em memoria e e entregue ao STT local.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from zora.config import AudioConfig

logger = logging.getLogger(__name__)


class AudioError(RuntimeError):
    pass


def resolve_input_device(sd, raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    needle = raw.lower()
    for index, device in enumerate(sd.query_devices()):
        if device.get("max_input_channels", 0) <= 0:
            continue
        if needle in device.get("name", "").lower():
            return index
    raise AudioError(f"dispositivo de entrada '{raw}' nao encontrado")


class UtteranceSegmenter:
    """Transforma um fluxo continuo de blocos de audio em falas completas.

    Regras: espera fala, inclui preroll, encerra depois de `silence_ms` de
    silencio (respeitando `min_speech_ms`) ou ao atingir `max_seconds`.
    """

    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self._preroll_max = max(1, int(cfg.preroll_ms / 30.0))
        self.reset()

    @property
    def speech_started(self) -> bool:
        return self._speech_started

    def reset(self) -> None:
        self._preroll: deque[np.ndarray] = deque(maxlen=self._preroll_max)
        self._chunks: list[np.ndarray] = []
        self._speech_started = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._utterance_ms = 0.0
        self._state_sent = False

    def feed(
        self,
        data: np.ndarray,
        on_state: Callable[[str], None] | None = None,
    ) -> np.ndarray | None:
        if data is None or data.size == 0:
            return None
        cfg = self.cfg
        block = data.astype(np.float32, copy=False).reshape(-1)
        block_ms = len(block) / cfg.sample_rate * 1000.0

        rms = float(np.sqrt(np.mean(np.square(block)) + 1e-12))
        db = 20.0 * float(np.log10(rms + 1e-9))
        voiced = db > cfg.speech_threshold_db

        if voiced and not self._speech_started:
            self._speech_started = True
            if self._preroll:
                self._chunks.extend(self._preroll)
                self._preroll.clear()
            if on_state and not self._state_sent:
                on_state("ouvindo")
                self._state_sent = True

        if not self._speech_started:
            self._preroll.append(block)
            return None

        self._chunks.append(block)
        self._utterance_ms += block_ms
        if voiced:
            self._speech_ms += block_ms
            self._silence_ms = 0.0
        else:
            self._silence_ms += block_ms

        finished = self._silence_ms >= cfg.silence_ms and self._speech_ms >= cfg.min_speech_ms
        too_long = cfg.max_seconds > 0 and self._utterance_ms >= cfg.max_seconds * 1000.0
        if finished or too_long:
            return self._emit()
        return None

    def start_manual(self) -> None:
        """Inicia uma fala manualmente (Enter), mesmo antes de detectar voz."""
        if self._speech_started:
            return
        self._speech_started = True
        if self._preroll:
            self._chunks.extend(self._preroll)
            self._preroll.clear()

    def snapshot(self, max_seconds: float | None = None) -> np.ndarray | None:
        """Audio capturado ate agora (para transcricao parcial ao vivo)."""
        if not self._chunks:
            return None
        audio = np.concatenate(self._chunks).astype(np.float32, copy=False)
        if max_seconds and max_seconds > 0:
            limit = int(self.cfg.sample_rate * max_seconds)
            if len(audio) > limit:
                audio = audio[-limit:]
        return audio

    def flush(self) -> np.ndarray | None:
        if self._speech_started and self._speech_ms >= self.cfg.min_speech_ms:
            return self._emit()
        self.reset()
        return None

    def _emit(self) -> np.ndarray | None:
        if not self._chunks:
            self.reset()
            return None
        audio = np.concatenate(self._chunks).astype(np.float32, copy=False)
        self.reset()
        return audio


class Recorder:
    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------ checks
    @staticmethod
    def available() -> tuple[bool, str]:
        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover - depende do sistema
            return False, f"sounddevice indisponivel: {exc}"
        try:
            devices = sd.query_devices()
            inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
            if not inputs:
                return False, "nenhum dispositivo de entrada encontrado"
            return True, f"{len(inputs)} dispositivo(s) de entrada"
        except Exception as exc:  # pragma: no cover
            return False, f"PortAudio indisponivel: {exc}"

    # ----------------------------------------------------------------- record
    def record_until_silence(
        self,
        on_state: Callable[[str], None] | None = None,
        wait_timeout: float | None = None,
    ) -> np.ndarray:
        """Grava uma fala isolada (push-to-talk). Retorna float32 mono @16k."""
        self._stop_event.clear()
        try:
            import sounddevice as sd
        except Exception as exc:
            raise AudioError(f"sounddevice indisponivel: {exc}") from exc

        cfg = self.cfg
        sr = cfg.sample_rate
        block = max(1, int(sr * 0.03))
        device = resolve_input_device(sd, cfg.input_device)
        frames: queue.Queue[np.ndarray] = queue.Queue()

        def callback(indata, frames_count, time_info, status):
            frames.put(indata.copy().reshape(-1))

        segmenter = UtteranceSegmenter(cfg)
        if on_state:
            on_state("aguardando fala")

        try:
            stream = sd.InputStream(
                samplerate=sr,
                channels=cfg.channels,
                dtype="float32",
                blocksize=block,
                device=device,
                callback=callback,
            )
        except Exception as exc:
            raise AudioError(f"nao foi possivel abrir o microfone: {exc}") from exc

        with stream:
            started_at = time.monotonic()
            deadline = started_at + cfg.max_seconds if cfg.max_seconds > 0 else None
            while deadline is None or time.monotonic() < deadline:
                if self._stop_event.is_set():
                    break
                if (
                    not segmenter.speech_started
                    and wait_timeout is not None
                    and time.monotonic() - started_at >= wait_timeout
                ):
                    break
                try:
                    data = frames.get(timeout=0.2)
                except queue.Empty:
                    continue
                utterance = segmenter.feed(data, on_state)
                if utterance is not None:
                    return utterance

        flushed = segmenter.flush()
        return flushed if flushed is not None else np.zeros(0, dtype=np.float32)


class ContinuousListener:
    """Mantem o microfone aberto e entrega falas segmentadas por VAD."""

    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self._frames: queue.Queue[np.ndarray] = queue.Queue()
        self._utterances: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._stream = None
        self._thread: threading.Thread | None = None
        self._segmenter = UtteranceSegmenter(cfg)
        self._seg_lock = threading.Lock()

    def start(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            raise AudioError(f"sounddevice indisponivel: {exc}") from exc

        cfg = self.cfg
        block = max(1, int(cfg.sample_rate * 0.03))
        device = resolve_input_device(sd, cfg.input_device)

        def callback(indata, frames_count, time_info, status):
            if self._stop.is_set() or self._paused.is_set():
                return
            self._frames.put(indata.copy().reshape(-1))

        try:
            stream = sd.InputStream(
                samplerate=cfg.sample_rate,
                channels=cfg.channels,
                dtype="float32",
                blocksize=block,
                device=device,
                callback=callback,
            )
            stream.start()
            self._stream = stream
        except Exception as exc:
            raise AudioError(f"nao foi possivel abrir o microfone: {exc}") from exc

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._paused.is_set():
                continue
            with self._seg_lock:
                utterance = self._segmenter.feed(data)
            if utterance is not None and len(utterance) > 0:
                self._utterances.put(utterance)
        self._utterances.put(None)

    def next_utterance(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            return self._utterances.get(timeout=timeout)
        except queue.Empty:
            return None

    def is_capturing(self) -> bool:
        with self._seg_lock:
            return self._segmenter.speech_started

    def snapshot(self, max_seconds: float | None = None) -> np.ndarray | None:
        with self._seg_lock:
            return self._segmenter.snapshot(max_seconds)

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def force_start(self) -> bool:
        """Enter: inicia uma fala manual (sem esperar o VAD detectar voz)."""
        if self._paused.is_set():
            return False
        with self._seg_lock:
            if self._segmenter.speech_started:
                return False
            self._segmenter.start_manual()
        return True

    def force_end(self) -> bool:
        """Enter: encerra a fala atual imediatamente e a envia para transcricao."""
        with self._seg_lock:
            utterance = self._segmenter.flush()
        if utterance is not None and len(utterance) > 0:
            self._utterances.put(utterance)
            return True
        return False

    def pause(self) -> None:
        self._paused.set()
        self._drain_frames()
        with self._seg_lock:
            self._segmenter.reset()

    def resume(self) -> None:
        with self._seg_lock:
            self._segmenter.reset()
        self._drain_frames()
        self._paused.clear()

    def stop(self) -> None:
        self._stop.set()
        self._paused.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.debug("falha ao fechar o stream de audio: %s", exc)
            self._stream = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._utterances.put(None)

    def _drain_frames(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return
