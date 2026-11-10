"""Iniciativa proativa: saudacoes, lembretes, agenda, tarefas e foco."""

from __future__ import annotations

import asyncio
import contextlib
import random
import re
import time
from datetime import datetime

from zora.agenda.client import AgendaError
from zora.controller.contract import OrchestratorContract
from zora.controller.proactive import ReminderSpec, greeting_for, in_quiet_hours
from zora.focus import RITUALS, principal_phase_today
from zora.opencode.events import assistant_text


class InitiativeMixin(OrchestratorContract):
    async def _check_focus(self, now: datetime) -> None:
        if not self.cfg.focus.enabled:
            return
        phase = principal_phase_today(now)
        if self.cfg.focus.prompt_on_moon and phase is not None:
            key, name, moment = phase
            today = moment.date().isoformat()
            if self.focus.last_moon_prompt() != today:
                self.focus.set_last_moon_prompt(today)
                ritual = RITUALS[key][1]
                manifestation = ""
                try:
                    manifestation = await self.agenda.lunation_note(today)
                except AgendaError as exc:
                    self.logger.warning("lunacao: %s", exc)
                message = f"Hoje é {name}. Ritual do ciclo: {ritual}."
                if manifestation:
                    message += f" Na Agenda, suas manifestações: {manifestation}."
                message += " Quer definir ou ajustar o foco?"
                await self._say_proactive(message)
                return
        if self.focus.is_expired(now):
            await self._say_proactive(
                "Seu foco atual venceu. Quer definir um novo foco para o próximo ciclo?"
            )

    def _mark_activity(self) -> None:
        self._last_activity = time.monotonic()

    def _user_name(self) -> str:
        if self._user_name_cache is None:
            preferences = self.brain.load_preferences()
            match = re.search(r"Nome preferido:\s*([^\n.]+)", preferences, re.IGNORECASE)
            self._user_name_cache = match.group(1).strip() if match else ""
        return self._user_name_cache

    async def _handle_reminder(self, spec: ReminderSpec) -> None:
        reminder_id = self.store.add_reminder(spec.label, spec.due.isoformat(timespec="seconds"))
        when = spec.due.strftime("%d/%m às %H:%M")
        self.ui.success(f"lembrete #{reminder_id} para {when}: {spec.label}")
        await self._speak(f"Combinado. Vou te lembrar de {spec.label} {when}.")

    def _toggle_continuous(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.audio.continuous = arg == "on"
        else:
            self.cfg.audio.continuous = not self.cfg.audio.continuous
        self._voice_mode = self.cfg.audio.continuous
        if self.cfg.audio.continuous:
            self.ui.success("modo contínuo ativado; fale à vontade")
        else:
            self.ui.success("modo contínuo desativado; use Enter para falar")

    def _toggle_proactive(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.proactive.enabled = arg == "on"
        else:
            self.cfg.proactive.enabled = not self.cfg.proactive.enabled
        if self.cfg.proactive.enabled:
            if self._proactive_task is None or self._proactive_task.done():
                self._proactive_task = asyncio.create_task(self._proactive_loop(greet=False))
            self.ui.success("iniciativa proativa ativada")
        else:
            self.ui.success("iniciativa proativa desativada (lembretes já criados ficam salvos)")

    async def _proactive_loop(self, greet: bool = True) -> None:
        cfg = self.cfg.proactive
        if not cfg.enabled:
            return
        await asyncio.sleep(1.0)
        if greet and cfg.startup_greeting:
            await self._startup_greeting()
        while True:
            await asyncio.sleep(max(5.0, cfg.check_interval_seconds))
            if not self.cfg.proactive.enabled:
                continue
            if self._busy or self._recording or self._listening:
                continue
            now = datetime.now().astimezone()
            if in_quiet_hours(now, cfg.quiet_hours):
                continue
            try:
                if cfg.reminders:
                    await self._fire_due_reminders()
                await self._check_agenda(now)
                await self._check_task_page(now)
                await self._check_focus(now)
                if cfg.idle_checkin:
                    idle_for = (time.monotonic() - self._last_activity) / 60.0
                    if idle_for >= cfg.idle_minutes and self._can_idle_checkin(now):
                        await self._say_proactive(await self._generate_initiative_message())
                        self._last_activity = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                self.logger.warning("falha na rotina proativa: %s", exc, exc_info=True)

    async def _check_agenda(self, now: datetime) -> None:
        cfg = self.cfg.agenda
        if not cfg.enabled or not self.agenda.enabled:
            return
        if time.monotonic() < self._agenda_next_check:
            return
        self._agenda_next_check = time.monotonic() + max(5.0, cfg.poll_minutes * 60.0)
        try:
            tasks = await self.agenda.pending_today(now.date().isoformat())
        except AgendaError as exc:
            self.logger.warning("agenda: %s", exc)
            return
        if now.date() != self._agenda_day:
            self._agenda_day = now.date()
            self._agenda_seen = set()
        ids = {str(task.get("id")) for task in tasks if task.get("id")}
        new_ids = ids - self._agenda_seen
        self._agenda_seen = ids
        self._agenda_pending_names = [
            str(task.get("text") or "tarefa") for task in tasks
        ]
        if not new_ids:
            return
        new_tasks = [task for task in tasks if str(task.get("id")) in new_ids]
        names = [str(task.get("text") or "tarefa") for task in new_tasks]
        shown = names[: cfg.max_items]
        if len(names) > len(shown):
            shown.append(f"e mais {len(names) - len(shown)}")
        if ids == new_ids:
            message = (
                f"Na agenda de hoje você tem {len(names)} tarefa(s) pendente(s): "
                + ", ".join(shown)
                + "."
            )
        else:
            message = "Novas tarefas na agenda: " + ", ".join(shown) + "."
        if not cfg.notify_pending:
            self.logger.debug("agenda: %s", message)
            return
        await self._say_proactive(message)

    async def _check_task_page(self, now: datetime) -> None:
        cfg = self.cfg.agenda
        if not cfg.enabled or not self.agenda.enabled or cfg.tasks_poll_seconds <= 0:
            return
        if time.monotonic() < self._tasks_next_check:
            return
        self._tasks_next_check = time.monotonic() + max(5.0, cfg.tasks_poll_seconds)
        try:
            meta = await self.agenda.pages_meta(30)
        except AgendaError as exc:
            self.logger.warning("tarefas: %s", exc)
            return
        meta_page = next(
            (item for item in meta if "tarefa" in str(item.get("title") or "").lower()),
            None,
        )
        if meta_page is None:
            return
        updated_at = str(meta_page.get("updated_at") or "")
        if updated_at and updated_at == self._task_page_updated_at:
            return
        try:
            pages = await self.agenda.pages(30)
        except AgendaError as exc:
            self.logger.warning("tarefas: %s", exc)
            return
        page = next(
            (item for item in pages if "tarefa" in str(item.get("title") or "").lower()),
            None,
        )
        if page is None:
            return
        self._task_page_updated_at = updated_at or str(page.get("updated_at") or "")
        before = {task.id for task in self.tasks.all()}
        synced = self.tasks.sync_text(str(page.get("content") or ""))
        new_tasks = [task for task in synced if task.id not in before]
        for task in new_tasks:
            plan = task.notes or await self._plan_task(task.title)
            self.tasks.set_status(task.id, "in_progress", plan or "")
            self.logger.debug("nova tarefa sincronizada: %s", task.title)

    async def _plan_task(self, title: str) -> str:
        prompt = (
            "Monte um plano curto (até 5 passos) para executar esta tarefa no "
            f"projeto {self.directory}: {title}. Não execute nada e não use "
            "ferramentas; responda apenas o plano, em português."
        )
        system = "Você é a Zaia. Seja objetiva e prática."
        try:
            if not self._task_session_id:
                session = await self.client.create_session(
                    title="Zaia — tarefas", model=self.client.model
                )
                self._task_session_id = session.id
            response = await asyncio.wait_for(
                self.client.prompt_sync(self._task_session_id, prompt, system=system),
                timeout=25.0,
            )
            text = assistant_text(response) if isinstance(response, dict) else ""
            return " ".join(text.split()).strip()
        except Exception as exc:  # pragma: no cover - depende do modelo/rede
            self.logger.warning("plano da tarefa falhou: %s", exc)
            return ""

    async def _fire_due_reminders(self) -> None:
        if not self.cfg.proactive.reminders:
            return
        now = datetime.now().astimezone()
        if in_quiet_hours(now, self.cfg.proactive.quiet_hours):
            return
        for row in self.store.due_reminders(now.isoformat()):
            await self._say_proactive(f"Lembrete: {row['text']}", count=False)
            self.store.mark_reminder_spoken(int(row["id"]))

    def _can_idle_checkin(self, now: datetime) -> bool:
        cfg = self.cfg.proactive
        if self._proactive_day != now.date():
            self._proactive_day = now.date()
            self._proactive_count = 0
        if self._proactive_count >= cfg.daily_max:
            return False
        elapsed = (time.monotonic() - self._last_proactive) / 60.0
        return elapsed >= cfg.min_interval_minutes

    async def _say_proactive(self, text: str, count: bool = True) -> None:
        self.ui.proactive(text)
        if count:
            self._proactive_count += 1
            self._last_proactive = time.monotonic()
        await self._speak(text)

    def _idle_message(self) -> str:
        options = [
            "Ainda estou por aqui. Quer continuar de onde paramos?",
            "Se quiser, posso resumir o que fizemos até agora.",
            "Quer retomar algum projeto ou revisar suas metas?",
            "Estou disponível se quiser pensar em algo junto.",
            "Como está seu ânimo agora?",
            "Quer só conversar um pouco?",
            "Posso te ajudar a destravar alguma coisa?",
            "O que está ocupando sua cabeça agora?",
            "Quer um café e uma pausa?",
            "Tô por aqui, sem pressa.",
            "Como foi seu dia até agora?",
        ]
        choices = [option for option in options if option != self._last_idle_message]
        message = random.choice(choices or options)
        self._last_idle_message = message
        return message

    async def _generate_initiative_message(self, greeting: bool = False) -> str:
        now = datetime.now().astimezone()
        hora = now.strftime("%H:%M")
        pending = self.store.pending_reminders()
        agenda = ", ".join(self._agenda_pending_names[:5]) or "nenhuma"
        metas = ", ".join(goal.label for goal in self.goals.all()) or "nenhuma"
        last = self._last_idle_message or "(nenhuma)"
        if greeting:
            instruction = (
                "É a primeira mensagem do dia. Cumprimente a Talita de forma "
                "carinhosa, acolhedora e espontânea."
            )
        else:
            instruction = (
                "Inicie uma conversa com a Talita de forma carinhosa, acolhedora "
                "e espontânea, como quem está por perto."
            )
        prompt = (
            f"Contexto: agora são {hora} ({greeting_for(now)}). "
            f"Modo ativo: {self.persona.name}. "
            f"Lembretes pendentes: {len(pending)}. "
            f"Tarefas pendentes na agenda: {agenda}. "
            f"Metas: {metas}. "
            f"Última mensagem de iniciativa: {last}.\n\n"
            "Pode mencionar metas ou tarefas quando for genuinamente relevante ou "
            "houver algo novo/urgente. Evite repetir pendências por hábito.\n\n"
            f"{instruction} Responda APENAS com a frase, em português do Brasil, "
            "com no máximo 25 palavras. Não use listas, emojis ou aspas. Não diga "
            "que é uma IA e não repita a última mensagem. Não precisa usar o nome "
            "dela toda vez."
        )
        system = (
            "Você é a Zaia, assistente pessoal da Talita. Seja calorosa, "
            "carinhosa e espontânea, sem exageros."
        )
        try:
            if not self._initiative_session_id:
                session = await self.client.create_session(
                    title="Zaia — iniciativa", model=self.client.model
                )
                self._initiative_session_id = session.id
            response = await asyncio.wait_for(
                self.client.prompt_sync(
                    self._initiative_session_id, prompt, system=system
                ),
                timeout=20.0,
            )
            text = assistant_text(response) if isinstance(response, dict) else ""
            text = " ".join(text.split()).strip().strip('"').strip("'")
            if text:
                self._last_idle_message = text
                return text
        except Exception as exc:  # pragma: no cover - depende do modelo/rede
            self.logger.warning("iniciativa via LLM falhou: %s", exc)
            if self._initiative_session_id:
                with contextlib.suppress(Exception):
                    await self.client.abort(self._initiative_session_id)
        return self._idle_message()

    async def _startup_greeting(self) -> None:
        now = datetime.now().astimezone()
        pending = self.store.pending_reminders()
        if self.cfg.proactive.greeting_via_llm:
            message = await self._generate_initiative_message(greeting=True)
        else:
            message = self._local_greeting(now)
        if pending:
            message += f" Você tem {len(pending)} lembrete(s) pendente(s)."
        if in_quiet_hours(now, self.cfg.proactive.quiet_hours):
            self.ui.proactive(message)
            return
        await self._say_proactive(message, count=False)

    def _local_greeting(self, now: datetime) -> str:
        greet = greeting_for(now)
        nome = self._user_name()
        base = f"{greet}{', ' + nome if nome else ''}!"
        if self._agenda_pending_names:
            shown = ", ".join(self._agenda_pending_names[:3])
            return f"{base} Hoje você tem: {shown}."
        return f"{base} Como posso ajudar?"
