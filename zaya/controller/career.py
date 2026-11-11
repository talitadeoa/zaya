"""Rotina semanal de vagas no Telegram, sem LLM.

Segue o mesmo padrao do ``BriefingMixin``: checa o horario num worker proprio,
reserva o dia com ``claim_meta`` para TUI e servico do Telegram nao enviarem
duas vezes, e busca as fontes so quando o dia ainda nao foi entregue.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

from zaya.career.service import (
    career_due,
    collect,
    compose_message,
    load_state,
    mark_delivered,
    pending_jobs,
    record_results,
    save_state,
    state_path,
)
from zaya.controller.contract import OrchestratorContract


class CareerMixin(OrchestratorContract):
    def _career_state_path(self):
        return state_path(
            self.cfg.resolved_data_dir(),
            self.cfg.career.state_file,
        )

    def _start_career_worker(self) -> None:
        cfg = self.cfg.career
        if not cfg.enabled or not self.telegram.enabled:
            return
        if self._career_task is not None and not self._career_task.done():
            return
        self._career_task = asyncio.create_task(self._career_loop())

    async def _career_loop(self) -> None:
        cfg = self.cfg.career
        await asyncio.sleep(3.0)
        while True:
            await asyncio.sleep(max(5.0, cfg.check_interval_seconds))
            if not cfg.enabled:
                continue
            now = datetime.now().astimezone()
            if not career_due(
                now.strftime("%H:%M"), cfg.at, cfg.until, now.date().weekday(), cfg.days
            ):
                continue
            try:
                await self._send_career(now.date())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - depende da rede
                self.logger.warning("falha na rotina de vagas: %s", exc, exc_info=True)

    async def _send_career(self, day: date) -> bool:
        """Busca, registra e entrega o que estiver pendente; devolve se enviou."""
        cfg = self.cfg.career
        key = f"career:{day.isoformat()}"
        if self.store.get_meta(key):
            return False
        if time.monotonic() - self._career_last_attempt < cfg.retry_minutes * 60:
            return False
        # Reserva antes de buscar: a rodada custa rede e nao pode rodar duas vezes.
        if not self.store.claim_meta(key, "sending"):
            return False
        self._career_last_attempt = time.monotonic()

        path = self._career_state_path()
        try:
            state = load_state(path)
        except ValueError as exc:
            self.logger.warning("rotina de vagas: %s", exc)
            self.store.delete_meta(key)
            return False

        result = await collect(state)
        # Grava antes de enviar: se o Telegram falhar, as vagas ficam pendentes
        # e a proxima rodada entrega em vez de perde-las.
        try:
            record_results(state, result)
            save_state(path, state)
        except OSError as exc:
            self.logger.warning("rotina de vagas: falha ao gravar o estado: %s", exc)
            self.store.delete_meta(key)
            return False

        pendentes = pending_jobs(state)
        if not pendentes and not result.bruto:
            # Nada a entregar e nenhuma fonte respondeu: tenta de novo depois.
            self.store.delete_meta(key)
            return False

        text = compose_message(state, result=result, day=day, limit=cfg.max_items)
        ensinar = [str(item.get("id")) for item in pendentes] if pendentes else []
        try:
            await self.telegram.send_message(text)
        except Exception as exc:
            self.logger.warning("rotina de vagas: falha ao enviar no Telegram: %s", exc)
            self.store.delete_meta(key)
            return False

        if ensinar:
            mark_delivered(state, ensinar)
            try:
                save_state(path, state)
            except OSError as exc:  # pragma: no cover - disco cheio
                self.logger.warning("rotina de vagas: entrega nao registrada: %s", exc)

        self.store.set_meta(key, datetime.now().astimezone().isoformat(timespec="seconds"))
        self.logger.info(
            "vagas de %s enviadas: %d entregues de %d lidas",
            day.isoformat(),
            len(ensinar),
            result.bruto,
        )
        return True
