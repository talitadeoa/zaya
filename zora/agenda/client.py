"""Leitura da Agenda (Supabase) para avisos proativos da Zora."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from zora.config import AgendaConfig


class AgendaError(RuntimeError):
    pass


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return env
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _today_local() -> str:
    try:
        return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()
    except Exception:
        return datetime.now().date().isoformat()


class AgendaClient:
    def __init__(self, cfg: AgendaConfig):
        self.cfg = cfg
        self._env = _load_env_file(Path(cfg.env_file).expanduser())

    def _get(self, *names: str) -> str:
        for name in names:
            value = self._env.get(name) or os.environ.get(name)
            if value:
                return value
        return ""

    @property
    def enabled(self) -> bool:
        if not self.cfg.enabled:
            return False
        return bool(
            self._get("VITE_SUPABASE_URL", "SUPABASE_URL")
            and self._get("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY")
            and self._get("AGENDA_USER_ID")
        )

    def _headers(self) -> dict[str, str]:
        key = self._get("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY")
        headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def pending_today(self, date: str | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        base = self._get("VITE_SUPABASE_URL", "SUPABASE_URL").rstrip("/")
        user_id = self._get("AGENDA_USER_ID")
        params = {
            "user_id": f"eq.{user_id}",
            "date": f"eq.{date or _today_local()}",
            "is_checkbox": "eq.true",
            "completed": "eq.false",
            "select": "id,text,parent_id,order,date",
            "order": "order.asc",
            "limit": "200",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{base}/rest/v1/notes", headers=self._headers(), params=params
                )
        except httpx.HTTPError as exc:
            raise AgendaError(f"falha ao consultar a Agenda: {exc}") from exc
        if not response.is_success:
            raise AgendaError(f"Agenda respondeu {response.status_code}")
        data = response.json()
        return data if isinstance(data, list) else []
