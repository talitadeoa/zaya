"""Cliente mínimo da API de bots do Telegram (texto, áudio e long polling)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from zora.config import TelegramConfig

_MAX_TEXT = 4000


class TelegramError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class TelegramClient:
    def __init__(self, cfg: TelegramConfig, http: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self._http = http
        self._owns_http = http is None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.bot_token)

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.cfg.poll_timeout + 10)
        return self._http

    def _url(self, method: str) -> str:
        base = self.cfg.api_base_url.rstrip("/")
        return f"{base}/bot{self.cfg.bot_token}/{method}"

    def _file_url(self, file_path: str) -> str:
        base = self.cfg.api_base_url.rstrip("/")
        return f"{base}/file/bot{self.cfg.bot_token}/{file_path}"

    def _resolve_chat(self, chat_id: str | int | None) -> str:
        value = str(chat_id if chat_id is not None else self.cfg.allowed_chat_id).strip()
        if not value:
            raise TelegramError("chat nao configurado (telegram.allowed_chat_id)")
        return value

    async def get_me(self) -> dict[str, Any]:
        return await self._get("getMe")

    async def send_message(
        self, text: str, chat_id: str | int | None = None
    ) -> dict[str, Any]:
        target = self._resolve_chat(chat_id)
        result: dict[str, Any] = {}
        for chunk in _split_text(text):
            result = await self._post(
                "sendMessage",
                json={"chat_id": target, "text": chunk, "disable_web_page_preview": True},
            )
        return result

    async def send_audio(
        self,
        path: str | Path,
        chat_id: str | int | None = None,
        caption: str = "",
        as_voice: bool = False,
    ) -> dict[str, Any]:
        audio = Path(path)
        if not audio.is_file():
            raise TelegramError(f"arquivo de audio nao encontrado: {audio}")
        target = self._resolve_chat(chat_id)
        field = "voice" if as_voice else "audio"
        mime = "audio/mpeg"
        cleanup: Path | None = None
        if as_voice:
            audio = await asyncio.to_thread(_to_ogg_opus, audio)
            cleanup = audio
            mime = "audio/ogg"
        try:
            with audio.open("rb") as fh:
                files = {field: (audio.name, fh, mime)}
                data = {"chat_id": target, "caption": caption}
                return await self._post_multipart(
                    "sendVoice" if as_voice else "sendAudio", data, files
                )
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)

    async def send_chat_action(
        self, chat_id: str | int | None = None, action: str = "typing"
    ) -> Any:
        target = self._resolve_chat(chat_id)
        return await self._post("sendChatAction", json={"chat_id": target, "action": action})

    async def get_updates(
        self, offset: int | None = None, timeout: int | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": self.cfg.poll_timeout if timeout is None else timeout,
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            params["offset"] = offset
        result = await self._get("getUpdates", params)
        return result if isinstance(result, list) else []

    async def get_file(self, file_id: str) -> str:
        result = await self._get("getFile", {"file_id": file_id})
        path = result.get("file_path") if isinstance(result, dict) else None
        if not path:
            raise TelegramError("o Telegram nao retornou o caminho do arquivo")
        return str(path)

    async def download_file(self, file_path: str) -> bytes:
        try:
            response = await self._http_client().get(self._file_url(file_path))
        except httpx.HTTPError as exc:
            raise TelegramError(f"falha ao baixar arquivo: {exc}") from exc
        if not response.is_success:
            raise TelegramError(
                f"erro ao baixar arquivo ({response.status_code})",
                response.status_code,
                response.text[:300],
            )
        return response.content

    async def _get(self, method: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._http_client().get(self._url(method), params=params)
        except httpx.HTTPError as exc:
            raise TelegramError(f"falha de rede: {exc}") from exc
        return self._payload(response)

    async def _post(self, method: str, json: dict[str, Any]) -> Any:
        try:
            response = await self._http_client().post(self._url(method), json=json)
        except httpx.HTTPError as exc:
            raise TelegramError(f"falha de rede: {exc}") from exc
        return self._payload(response)

    async def _post_multipart(
        self, method: str, data: dict[str, Any], files: dict[str, Any]
    ) -> Any:
        try:
            response = await self._http_client().post(self._url(method), data=data, files=files)
        except httpx.HTTPError as exc:
            raise TelegramError(f"falha de rede: {exc}") from exc
        return self._payload(response)

    @staticmethod
    def _payload(response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not response.is_success or not payload.get("ok", False):
            description = payload.get("description") or response.text[:300]
            raise TelegramError(
                f"erro do Telegram: {description}", response.status_code, response.text[:300]
            )
        return payload.get("result")

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None


def _to_ogg_opus(source: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TelegramError(
            "ffmpeg nao encontrado; instale com 'brew install ffmpeg' para enviar voz"
        )
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as fh:
        target = Path(fh.name)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                "-vbr",
                "on",
                str(target),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise TelegramError(f"falha ao converter audio para ogg/opus: {exc}") from exc
    return target


def _split_text(text: str, limit: int = _MAX_TEXT) -> list[str]:
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = max(remaining.rfind("\n", 0, limit), remaining.rfind(" ", 0, limit))
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks
