"""Cliente assincrono para o OpenCode Server (HTTP + SSE).

O cliente fala com a API v2 (OpenCode 2.x) e, por compatibilidade, com a API
antiga (1.18.x) quando o servidor nao expoe `/api/location`.

Endpoints v2 usados:
  GET  /api/location
  POST /api/session
  GET  /api/session/{id}
  PATCH /api/session/{id}
  POST /api/session/{id}/prompt
  POST /api/session/{id}/interrupt
  GET  /api/session/{id}/message?limit=N
  GET  /api/session/active
  POST /api/session/{id}/permission/{requestID}/reply
  GET  /api/event

Endpoints v1 equivalentes: /global/health, /path, /session, /session/{id}/message,
/session/{id}/prompt_async, /session/{id}/abort, /session/status, /event.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from zaya.config import Config
from zaya.opencode.events import (
    assistant_text,
    iter_sse_json,
    message_error,
    message_id,
    message_role,
    session_status_is_idle,
)
from zaya.opencode.models import ModelRef, SessionInfo


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

        # None = ainda nao detectado; 2 = API v2; 1 = API antiga; 0 = indisponivel.
        self.api_version: int | None = None
        self._auth_required = False
        self._session_settings: dict[str, tuple[str, str]] = {}

        self._password = self._resolve_password()
        if self._password:
            self.cfg.opencode.server_password = self._password
        self._client = httpx.AsyncClient(
            base_url=self.endpoint,
            auth=self._auth(),
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

    def _auth(self) -> httpx.BasicAuth | None:
        password = (self.cfg.opencode.server_password or "").strip()
        if not password:
            return None
        return httpx.BasicAuth("opencode", password)

    def _configure_auth(self) -> None:
        auth = self._auth()
        if auth is not None:
            self._client.auth = auth

    def _resolve_password(self) -> str:
        """Senha configurada, do nosso servidor ou do servico local do OpenCode."""
        configured = (self.cfg.opencode.server_password or "").strip()
        if configured:
            return configured

        candidates = [
            self.cfg.servers_dir() / f"{self.port}.json",
            Path.home() / ".local" / "state" / "opencode" / "service.json",
        ]
        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if path.name == "service.json":
                url = str(data.get("url") or "")
                if url and urlsplit(url).port != self.port:
                    continue
            password = str(data.get("password") or "").strip()
            if password:
                return password
        return ""

    # ---------------------------------------------------------- processo local
    def _server_state_path(self) -> Path:
        return self.cfg.servers_dir() / f"{self.port}.json"

    def _read_server_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._server_state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 1:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _process_command(pid: int) -> str:
        try:
            proc = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return proc.stdout.strip()

    def _startup_lock(self) -> int | None:
        """Lock exclusivo por porta durante a inicializacao do servidor.

        Evita que duas Zayas subindo juntas encerrem o servidor uma da outra.
        O lock cobre apenas o trecho de spawn; o servidor continua vivo depois
        que o processo da Zaya solta o lock.
        """
        path = self.cfg.servers_dir() / f"{self.port}.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return fd

    @staticmethod
    def _process_cwd(pid: int) -> str:
        """Diretorio de trabalho do processo (vazio se nao der para descobrir)."""
        try:
            proc = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        for line in proc.stdout.splitlines():
            if line.startswith("n"):
                return line[1:].strip()
        return ""

    @staticmethod
    def _listener_pids(port: int) -> list[int]:
        try:
            proc = subprocess.run(
                ["lsof", "-tnP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return [int(line) for line in proc.stdout.split() if line.isdigit()]

    def _port_in_use(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=1.0):
                return True
        except OSError:
            return False

    @classmethod
    def _is_managed_server(cls, pid: int) -> bool:
        command = cls._process_command(pid)
        return "opencode" in command and "serve" in command

    @staticmethod
    def _terminate(pid: int) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not OpenCodeClient._pid_alive(pid):
                    return
                time.sleep(0.1)

    def _clear_server_state(self, pid: int | None = None) -> None:
        if pid is not None and self._read_server_state().get("pid") != pid:
            return
        try:
            self._server_state_path().unlink()
        except OSError:
            pass

    def _write_server_state(self, state: dict[str, Any]) -> None:
        path = self._server_state_path()
        tmp = path.with_suffix(".json.tmp")
        # O arquivo guarda a senha do servidor: nasce 0600 e substitui o anterior
        # de forma atomica, sem janela com permissao mais aberta.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(state, indent=2))
        finally:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)

    def _reclaim_port(self) -> None:
        """Encerra um servidor OpenCode orfao que ficou segurando a porta.

        A porta e derivada do diretorio do projeto e o servidor gerenciado e
        mantido vivo entre execucoes; se ele deixa de responder (senha antiga,
        processo travado), a proxima Zaya precisa liberar a porta antes de subir
        outro. Roda sob o lock de inicializacao: aqui um listener nao registrado
        e resto de uma instancia que morreu no meio do spawn. Processos que nao
        sejam `opencode serve` deste projeto nunca sao encerrados.
        """
        state = self._read_server_state()
        registered = (
            state.get("pid") if state.get("directory") in (None, str(self.directory)) else None
        )
        pids = self._listener_pids(self.port)
        if not pids and isinstance(registered, int) and self._pid_alive(registered):
            pids = [registered]

        killed: list[int] = []
        for pid in dict.fromkeys(pids):
            if not isinstance(pid, int) or not self._pid_alive(pid):
                continue
            if not self._is_managed_server(pid):
                continue
            # Sem registro nosso, so encerra se o processo roda no diretorio
            # deste projeto: um `opencode serve` alheio nao pode ser morto.
            if pid != registered and self._process_cwd(pid) != str(self.directory):
                continue
            self._terminate(pid)
            killed.append(pid)

        if self._port_in_use():
            owner = ", ".join(str(pid) for pid in pids) or "desconhecido"
            raise OpenCodeError(
                f"A porta {self.port} esta ocupada por um processo que a Zaya nao "
                f"gerencia (pid {owner}). Encerre-o ou ajuste ZAYA_OPENCODE_BASE_URL."
            )

        if killed:
            self._clear_server_state()

    @property
    def model(self) -> ModelRef:
        return ModelRef.parse(self.cfg.opencode.model)

    def _path(self, path: str) -> str:
        if self.api_version == 2:
            return f"/api{path}"
        return path

    @staticmethod
    def _is_json(resp: httpx.Response) -> bool:
        content_type = resp.headers.get("content-type", "")
        return "application/json" in content_type.lower()

    @staticmethod
    def _is_error_payload(data: Any) -> bool:
        """A API v2 devolve erros como `{"_tag": "UnauthorizedError", ...}`."""
        if not isinstance(data, dict):
            return False
        tag = data.get("_tag")
        return isinstance(tag, str) and "error" in tag.lower()

    async def _detect_version(self) -> int:
        """Descobre se o servidor fala a API v2 (preferida) ou a antiga."""
        if self.api_version is not None:
            return self.api_version

        self._auth_required = False
        try:
            resp = await self._client.get(
                "/api/location",
                params={"location[directory]": str(self.directory)},
                timeout=5.0,
            )
            if resp.status_code == 200 and self._is_json(resp):
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    data = None
                self.api_version = 2
                if self._is_error_payload(data):
                    # Servidor v2 protegido, mas sem a senha correta.
                    self._auth_required = True
                return 2
            if resp.status_code == 401:
                # O servidor e v2, mas a senha esta faltando/errada.
                self.api_version = 2
                self._auth_required = True
                return 2
        except httpx.HTTPError:
            pass

        try:
            resp = await self._client.get("/global/health", timeout=5.0)
            if resp.status_code == 200 and self._is_json(resp):
                self.api_version = 1
                return 1
        except httpx.HTTPError:
            pass

        # Nao memoriza a falha: o servidor pode ainda estar iniciando. A proxima
        # chamada tenta de novo em vez de ficar presa na API v1/indisponivel.
        self.api_version = None
        return 0

    # ------------------------------------------------------------------ health
    async def health(self) -> dict[str, Any] | None:
        await self._detect_version()
        path = "/api/location" if self.api_version == 2 else "/global/health"
        params = {"location[directory]": str(self.directory)} if self.api_version == 2 else None
        try:
            resp = await self._client.get(path, params=params, timeout=5.0)
        except httpx.HTTPError:
            return None
        if resp.status_code == 401:
            self._auth_required = True
            return None
        if resp.status_code != 200 or not self._is_json(resp):
            return None
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return None
        if self._is_error_payload(data):
            # HTTP 200 com corpo de erro: nao conta como servidor saudavel.
            self._auth_required = True
            return None
        return data if isinstance(data, dict) else None

    async def path_info(self) -> dict[str, Any] | None:
        if self.api_version is None:
            await self._detect_version()
        if self.api_version == 2:
            return await self.health()
        try:
            resp = await self._client.get("/path", timeout=5.0)
            if resp.status_code == 200 and self._is_json(resp):
                return resp.json()
        except httpx.HTTPError:
            return None
        return None

    async def ensure_server(self) -> None:
        health = await self.health()
        if health:
            if self.auto_endpoint and self.api_version == 1:
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
                            f"({server_dir}). Mude ZAYA_OPENCODE_BASE_URL ou encerre-o."
                        )
            return

        unavailable = f"Servidor OpenCode indisponivel em {self.endpoint}"
        auth_error = (
            f"O servidor OpenCode em {self.endpoint} exige autenticacao. "
            "Defina ZAYA_OPENCODE_PASSWORD com a senha do servidor."
        )

        if not self.auto_endpoint:
            raise OpenCodeError(
                auth_error
                if self._auth_required
                else f"{unavailable}. Inicie 'opencode serve' ou ajuste ZAYA_OPENCODE_BASE_URL."
            )
        if not self.cfg.opencode.auto_start or not self.cfg.opencode.managed:
            raise OpenCodeError(
                auth_error if self._auth_required else f"{unavailable} e auto_start desativado."
            )

        if self._auth_required:
            # Respondeu 401: e um servidor antigo nosso com outra senha. O spawn
            # recupera a porta (sob o lock de inicializacao) em vez de abrir mao
            # do servidor gerenciado.
            self.api_version = None
            self._auth_required = False

        await self._spawn_server()

    async def _spawn_server(self) -> None:
        binary = shutil.which("opencode")
        if not binary:
            raise OpenCodeError("Binario 'opencode' nao encontrado no PATH.")

        self.cfg.ensure_dirs()
        log_path = self.cfg.log_dir() / f"opencode-{self.port}.log"

        # Serializa a inicializacao entre instancias da Zaya: duas subindo juntas
        # nao podem encerrar o servidor uma da outra.
        lock_fd = self._startup_lock()
        if lock_fd is None:
            raise OpenCodeError(
                f"Outra Zaya esta iniciando o servidor OpenCode deste projeto "
                f"(porta {self.port}). Tente novamente em instantes."
            )
        try:
            await self._start_server_locked(binary, log_path)
        finally:
            try:
                os.close(lock_fd)
            except OSError:
                pass

    async def _start_server_locked(self, binary: str, log_path: Path) -> None:
        if self._port_in_use():
            self._reclaim_port()

        if not self.cfg.opencode.server_password:
            self.cfg.opencode.server_password = secrets.token_urlsafe(24)
            self._password = self.cfg.opencode.server_password
            self._configure_auth()

        env = os.environ.copy()
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

        state: dict[str, Any] = {
            "pid": self._spawned.pid,
            "port": self.port,
            "host": self.host,
            "directory": str(self.directory),
            "log": str(log_path),
            "started_at": time.time(),
            # Sempre persistida: sem ela a proxima Zaya nao consegue reautenticar
            # no servidor mantido vivo entre execucoes.
            "password": self.cfg.opencode.server_password,
        }

        self.api_version = None
        self._auth_required = False
        deadline = time.monotonic() + self.cfg.opencode.startup_timeout
        try:
            while time.monotonic() < deadline:
                if self._spawned.poll() is not None:
                    raise OpenCodeError(
                        f"OpenCode encerrou ao iniciar (codigo {self._spawned.returncode}). "
                        f"Veja {log_path}.\n{_tail(log_path)}"
                    )
                if self._auth_required:
                    raise OpenCodeError(
                        f"O servidor OpenCode em {self.endpoint} exige autenticacao. "
                        "Defina ZAYA_OPENCODE_PASSWORD com a senha do servidor."
                    )
                if await self.health():
                    # So registra o pid depois de confirmar que o servidor responde;
                    # assim um spawn que falhou nao deixa um pid morto no estado.
                    self._write_server_state(state)
                    return
                await asyncio.sleep(0.25)

            raise OpenCodeError(
                f"OpenCode nao respondeu em {self.cfg.opencode.startup_timeout:.0f}s. "
                f"Veja {log_path}.\n{_tail(log_path)}"
            )
        except OpenCodeError:
            self._abort_spawn()
            raise

    def _abort_spawn(self) -> None:
        proc = self._spawned
        if proc is not None and proc.poll() is None:
            self._terminate(proc.pid)
        if proc is not None:
            self._clear_server_state(proc.pid)
        self._spawned = None

    # ----------------------------------------------------------------- requests
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self.api_version is None:
            await self._detect_version()
        actual = self._path(path)
        try:
            resp = await self._client.request(method, actual, **kwargs)
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
        await self._detect_version()
        selected = model or self.model
        agent = self.cfg.opencode.agent
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        if selected:
            body["model"] = selected.create_payload()
        if agent:
            body["agent"] = agent
        if self.api_version == 2:
            body["location"] = {"directory": str(self.directory)}
        data = await self._request("POST", "/session", json=body)
        if self.api_version == 2 and isinstance(data, dict):
            data = data.get("data", data)
        session = SessionInfo.from_api(data or {})
        if session.id:
            self._session_settings[session.id] = (
                selected.slug if selected else "",
                agent or "",
            )
        return session

    async def delete_session(self, session_id: str) -> None:
        """Remove uma sessao efemera (ex.: resumo diario de memoria)."""
        try:
            await self._request("DELETE", f"/session/{session_id}")
        except OpenCodeError:
            pass

    async def get_session(self, session_id: str) -> SessionInfo | None:
        try:
            data = await self._request("GET", f"/session/{session_id}")
        except OpenCodeError as exc:
            if exc.status_code in {400, 404}:
                return None
            raise
        if self.api_version == 2 and isinstance(data, dict):
            data = data.get("data", data)
        return SessionInfo.from_api(data or {})

    async def set_title(self, session_id: str, title: str) -> None:
        await self._request("PATCH", f"/session/{session_id}", json={"title": title})

    async def list_sessions(self) -> list[SessionInfo]:
        data = await self._request("GET", "/session")
        if self.api_version == 2 and isinstance(data, dict):
            data = data.get("data", [])
        return [SessionInfo.from_api(item) for item in data or []]

    async def session_status(self, session_id: str) -> Any:
        if self.api_version is None:
            await self._detect_version()
        if self.api_version == 2:
            try:
                data = await self._request("GET", "/session/active") or {}
                active = data.get("data") if isinstance(data, dict) else {}
                if isinstance(active, dict) and session_id in active:
                    return {"type": "busy"}
                return {"type": "idle"}
            except OpenCodeError:
                info = await self.get_session(session_id)
                time_info = (info.raw.get("time") if info else None) or {}
                busy = time_info.get("busy")
                idle = time_info.get("idle")
                if busy and (not idle or busy > idle):
                    return {"type": "busy"}
                return {"type": "idle"}

        data = await self._request("GET", "/session/status") or {}
        return data.get(session_id)

    async def is_idle(self, session_id: str) -> bool:
        return session_status_is_idle(await self.session_status(session_id))

    async def abort(self, session_id: str) -> None:
        path = (
            f"/session/{session_id}/interrupt"
            if self.api_version == 2
            else f"/session/{session_id}/abort"
        )
        try:
            await self._request("POST", path)
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

    async def _ensure_session_settings(
        self,
        session_id: str,
        model: ModelRef | None,
        agent: str | None,
    ) -> None:
        """Na v2 o modelo/agente ficam presos a sessao; atualiza quando mudam."""
        if self.api_version != 2:
            return
        selected = model or self.model
        selected_agent = agent or self.cfg.opencode.agent
        key = (selected.slug if selected else "", selected_agent or "")
        if self._session_settings.get(session_id) == key:
            return
        if selected:
            await self._request(
                "POST", f"/session/{session_id}/model", json={"model": selected.create_payload()}
            )
        if selected_agent:
            await self._request(
                "POST", f"/session/{session_id}/agent", json={"agent": selected_agent}
            )
        self._session_settings[session_id] = key

    async def prompt_sync(
        self,
        session_id: str,
        text: str,
        model: ModelRef | None = None,
        agent: str | None = None,
        system: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        if self.api_version is None:
            await self._detect_version()
        if self.api_version == 2:
            before = await self.latest_assistant(session_id)
            before_id = message_id_of(before)
            await self.prompt_async(
                session_id,
                text,
                model=model,
                agent=agent,
                system=system,
                message_id=message_id,
            )
            deadline = time.monotonic() + self.cfg.opencode.request_timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(0.4)
                message = await self.latest_assistant(session_id)
                if not message or message_id_of(message) == before_id:
                    continue
                error = message_error(message)
                if error:
                    raise OpenCodeError(f"O agente retornou erro: {error}")
                if session_status_is_idle(await self.session_status(session_id)):
                    return message
            raise OpenCodeError("tempo limite aguardando resposta do OpenCode")

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
        if self.api_version is None:
            await self._detect_version()
        if self.api_version == 2:
            await self._ensure_session_settings(session_id, model, agent)
            if system:
                text = f"{system}\n\n---\n\n{text}"
            body: dict[str, Any] = {"text": text, "delivery": "queue"}
            if message_id:
                body["id"] = message_id
            await self._request("POST", f"/session/{session_id}/prompt", json=body)
            return

        body = self._prompt_body(text, model, agent, system, message_id)
        await self._request("POST", f"/session/{session_id}/prompt_async", json=body)

    async def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/session/{session_id}/message", params={"limit": limit})
        if self.api_version == 2 and isinstance(data, dict):
            data = data.get("data", [])
        return data or []

    async def latest_assistant(self, session_id: str, limit: int = 20) -> dict[str, Any] | None:
        items = await self.messages(session_id, limit=limit)
        if self.api_version == 2:
            ordered = sorted(items, key=_message_created, reverse=True)
            for item in ordered:
                if message_role(item) == "assistant":
                    return item
            return None
        for item in reversed(items):
            if message_role(item) == "assistant":
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
        self, session_id: str, permission_id: str, response: str, *, v2: bool | None = None
    ) -> None:
        if response not in {"once", "always", "reject"}:
            raise ValueError(f"resposta de permissao invalida: {response}")

        # v2: POST /api/session/{id}/permission/{requestID}/reply {"decision": ...}
        # v1: POST /session/{id}/permissions/{permissionID}       {"response": ...}
        v2_path = (
            f"/session/{session_id}/permission/{permission_id}/reply",
            {"decision": response},
        )
        v1_path = (
            f"/session/{session_id}/permissions/{permission_id}",
            {"response": response},
        )
        if v2 is None:
            use_v2 = self.api_version == 2
        else:
            use_v2 = v2
        attempts = [v2_path, v1_path] if use_v2 else [v1_path, v2_path]

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
        if self.api_version is None:
            await self._detect_version()
        if self.api_version == 2:
            data = (
                await self._request(
                    "GET",
                    "/model",
                    params={"location[directory]": str(self.directory)},
                    timeout=timeout,
                )
                or {}
            )
            grouped: dict[str, dict[str, Any]] = {}
            for item in data.get("data", []) if isinstance(data, dict) else []:
                provider_id = str(item.get("providerID") or "")
                model_key = str(item.get("modelID") or item.get("id") or "")
                if not provider_id or not model_key:
                    continue
                provider = grouped.setdefault(provider_id, {"id": provider_id, "models": {}})
                provider["models"][model_key] = item
            return {"all": list(grouped.values()), "connected": list(grouped)}

        return await self._request("GET", "/provider", timeout=timeout) or {}

    # ------------------------------------------------------------------ events
    async def stream_events(self, stop: asyncio.Event | None = None):
        """Async generator de eventos SSE, com reconexao automatica."""
        if self.api_version is None:
            await self._detect_version()
        path = self._path("/event")
        delay = 0.5
        while not (stop and stop.is_set()):
            try:
                async with self._client.stream("GET", path, timeout=httpx.Timeout(None)) as resp:
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


def message_id_of(message: dict[str, Any] | None) -> str | None:
    return message_id(message) if message else None


def _message_created(message: dict[str, Any]) -> float:
    time_info = message.get("time")
    if isinstance(time_info, dict):
        created = time_info.get("created")
        if isinstance(created, (int, float)):
            return float(created)
    info = message.get("info") or {}
    time_info = info.get("time") if isinstance(info, dict) else None
    if isinstance(time_info, dict):
        created = time_info.get("created")
        if isinstance(created, (int, float)):
            return float(created)
    return 0.0


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
    return found
