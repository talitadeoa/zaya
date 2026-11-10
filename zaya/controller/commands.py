"""Comandos de barra (/status, /nota, /foco, /carteira, ...)."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta

from zaya.agenda.client import AgendaError
from zaya.controller.contract import OrchestratorContract
from zaya.controller.modes import PERSONAS, get_persona
from zaya.controller.proactive import parse_reminder
from zaya.finance.quotes import FinanceError, fetch_quote, format_quote
from zaya.focus import moon_details, period_label, principal_phase_today
from zaya.opencode.events import assistant_text
from zaya.tasks.store import Task


def escape_user(text: str) -> str:
    from rich.markup import escape

    return escape(str(text))


class CommandsMixin(OrchestratorContract):
    async def _handle_command(self, text: str) -> bool:
        command, _, argument = text.partition(" ")
        command = command.lower().strip()
        argument = argument.strip()

        if command in {"/sair", "/quit", "/exit"}:
            self.ui.info("até logo.")
            return True
        if command in {"/reiniciar", "/restart"}:
            self._restart_requested = True
            self.ui.info("reiniciando…")
            return True
        if command in {"/", "/ajuda", "/help"}:
            self.ui.help()
        elif command == "/status":
            await self._show_status()
        elif command == "/novo":
            await self._new_session()
        elif command == "/sessao":
            self.ui.info(f"sessão OpenCode: {self.session_id}")
        elif command == "/buscar":
            if not argument:
                self.ui.warning("uso: /buscar <termo>")
            else:
                hits = await asyncio.to_thread(
                    self.index.search, argument, max(5, self.cfg.secondbrain.context_results)
                )
                self.ui.search_results(hits)
        elif command == "/nota":
            if not argument:
                self.ui.warning("uso: /nota <título>")
            else:
                try:
                    note = await asyncio.to_thread(self.brain.read_note, argument)
                    self.ui.print_note(note)
                except FileNotFoundError as exc:
                    self.ui.warning(str(exc))
        elif command == "/indexar":
            self.ui.info("reindexando Second Brain...")
            try:
                stats = await asyncio.to_thread(self.index.reindex, True)
                self.ui.success(
                    f"{stats['indexed']} nota(s) indexada(s), {stats['total']} no total"
                )
            except Exception as exc:
                self.ui.error(f"falha ao indexar: {exc}")
        elif command == "/modelo":
            await self._switch_model(argument)
        elif command in {"/modo", "/perfil", "/persona"}:
            await self._switch_mode(argument)
        elif command in {"/metas", "/objetivos", "/painel"}:
            await self._show_dashboard()
        elif command in {"/bolsa", "/acao", "/ações"}:
            await self._handle_quote(argument)
        elif command in {"/carteira", "/watchlist", "/watch"}:
            await self._handle_watchlist(argument)
        elif command in {"/foco", "/focos"}:
            await self._handle_focus(argument)
        elif command in {"/tarefas", "/tarefa"}:
            await self._handle_tasks(argument)
        elif command == "/voz":
            self._switch_voice(argument)
        elif command == "/interromper":
            await self.client.abort(self.session_id)
            self.tts.stop()
            self.ui.info("interrupção enviada.")
        elif command in {"/lembrar", "/lembrete"}:
            if not argument:
                self.ui.warning("uso: /lembrar <texto> em 30 minutos")
            else:
                spec = parse_reminder(argument, require_prefix=False)
                if spec is None:
                    self.ui.warning("não entendi o horário. Ex.: /lembrar beber água em 30 minutos")
                else:
                    await self._handle_reminder(spec)
        elif command == "/lembretes":
            self.ui.reminders(self.store.pending_reminders())
        elif command == "/cancelar":
            try:
                reminder_id = int(argument)
            except ValueError:
                self.ui.warning("uso: /cancelar <id>")
            else:
                if self.store.cancel_reminder(reminder_id):
                    self.ui.success(f"lembrete #{reminder_id} cancelado")
                else:
                    self.ui.warning(f"lembrete #{reminder_id} não encontrado")
        elif command == "/proativo":
            self._toggle_proactive(argument)
        elif command in {"/continuo", "/contínuo"}:
            self._toggle_continuous(argument)
        elif command in {"/fala", "/ouvir"}:
            self._toggle_continuous("on")
        elif command in {"/texto", "/digitar"}:
            self._toggle_continuous("off")
        elif command == "/contexto":
            self._toggle_context(argument)
        elif command in {"/rapido", "/rápido", "/fast"}:
            self._toggle_fast(argument)
        elif command in {"/chines", "/chinês", "/中文"}:
            self._toggle_chinese(argument)
        elif command in {"/consolidar", "/memoria", "/memória"}:
            await self._handle_consolidate(argument)
        elif command in {"/preferencia", "/preferência"}:
            if not argument:
                self.ui.warning("uso: /preferencia <texto>")
            else:
                await self._handle_preference(argument)
        elif command in {"/preferencias", "/preferências"}:
            text = self.brain.load_preferences()
            self.ui.print_markdown(text or "(sem preferências registradas)", "Preferências")
        elif command == "/confirmar":
            self._toggle_create_confirmation(argument)
        elif command in {"/autoaprovar", "/autoapprove"}:
            self._toggle_auto_approve(argument)
        elif command == "/historico":
            self._show_history()
        else:
            self.ui.warning(f"comando desconhecido: {command} (use /ajuda)")
        return False

    async def _show_status(self) -> None:
        health = await self.client.health()
        sb_stats = self.index.stats()
        mem = self.store.stats()
        self.ui.status(
            self.directory,
            self.session_id,
            self.cfg.opencode.model,
            sb_stats["notes"],
            self.tts.provider,
        )
        self.ui.info(f"servidor: {self.client.endpoint} · saudável={bool(health)}")
        self.ui.info(
            f"memória operacional: {mem['conversations']} conversa(s), "
            f"{mem['turns']} turno(s), {mem['records']} registro(s)"
        )
        pending = self.store.reminder_stats()["pending"]
        self.ui.info(
            f"proativo: {'ativo' if self.cfg.proactive.enabled else 'desativado'} · "
            f"{pending} lembrete(s) pendente(s)"
        )
        self.ui.info(f"second brain: {self.brain.root} · {sb_stats}")
        self.ui.info(f"modo ativo: {self.persona.name} ({self.persona.key})")

    async def _new_session(self) -> None:
        async with self._transition_lock:
            conv = self.store.create_conversation(
                str(self.directory), title="Nova sessão", mode=self.persona.key
            )
            conversation_id = int(conv["id"])
            session = await self.client.create_session(
                title=f"Zaya — {self.directory.name} · {self.persona.name}",
                model=self.client.model,
            )
            self.store.set_session(conversation_id, session.id)
            self.conversation_id = conversation_id
            self.session_id = session.id
            self._last_assistant_message_id = None
            self._resume_context = ""
            self.store.set_meta("mode", self.persona.key)
        self.ui.success(f"nova sessão: {session.id}")

    async def _switch_model(self, argument: str) -> None:
        if not argument:
            self.ui.warning("uso: /modelo <provider/model>")
            return
        from zaya.opencode.models import ModelRef

        try:
            model = ModelRef.parse(argument)
        except ValueError as exc:
            self.ui.error(str(exc))
            return
        self.cfg.opencode.model = model.slug
        self.store.set_meta("model", model.slug)
        self.ui.success(f"modelo da próxima mensagem: {model.slug}")

    async def _show_dashboard(self) -> None:
        moon = moon_details()
        note = ""
        phase = principal_phase_today(datetime.now().astimezone() + timedelta(days=1))
        if phase:
            _key, name, moment = phase
            try:
                manifestation = await self.agenda.lunation_note(
                    moment.date().isoformat()
                )
            except AgendaError:
                manifestation = ""
            note = f"Amanhã: {name}"
            if manifestation:
                note += f" — manifestações na Agenda: {manifestation}"
        self.ui.dashboard(self.focus.current, self.goals.all(), moon, note)

    async def _handle_quote(self, argument: str) -> None:
        ticker = argument.strip().split()[0] if argument.strip() else ""
        if not ticker:
            self.ui.warning("uso: /bolsa <ticker> (ex.: PETR4, AAPL)")
            return
        try:
            quote = await fetch_quote(ticker)
        except FinanceError as exc:
            self.ui.error(str(exc))
            return
        self.ui.info(format_quote(quote))

    async def _handle_watchlist(self, argument: str) -> None:
        parts = argument.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub in {"add", "adicionar", "+"}:
            if not rest:
                self.ui.warning("uso: /carteira add <ticker> ou /carteira add Nome=TICKER")
                return
            if "=" in rest:
                label, ticker = rest.split("=", 1)
            else:
                label, ticker = rest, rest
            item = self.watchlist.add(label, ticker)
            self.ui.success(f"{item.label} ({item.ticker}) na carteira")
            return
        if sub in {"remove", "remover", "del", "-"}:
            if not rest:
                self.ui.warning("uso: /carteira remover <ticker>")
                return
            if self.watchlist.remove(rest):
                self.ui.success(f"{rest.upper()} removido da carteira")
            else:
                self.ui.warning(f"{rest.upper()} não estava na carteira")
            return
        items = self.watchlist.all()
        if not items:
            self.ui.info("carteira vazia; use /carteira add <ticker>")
            return
        results = await asyncio.gather(
            *(fetch_quote(item.ticker) for item in items), return_exceptions=True
        )
        rows: list[dict[str, str]] = []
        for item, result in zip(items, results, strict=False):
            if isinstance(result, BaseException):
                rows.append(
                    {
                        "label": item.label,
                        "ticker": item.ticker,
                        "price": "?",
                        "change": "?",
                    }
                )
                continue
            price = f"{result.price:.2f}" if result.price is not None else "?"
            change = (
                f"{result.change_pct:+.2f}%"
                if result.change_pct is not None
                else "?"
            )
            rows.append(
                {
                    "label": item.label,
                    "ticker": result.symbol,
                    "price": f"{result.currency} {price}".strip(),
                    "change": change,
                }
            )
        self.ui.watchlist(rows)

    async def _handle_focus(self, argument: str) -> None:
        parts = argument.strip().split(maxsplit=2)
        sub = parts[0].lower() if parts else ""
        if sub in {"definir", "novo", "set"}:
            if len(parts) < 3:
                self.ui.warning("uso: /foco definir semanal|mensal|trimestral <texto>")
                return
            period_raw = parts[1].lower()
            period = {
                "semanal": "week",
                "semana": "week",
                "mensal": "month",
                "mes": "month",
                "mês": "month",
                "trimestral": "quarter",
                "trimestre": "quarter",
            }.get(period_raw)
            if period is None:
                self.ui.warning("período inválido; use semanal, mensal ou trimestral")
                return
            text = parts[2].strip()
            goals = [part.strip() for part in re.split(r"[;,]", text) if part.strip()]
            if len(goals) <= 1:
                goals = []
            focus = self.focus.set(period, text, goals)
            self.ui.success(f"foco {period_label(focus.period)} definido até {focus.end}")
            await self._show_dashboard()
            return
        if sub in {"limpar", "clear", "remover"}:
            self.focus.clear()
            self.ui.success("foco limpo")
            return
        current = self.focus.current
        if current is None:
            self.ui.info("nenhum foco definido; use /foco definir semanal <texto>")
        else:
            self.ui.info(
                f"foco {period_label(current.period)}: {current.label} "
                f"({current.start} → {current.end})"
            )
            for goal in current.goals:
                self.ui.info(f"  • {goal}")
        detail = moon_details()
        self.ui.info(
            f"{detail.moon_emoji} {detail.phase_name} · "
            f"{detail.sign_symbol} {detail.sign_name} · "
            f"{detail.element_emoji} {detail.element} · {detail.phase_emoji}"
        )

    def _find_task(self, query: str) -> Task | None:
        query = query.strip().lower()
        if not query:
            return None
        for task in self.tasks.all():
            if task.id.startswith(query) or query in task.title.lower():
                return task
        return None

    async def _handle_tasks(self, argument: str) -> None:
        parts = argument.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub in {"limpar", "clean", "limpar-concluidas"}:
            count = self.tasks.clear_done()
            self.ui.success(f"{count} tarefa(s) concluída(s) removida(s)")
            return
        if sub in {"sync", "sincronizar", "atualizar"}:
            try:
                pages = await self.agenda.pages(30)
            except AgendaError as exc:
                self.ui.error(str(exc))
                return
            page = next(
                (
                    item
                    for item in pages
                    if "tarefa" in str(item.get("title") or "").lower()
                ),
                None,
            )
            if page is None:
                self.ui.warning("não achei uma página de tarefas na Agenda")
                return
            synced = self.tasks.sync_text(str(page.get("content") or ""))
            self.ui.success(
                f"{len(synced)} tarefa(s) sincronizada(s) de '{page.get('title')}'"
            )
        elif sub in {"fazer", "executar", "run"}:
            task = self._find_task(rest)
            if task is None:
                self.ui.warning("uso: /tarefas fazer <id ou trecho do título>")
                return
            self.tasks.set_status(task.id, "in_progress")
            prompt = (
                f"Tarefa de longo prazo registrada na Agenda: {task.title}\n\n"
                "Execute isso no repositório atual, com mudanças mínimas e testes. "
                "Ao terminar, explique o que fez e o que ainda falta."
            )
            await self._handle_task_impl(prompt, speak=True)
            return
        elif sub in {"concluir", "done", "feito"}:
            task = self._find_task(rest)
            if task is None:
                self.ui.warning("uso: /tarefas concluir <id ou trecho do título>")
                return
            self.tasks.set_status(task.id, "done")
            self.ui.success(f"tarefa concluída: {task.title}")
            return

        tasks = self.tasks.all()
        if not tasks:
            self.ui.info("nenhuma tarefa registrada; use /tarefas sync")
            return
        symbols = {"done": "✓", "in_progress": "◐", "pending": "•"}
        for task in tasks:
            symbol = symbols.get(task.status, "•")
            self.ui.info(f"{symbol} [{task.status}] {task.id} — {task.title}")
            if task.notes:
                self.ui.info(f"   {task.notes}")

    async def _switch_mode(self, argument: str) -> None:
        if not argument:
            self.ui.info(f"modo atual: {self.persona.name} ({self.persona.key})")
            for item in PERSONAS.values():
                self.ui.info(f"  {item.key}: {item.name} — {item.summary}")
            return
        persona = get_persona(argument)
        if persona is None:
            options = ", ".join(PERSONAS)
            self.ui.warning(f"modo desconhecido: {argument} (opções: {options})")
            return
        await self.select_mode(persona.key)
        self.ui.success(f"modo ativo: {persona.name} — {persona.summary}")

    def _switch_voice(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "ligar"}:
            self.tts.enabled = True
            self.ui.success("voz ativada")
        elif arg in {"off", "desligar"}:
            self.tts.enabled = False
            self.ui.success("voz desativada")
        elif arg in {"edge", "say", "none"}:
            self.tts.set_provider(arg)
            self.ui.success(f"provedor de voz: {arg}")
        elif not arg:
            self.ui.info(
                f"voz={'ligada' if self.tts.enabled else 'desligada'} · provedor={self.tts.provider}"
            )
        else:
            self.ui.warning("uso: /voz on|off|edge|say|none")

    def _toggle_chinese(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.interface.chinese_echo = arg == "on"
        else:
            self.cfg.interface.chinese_echo = not self.cfg.interface.chinese_echo
        state = "ativado" if self.cfg.interface.chinese_echo else "desativado"
        self.ui.success(f"eco em chinês {state}")

    async def translate_zh(self, text: str) -> str:
        """Traduz para chinês simplificado e devolve '中文 | pinyin'."""
        source = (text or "").strip()
        if not source:
            return ""
        cached = self._zh_cache.get(source)
        if cached:
            return cached
        prompt = (
            "Traduza o texto abaixo para chinês simplificado e escreva também "
            "o pinyin. Responda APENAS no formato: 中文 | pinyin\n\n"
            f"Texto: {source}"
        )
        system = "Você é um tradutor português-chinês. Seja fiel e conciso."
        try:
            if not self._zh_session_id:
                session = await self.client.create_session(
                    title="Zaya — 中文", model=self.client.model
                )
                self._zh_session_id = session.id
            response = await asyncio.wait_for(
                self.client.prompt_sync(
                    self._zh_session_id, prompt, system=system
                ),
                timeout=20.0,
            )
            output = assistant_text(response) if isinstance(response, dict) else ""
            output = " ".join(output.split()).strip()
            if output:
                self._zh_cache[source] = output
                return output
        except Exception as exc:  # pragma: no cover - depende do modelo/rede
            self.logger.warning("tradução zh falhou: %s", exc)
        return ""

    def _toggle_fast(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.interface.fast = arg == "on"
        else:
            self.cfg.interface.fast = not self.cfg.interface.fast
        state = "ativado" if self.cfg.interface.fast else "desativado"
        self.ui.success(f"modo rápido {state}")

    def _toggle_context(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.secondbrain.auto_context = arg == "on"
        else:
            self.cfg.secondbrain.auto_context = not self.cfg.secondbrain.auto_context
        state = "ativado" if self.cfg.secondbrain.auto_context else "desativado"
        self.ui.success(f"contexto automático do Second Brain {state}")

    def _toggle_create_confirmation(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.secondbrain.confirm_create = arg == "on"
        else:
            self.cfg.secondbrain.confirm_create = not self.cfg.secondbrain.confirm_create
        state = "pedindo confirmação" if self.cfg.secondbrain.confirm_create else "sem confirmação"
        self.ui.success(f"criação de nota: {state}")

    def _toggle_auto_approve(self, argument: str) -> None:
        arg = argument.lower().strip()
        if arg in {"on", "off"}:
            self.cfg.opencode.auto_approve = arg == "on"
        else:
            self.cfg.opencode.auto_approve = not self.cfg.opencode.auto_approve
        if self.cfg.opencode.auto_approve:
            self.ui.warning(
                "auto-aprovação de ferramentas ATIVA: o agente pode executar "
                "comandos e editar arquivos sem perguntar."
            )
        else:
            self.ui.success("auto-aprovação de ferramentas desativada")

    def _show_history(self) -> None:
        assert self.conversation_id is not None
        turns = self.store.turns(self.conversation_id, limit=10)
        if not turns:
            self.ui.info("sem histórico nesta conversa.")
            return
        for turn in turns:
            text = str(turn["text"])
            if len(text) > 300:
                text = text[:300] + "..."
            self.ui.console.print(f"[bold]{escape_user(turn['role'])}[/]: {escape_user(text)}")
