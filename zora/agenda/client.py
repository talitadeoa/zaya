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


def _imul(a: int, b: int) -> int:
    return (a * b) & 0xFFFFFFFF


def _hash32(value: str) -> int:
    result = 0x811C9DC5
    for ch in value:
        result ^= ord(ch)
        result = _imul(result, 0x01000193)
    return result & 0xFFFFFFFF


def lunation_note_id(date: str) -> str:
    """Mesmo id estável usado pela Agenda para a nota de manifestação."""
    seed = f"lunation:{date}"
    value = (
        format(_hash32(seed), "08x")
        + format(_hash32(seed + "#1"), "08x")
        + format(_hash32(seed + "#2"), "08x")
        + format(_hash32(seed + "#3"), "08x")
    )[:32]
    chars = list(value)
    chars[12] = "4"
    chars[16] = "8"
    joined = "".join(chars)
    return (
        f"{joined[0:8]}-{joined[8:12]}-{joined[12:16]}-"
        f"{joined[16:20]}-{joined[20:32]}"
    )


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

    async def _query(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        base = self._get("VITE_SUPABASE_URL", "SUPABASE_URL").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{base}/rest/v1/{table}", headers=self._headers(), params=params
                )
        except httpx.HTTPError as exc:
            raise AgendaError(f"falha ao consultar a Agenda: {exc}") from exc
        if not response.is_success:
            raise AgendaError(f"Agenda respondeu {response.status_code}")
        data = response.json()
        return data if isinstance(data, list) else []

    async def pending_today(self, date: str | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
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
        return await self._query("notes", params)

    async def notes_on(self, date: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        user_id = self._get("AGENDA_USER_ID")
        params = {
            "user_id": f"eq.{user_id}",
            "date": f"eq.{date}",
            "select": "id,text,is_checkbox,completed,date",
            "order": "order.asc",
            "limit": "200",
        }
        return await self._query("notes", params)

    async def lunation_note(self, date: str) -> str:
        rows = await self.notes_on(date)
        wanted = lunation_note_id(date)
        for row in rows:
            if str(row.get("id")) == wanted:
                return str(row.get("text") or "")
        return ""

    async def metas(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        user_id = self._get("AGENDA_USER_ID")
        params = {
            "user_id": f"eq.{user_id}",
            "active": "eq.true",
            "select": "id,text,period,start_date,end_date",
            "order": "order.asc",
            "limit": "100",
        }
        return await self._query("metas", params)

    async def pages_meta(self, limit: int = 30) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        user_id = self._get("AGENDA_USER_ID")
        params = {
            "user_id": f"eq.{user_id}",
            "select": "id,title,updated_at",
            "order": "updated_at.desc",
            "limit": str(limit),
        }
        return await self._query("pages", params)

    async def pages(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        user_id = self._get("AGENDA_USER_ID")
        params = {
            "user_id": f"eq.{user_id}",
            "select": "id,title,content,updated_at",
            "order": "updated_at.desc",
            "limit": str(limit),
        }
        return await self._query("pages", params)
