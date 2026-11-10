"""Briefings diarios: checa a Agenda e envia no Telegram, sem LLM."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

from zora.agenda.client import AgendaError
from zora.briefing.compose import (
    collect_habits,
    collect_tasks,
    due_slot,
    habits_from_routines,
    morning_message,
    noon_message,
)
from zora.controller.contract import OrchestratorContract


class BriefingMixin(OrchestratorContract):
    def _start_briefing_worker(self) -> None:
        cfg = self.cfg.briefing
        if not cfg.enabled or not self.telegram.enabled:
            return
        if self._briefing_task is not None and not self._briefing_task.done():
            return
        self._briefing_task = asyncio.create_task(self._briefing_loop())

    async def _briefing_loop(self) -> None:
        cfg = self.cfg.briefing
        await asyncio.sleep(2.0)
        while True:
            await asyncio.sleep(max(5.0, cfg.check_interval_seconds))
            if not cfg.enabled:
                continue
            if self._busy or self._recording or self._listening:
                continue
            now = datetime.now().astimezone()
            slot = due_slot(
                now.strftime("%H:%M"), cfg.morning_at, cfg.noon_at, cfg.noon_until
            )
            if slot is None:
                continue
            try:
                await self._send_briefing(slot, now.date())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - depende da rede
                self.logger.warning("falha no briefing %s: %s", slot, exc, exc_info=True)

    async def _send_briefing(self, slot: str, day: date) -> bool:
        """Envia uma vez por dia/slot; devolve se enviou agora."""
        cfg = self.cfg.briefing
        key = f"briefing:{day.isoformat()}:{slot}"
        if self.store.get_meta(key):
            return False
        if time.monotonic() - self._briefing_last_attempt < cfg.retry_minutes * 60:
            return False
        # Reserva o slot antes de enviar: TUI e servico do Telegram podem
        # estar abertos ao mesmo tempo e nao devem mandar duas mensagens.
        if not self.store.claim_meta(key, "sending"):
            return False
        self._briefing_last_attempt = time.monotonic()

        try:
            text = await self._compose_briefing(slot, day)
        except AgendaError as exc:
            self.logger.warning("briefing: Agenda indisponível: %s", exc)
            self.store.delete_meta(key)
            return False
        if not text:
            self.store.delete_meta(key)
            return False
        try:
            await self.telegram.send_message(text)
        except Exception as exc:
            self.logger.warning("briefing: falha ao enviar no Telegram: %s", exc)
            self.store.delete_meta(key)
            return False

        self.store.set_meta(
            key, datetime.now().astimezone().isoformat(timespec="seconds")
        )
        self.logger.info("briefing %s de %s enviado", slot, day.isoformat())
        return True

    async def _compose_briefing(self, slot: str, day: date) -> str:
        cfg = self.cfg.briefing
        if not self.agenda.enabled:
            self.logger.info("briefing ignorado: Agenda desabilitada")
            return ""

        notes = await self.agenda.notes_on(day.isoformat())
        tasks = collect_tasks(notes)
        habits, found = collect_habits(notes, cfg.morning_routine)
        if not found:
            try:
                routines = await self.agenda.routines()
            except AgendaError as exc:
                self.logger.warning("briefing: rotinas indisponíveis: %s", exc)
                routines = []
            habits, found = habits_from_routines(
                routines, day, cfg.morning_routine
            )

        name = self._user_name()
        if slot == "morning":
            return morning_message(
                name, day, tasks, habits, found, max_items=cfg.max_tasks
            )
        return noon_message(name, day, tasks, habits, found, max_items=cfg.max_tasks)
