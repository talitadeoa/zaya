"""Cliente do WhatsApp Cloud API (Meta) para texto e audio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from zora.config import WhatsAppConfig


class WhatsAppError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class WhatsAppClient:
    def __init__(self, cfg: WhatsAppConfig, http: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self._http = http
        self._owns_http = http is None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.phone_number_id and self.cfg.access_token)

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    def _url(self, suffix: str) -> str:
        base = self.cfg.graph_base_url.rstrip("/")
        return f"{base}/{self.cfg.api_version}/{self.cfg.phone_number_id}/{suffix}"

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.access_token}"}

    def _resolve_to(self, to: str | None) -> str:
        recipient = (to or self.cfg.recipient).strip()
        if not recipient:
            raise WhatsAppError("destinatario nao configurado (whatsapp.recipient)")
        return recipient

    async def send_text(self, text: str, to: str | None = None) -> dict[str, Any]:
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._resolve_to(to),
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        return await self._post("messages", json=body)

    async def send_audio(
        self,
        path: str | Path,
        to: str | None = None,
        mime_type: str = "audio/mpeg",
    ) -> dict[str, Any]:
        audio = Path(path)
        if not audio.is_file():
            raise WhatsAppError(f"arquivo de audio nao encontrado: {audio}")
        media_id = await self._upload_media(audio, mime_type)
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._resolve_to(to),
            "type": "audio",
            "audio": {"id": media_id},
        }
        return await self._post("messages", json=body)

    async def _upload_media(self, path: Path, mime_type: str) -> str:
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, mime_type)}
            data = {"messaging_product": "whatsapp", "type": mime_type}
            try:
                response = await self._http_client().post(
                    self._url("media"),
                    headers=self._auth(),
                    data=data,
                    files=files,
                )
            except httpx.HTTPError as exc:
                raise WhatsAppError(f"falha ao enviar midia: {exc}") from exc
        self._raise_for_status(response)
        payload = _json(response)
        media_id = payload.get("id")
        if not media_id:
            raise WhatsAppError("a Meta nao retornou id da midia")
        return str(media_id)

    async def _post(self, suffix: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http_client().post(
                self._url(suffix), headers=self._auth(), json=json
            )
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"falha de rede: {exc}") from exc
        self._raise_for_status(response)
        return _json(response)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        raise WhatsAppError(
            f"erro da API do WhatsApp ({response.status_code})",
            response.status_code,
            response.text[:500],
        )

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}
