"""Serviço que escuta o Telegram e encaminha mensagens para a Zaya."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from zaya.config import Config
from zaya.controller.orchestrator import Orchestrator
from zaya.interface.terminal import TerminalUI
from zaya.stt.faster_whisper import STTError, WhisperTranscriber
from zaya.telegram.client import TelegramClient, TelegramError


class TelegramService:
    def __init__(
        self,
        cfg: Config,
        orchestrator: Orchestrator,
        client: TelegramClient,
        transcriber: WhisperTranscriber,
        ui: TerminalUI,
        logger: logging.Logger,
    ):
        self.cfg = cfg
        self.orchestrator = orchestrator
        self.client = client
        self.transcriber = transcriber
        self.ui = ui
        self.logger = logger
        self._chat_id: str | None = None

    async def run(self) -> None:
        me = await self.client.get_me()
        username = me.get("username", "?") if isinstance(me, dict) else "?"
        self.ui.info(f"Telegram conectado: @{username}")
        self.ui.hint("enviando mensagens; Ctrl+C encerra")
        # Briefings diarios rodam mesmo sem mensagem recebida.
        self.orchestrator.start_briefing()
        offset: int | None = None
        while True:
            try:
                updates = await self.client.get_updates(offset=offset)
            except TelegramError as exc:
                self.logger.warning("getUpdates falhou: %s", exc)
                await asyncio.sleep(2.0)
                continue
            for update in updates:
                offset = int(update.get("update_id", 0)) + 1
                try:
                    await self._handle_update(update)
                except Exception as exc:  # pragma: no cover - depende da rede/agente
                    self.logger.exception("falha ao processar update")
                    await self._safe_reply(update, f"Deu erro aqui: {exc}")
            await asyncio.sleep(0.05)

    def _allowed(self, chat_id: Any) -> bool:
        allowed = str(self.cfg.telegram.allowed_chat_id or "").strip()
        if allowed:
            return str(chat_id) == allowed
        if self._chat_id is None:
            self._chat_id = str(chat_id)
            self.ui.info(f"chat autorizado automaticamente: {self._chat_id}")
        return str(chat_id) == self._chat_id

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is None or not self._allowed(chat_id):
            return
        text = (message.get("text") or "").strip()
        if text in {"/start", "/iniciar"}:
            await self.client.send_message(
                "Oi! Sou a Zaya. Manda texto ou áudio que eu executo e te aviso "
                "aqui quando terminar.",
                chat_id=chat_id,
            )
            return
        if not text:
            text = await self._transcribe_message(message)
        if not text:
            return

        await self.client.send_chat_action(chat_id, "typing")
        await self.client.send_message("⏳ trabalhando nisso…", chat_id=chat_id)
        try:
            answer = await self.orchestrator.answer_once(text, speak=False, close=False)
        except Exception as exc:
            self.logger.exception("falha no agente")
            answer = f"Deu erro aqui: {exc}"

        if answer:
            await self.client.send_message(answer, chat_id=chat_id)
            if self.cfg.telegram.voice_replies:
                await self._send_voice(answer, chat_id)
        else:
            await self.client.send_message("Concluído.", chat_id=chat_id)

    async def _transcribe_message(self, message: dict[str, Any]) -> str:
        media = message.get("voice") or message.get("audio")
        if not media:
            return ""
        file_id = media.get("file_id")
        if not file_id:
            return ""
        self.ui.info("baixando áudio…")
        file_path = await self.client.get_file(str(file_id))
        data = await self.client.download_file(file_path)
        mime = str(media.get("mime_type") or "")
        if "ogg" in mime:
            suffix = ".ogg"
        elif "m4a" in mime or "mp4" in mime:
            suffix = ".m4a"
        else:
            suffix = ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(data)
            tmp = Path(fh.name)
        try:
            self.ui.info(f"transcrevendo áudio ({len(data)} bytes)…")
            return await asyncio.to_thread(self.transcriber.transcribe_file, tmp)
        except STTError as exc:
            self.logger.warning("transcrição falhou: %s", exc)
            return ""
        finally:
            tmp.unlink(missing_ok=True)

    async def _send_voice(self, text: str, chat_id: Any) -> None:
        prepared = await self.orchestrator.tts.prepare(text)
        if prepared is None:
            return
        backend, path = prepared
        try:
            await self.client.send_audio(path, chat_id=chat_id, as_voice=True)
        finally:
            backend.cleanup(path)

    async def _safe_reply(self, update: dict[str, Any], text: str) -> None:
        chat_id = ((update.get("message") or {}).get("chat") or {}).get("id")
        if chat_id is None:
            return
        try:
            await self.client.send_message(text, chat_id=chat_id)
        except Exception as exc:
            self.logger.debug("falha ao responder no Telegram: %s", exc)
