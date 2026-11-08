"""Cliente assincrono para o OpenCode Server (API real, HTTP + SSE).

Endpoints usados (OpenCode 1.18.x):
  GET  /global/health
  GET  /path
  POST /session
  GET  /session/{id}
  PATCH /session/{id}
  POST /session/{id}/message
  POST /session/{id}/prompt_async
  POST /session/{id}/abort
  GET  /session/{id}/message?limit=N
  POST /session/{id}/permissions/{permissionID}
  GET  /session/status
  GET  /event
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from zora.config import Config
from zora.opencode.events import (
    assistant_text,
    iter_sse_json,
    message_error,
    session_status_is_idle,
)
from zora.opencode.models import ModelRef, SessionInfo


class OpenCodeError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class OpenCodeClient:
    def __init__(self, cfg: Config, directory: Path | None = None):
        self.cfg = cfg
        self.directory = (directory or cfg.project_directory()).resolve()
        self.auto_endpoint = not bool(cfg.opencode.base_url)
        self.endpoint = self._resolve_endpoint().rstrip("/")
        parts = urlsplit(self.endpoint)
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or (443 if parts.scheme == "https" else 80)

        auth = None
        if cfg.opencode.server_password:
            auth = httpx.BasicAuth("opencode", cfg.opencode.server_password)
        self._client = httpx.AsyncClient(
            base_url=self.endpoint,
            auth=auth,
            timeout=httpx.Timeout(cfg.opencode.request_timeout, connect=10.0),
            headers={"Accept": "application/json"},
        )
        self._spawned: subprocess.Popen | None = None

    # ---------------------------------------------------------------- endpoint
    def _resolve_endpoint(self) -> str:
        if self.cfg.opencode.base_url:
            return self.cfg.opencode.base_url
        digest = hashlib.sha1(str(self.directory).encode("utf-8")).hexdigest()
        port = 4200 + int(digest[:8], 16) % 700
        return f"http://127.0.0.1:{port}"

    @property
    def model(self) -> ModelRef:
        return ModelRef.parse(self.cfg.opencode.model)

    # ------------------------------------------------------------------ health
    async def health(self) -> dict[str, Any] | None:
        try:
            resp = await self._client.get("/global/health", timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
        except httpx.HTTPError:
            return None
        return None

    async def path_info(self) -> dict[str, Any] | None:
        try:
            resp = await self._client.get("/path", timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
        except httpx.HTTPError:
            return None
        return None

    async def ensure_server(self) -> None:
        health = await self.health()
        if health:
            if self.auto_endpoint:
                info = await self.path_info()
                server_dir = info.get("directory") if info else None
                if server_dir:
                    try:
                        same = Path(server_dir).resolve() == self.directory
                    except OSError:
                        same = False
                    if not same:
                        raise OpenCodeError(
                            "Ha um servidor OpenCode nesta porta para outro diretorio "
                            f"({server_dir}). Mude ZORA_OPENCODE_BASE_URL ou encerre-o."
                        )
            return

        if not self.auto_endpoint:
            raise OpenCodeError(
                f"Servidor OpenCode indisponivel em {self.endpoint}. "
                "Inicie 'opencode serve' ou ajuste ZORA_OPENCODE_BASE_URL."
            )
        if not self.cfg.opencode.auto_start or not self.cfg.opencode.managed:
            raise OpenCodeError(
                f"Servidor OpenCode indisponivel em {self.endpoint} e auto_start desativado."
            )
        await self._spawn_server()

    async def _spawn_server(self) -> None:
        binary = shutil.which("opencode")
        if not binary:
            raise OpenCodeError("Binario 'opencode' nao encontrado no PATH.")

        self.cfg.ensure_dirs()
        log_path = self.cfg.log_dir() / f"opencode-{self.port}.log"
        pid_path = self.cfg.servers_dir() / f"{self.port}.json"

        env = os.environ.copy()
        if self.cfg.opencode.server_password:
            env["OPENCODE_SERVER_PASSWORD"] = self.cfg.opencode.server_password
            env.setdefault("OPENCODE_SERVER_USERNAME", "opencode")

        cmd = [
            binary,
            "serve",
            "--hostname",
            self.host,
            "--port",
            str(self.port),
        ]
        log_file = log_path.open("ab")
        try:
            self._spawned = subprocess.Popen(
                cmd,
                cwd=str(self.directory),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        finally:
            log_file.close()

        pid_path.write_text(
            json.dumps(
                {
                    "pid": self._spawned.pid,
                    "port": self.port,
                    "host": self.host,
                    "directory": str(self.directory),
                    "log": str(log_path),
                    "started_at": time.time(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        deadline = time.monotonic() + self.cfg.opencode.startup_timeout
        while time.monotonic() < deadline:
            if self._spawned.poll() is not None:
                raise OpenCodeError(
                    f"OpenCode encerrou ao iniciar (codigo {self._spawned.returncode}). "
                    f"Veja {log_path}.\n{_tail(log_path)}"
                )
            if await self.health():
                return
            await asyncio.sleep(0.25)

        raise OpenCodeError(
            f"OpenCode nao respondeu em {self.cfg.opencode.startup_timeout:.0f}s. "
            f"Veja {log_path}.\n{_tail(log_path)}"
        )

    # ----------------------------------------------------------------- requests
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise OpenCodeError(f"Falha de rede em {method} {path}: {exc}") from exc
        if resp.status_code >= 400:
            raise OpenCodeError(
                f"{method} {path} retornou HTTP {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text[:2000],
            )
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except json.JSONDecodeError:
            return resp.text

    # ---------------------------------------------------------------- sessions
    async def create_session(
        self, title: str | None = None, model: ModelRef | None = None
    ) -> SessionInfo:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        if model:
            body["model"] = model.create_payload()
        if self.cfg.opencode.agent:
            body["agent"] = self.cfg.opencode.agent
        data = await self._request("POST", "/session", json=body)
        return SessionInfo.from_api(data)

    async def get_session(self, session_id: str) -> SessionInfo | None:
        try:
            data = await self._request("GET", f"/session/{session_id}")
        except OpenCodeError as exc:
            if exc.status_code in {400, 404}:
                return None
            raise
        return SessionInfo.from_api(data)

    async def set_title(self, session_id: str, title: str) -> None:
        await self._request("PATCH", f"/session/{session_id}", json={"title": title})

    async def list_sessions(self) -> list[SessionInfo]:
        data = await self._request("GET", "/session")
        return [SessionInfo.from_api(item) for item in data or []]

    async def session_status(self, session_id: str) -> Any:
        data = await self._request("GET", "/session/status") or {}
        return data.get(session_id)

    async def is_idle(self, session_id: str) -> bool:
        return session_status_is_idle(await self.session_status(session_id))

    async def abort(self, session_id: str) -> None:
        try:
            await self._request("POST", f"/session/{session_id}/abort")
        except OpenCodeError:
            pass

    # ---------------------------------------------------------------- messages
    def _prompt_body(
        self,
        text: str,
        model: ModelRef | None,
        agent: str | None,
        system: str | None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": (model or self.model).prompt_payload(),
            "parts": [{"type": "text", "text": text}],
        }
        if message_id:
            body["messageID"] = message_id
        if agent or self.cfg.opencode.agent:
            body["agent"] = agent or self.cfg.opencode.agent
        if system:
            body["system"] = system
        return body

    async def prompt_sync(
        self,
        session_id: str,
        text: str,
        model: ModelRef | None = None,
        agent: str | None = None,
        system: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        body = self._prompt_body(text, model, agent, system, message_id)
        return await self._request("POST", f"/session/{session_id}/message", json=body)

    async def prompt_async(
        self,
        session_id: str,
        text: str,
        model: ModelRef | None = None,
        agent: str | None = None,
        system: str | None = None,
        message_id: str | None = None,
    ) -> None:
        body = self._prompt_body(text, model, agent, system, message_id)
        await self._request("POST", f"/session/{session_id}/prompt_async", json=body)

    async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/session/{session_id}/message", params={"limit": limit})
        return data or []

    async def latest_assistant(self, session_id: str, limit: int = 20) -> dict[str, Any] | None:
        for item in reversed(await self.messages(session_id, limit=limit)):
            info = item.get("info") or {}
            if info.get("role") == "assistant":
                return item
        return None

    async def latest_assistant_text(self, session_id: str) -> str:
        item = await self.latest_assistant(session_id)
        if not item:
            return ""
        err = message_error(item)
        if err:
            raise OpenCodeError(f"O agente retornou erro: {err}")
        return assistant_text(item)

    async def reply_permission(
        self, session_id: str, permission_id: str, response: str, *, v2: bool = False
    ) -> None:
        if response not in {"once", "always", "reject"}:
            raise ValueError(f"resposta de permissao invalida: {response}")

        # OpenCode 1.18 tem dois sistemas de permissao:
        #   v1: POST /session/{id}/permissions/{permissionID}  {"response": ...}
        #   v2: POST /api/session/{id}/permission/{requestID}/reply  {"reply": ...}
        # O evento indica qual usar; se um endpoint sumir/mudar, tentamos o outro.
        v1 = (f"/session/{session_id}/permissions/{permission_id}", {"response": response})
        v2_path = (
            f"/api/session/{session_id}/permission/{permission_id}/reply",
            {"reply": response},
        )
        attempts = [v2_path, v1] if v2 else [v1, v2_path]

        last_error: OpenCodeError | None = None
        for path, body in attempts:
            try:
                await self._request("POST", path, json=body)
                return
            except OpenCodeError as exc:
                if exc.status_code != 404:
                    raise
                last_error = exc
        raise last_error or OpenCodeError("permissao nao encontrada")

    # ---------------------------------------------------------------- providers
    async def providers(self, timeout: float = 8.0) -> dict[str, Any]:
        # /provider pode fazer refresh de rede e travar; nunca bloqueie o CLI.
        return await self._request("GET", "/provider", timeout=timeout) or {}

    # ------------------------------------------------------------------ events
    async def stream_events(self, stop: asyncio.Event | None = None):
        """Async generator de eventos SSE, com reconexao automatica."""
        delay = 0.5
        while not (stop and stop.is_set()):
            try:
                async with self._client.stream(
                    "GET", "/event", timeout=httpx.Timeout(None)
                ) as resp:
                    resp.raise_for_status()
                    delay = 0.5
                    async for event in iter_sse_json(resp.aiter_lines()):
                        if stop and stop.is_set():
                            return
                        yield event
            except (httpx.HTTPError, OSError):
                if stop and stop.is_set():
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._spawned and self._spawned.poll() is None:
            # O servidor e mantido vivo entre execucoes para reaproveitar sessoes.
            pass


def _tail(path: Path, limit: int = 1500) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return data[-limit:]


async def load_provider_models(client: OpenCodeClient) -> set[str]:
    data = await client.providers()
    found: set[str] = set()
    for provider in data.get("all", []) or []:
        provider_id = provider.get("id", "")
        for model_key in provider.get("models") or {}:
            found.add(f"{provider_id}/{model_key}")
    for provider_id in data.get("connected", []) or []:
        for provider in data.get("all", []) or []:
            if provider.get("id") == provider_id:
                for model_key in provider.get("models") or {}:
                    found.add(f"{provider_id}/{model_key}")
    return found
