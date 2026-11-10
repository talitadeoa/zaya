"""STT local com faster-whisper (portugues), CPU/int8 para macOS Intel."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

from zaya.config import STTConfig

SAMPLE_RATE = 16000


class STTError(RuntimeError):
    pass


class WhisperTranscriber:
    def __init__(self, cfg: STTConfig):
        self.cfg = cfg
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # pragma: no cover - depende do ambiente
            raise STTError(
                "faster-whisper nao esta instalado corretamente. "
                "Rode 'zaya doctor' e reinstale com Python 3.12."
            ) from exc
        try:
            self._model = WhisperModel(
                self.cfg.model,
                device=self.cfg.device,
                compute_type=self.cfg.compute_type,
            )
        except Exception as exc:  # pragma: no cover
            raise STTError(
                f"nao foi possivel carregar o modelo '{self.cfg.model}': {exc}. "
                "O primeiro uso baixa o modelo do Hugging Face e requer internet."
            ) from exc
        return self._model

    def warmup(self) -> None:
        self._ensure_model()

    def transcribe(
        self,
        audio: np.ndarray,
        vad_filter: bool | None = None,
    ) -> str:
        if audio is None or len(audio) == 0:
            return ""
        model = self._ensure_model()
        if audio.dtype != np.float32:
            if np.issubdtype(audio.dtype, np.integer):
                audio = audio.astype(np.float32) / 32768.0
            else:
                audio = audio.astype(np.float32)
        audio = np.ascontiguousarray(audio.reshape(-1))
        use_vad = self.cfg.vad_filter if vad_filter is None else vad_filter
        try:
            segments, _info = model.transcribe(
                audio,
                language=self.cfg.language,
                beam_size=self.cfg.beam_size,
                vad_filter=use_vad,
                condition_on_previous_text=False,
            )
            return " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
        except Exception as exc:  # pragma: no cover
            raise STTError(f"falha na transcricao: {exc}") from exc

    def _decode_audio(self, path: str | Path) -> np.ndarray:
        """Decodifica qualquer formato suportado pelo ffmpeg para float32 mono 16 kHz.

        O PyAV embutido no faster-whisper e incompativel com a versao do PyAV
        instalada (wheels recentes), entao nao entregamos o arquivo direto ao
        modelo: convertemos para PCM antes.
        """
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise STTError(
                "ffmpeg nao encontrado no PATH; e necessario para decodificar "
                "audios do Telegram."
            )
        cmd = [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise STTError(f"falha ao executar ffmpeg: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise STTError(f"ffmpeg nao conseguiu decodificar o audio: {detail}")
        return np.frombuffer(result.stdout, dtype=np.float32).copy()

    def transcribe_file(self, path: str | Path) -> str:
        audio = self._decode_audio(path)
        return self.transcribe(audio)
