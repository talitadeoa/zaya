"""Webhook do WhatsApp Cloud API: verificacao da Meta e parsing de mensagens."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class IncomingMessage:
    sender: str
    message_id: str
    kind: str
    text: str = ""
    media_id: str = ""
    mime_type: str = ""
    timestamp: str = ""


def parse_webhook(payload: dict[str, Any]) -> list[IncomingMessage]:
    messages: list[IncomingMessage] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for item in value.get("messages") or []:
                sender = str(item.get("from") or "")
                message_id = str(item.get("id") or "")
                timestamp = str(item.get("timestamp") or "")
                kind = str(item.get("type") or "other")
                if kind == "text":
                    text = str((item.get("text") or {}).get("body") or "")
                    messages.append(
                        IncomingMessage(sender, message_id, kind, text=text, timestamp=timestamp)
                    )
                elif kind == "audio":
                    audio = item.get("audio") or {}
                    messages.append(
                        IncomingMessage(
                            sender,
                            message_id,
                            kind,
                            media_id=str(audio.get("id") or ""),
                            mime_type=str(audio.get("mime_type") or ""),
                            timestamp=timestamp,
                        )
                    )
                else:
                    messages.append(
                        IncomingMessage(sender, message_id, kind, timestamp=timestamp)
                    )
    return messages


class _WebhookHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    verify_token = ""
    on_message: Callable[[IncomingMessage], None] | None = None

    def dispatch(self, message: IncomingMessage) -> None:
        if self.on_message is None:
            return
        try:
            self.on_message(message)
        except Exception:
            # um erro do handler nunca deve derrubar o webhook
            pass


class _Handler(BaseHTTPRequestHandler):
    server: _WebhookHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/webhook":
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        mode = (query.get("hub.mode") or [""])[0]
        token = (query.get("hub.verify_token") or [""])[0]
        challenge = (query.get("hub.challenge") or [""])[0]
        if mode == "subscribe" and token == self.server.verify_token:
            self._respond(200, challenge)
        else:
            self.send_error(403)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/webhook":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        self._respond(200, "ok")
        for message in parse_webhook(payload if isinstance(payload, dict) else {}):
            self.server.dispatch(message)

    def _respond(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class WebhookServer:
    def __init__(
        self,
        verify_token: str,
        on_message: Callable[[IncomingMessage], None] | None = None,
        host: str = "127.0.0.1",
        port: int = 8765,
    ):
        self._httpd = _WebhookHTTPServer((host, port), _Handler)
        self._httpd.verify_token = verify_token
        self._httpd.on_message = on_message

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        self._httpd.shutdown()

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)
