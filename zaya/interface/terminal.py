"""Interface de terminal (Rich), limpa e sem Live para nao conflitar com input."""

from __future__ import annotations

import threading
from pathlib import Path

from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

ACCENT = "#D644FB"
CYAN = "#79DCFD"
MINT = "#A8EFDB"
LILAC = "#E6C3FD"
MUTED = "grey62"

_ASCII_ART = [
    " ███████╗ ██████╗ ██████╗  █████╗ ",
    " ╚══███╔╝██╔═══██╗██╔══██╗██╔══██╗",
    "   ███╔╝ ██║   ██║██████╔╝███████║",
    "  ███╔╝  ██║   ██║██╔══██╗██╔══██║",
    " ███████╗╚██████╔╝██║  ██║██║  ██║",
    " ╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝",
]
_ASCII_COLORS = [LILAC, LILAC, ACCENT, CYAN, MINT, MINT]

_ASCII_METAS = [
    " ███╗   ███╗███████╗████████╗ █████╗ ███████╗",
    " ████╗ ████║██╔════╝╚══██╔══╝██╔══██╗██╔════╝",
    " ██╔████╔██║█████╗     ██║   ███████║███████╗",
    " ██║╚██╔╝██║██╔══╝     ██║   ██╔══██║╚════██║",
    " ██║ ╚═╝ ██║███████╗   ██║   ██║  ██║███████║",
    " ╚═╝     ╚═╝╚══════╝   ╚═╝   ╚═╝  ╚═╝╚══════╝",
]


def _message_title(name: str, color: str) -> Text:
    return Text(f"{name}:", style=f"bold {color}")


class TerminalUI:
    def __init__(self, console: Console | None = None, quiet: bool = False):
        self.console = console or Console()
        self._quiet = quiet
        self._tty = bool(getattr(self.console, "is_terminal", False)) and not quiet
        self._status_visible = False
        self._status_label = ""
        self._status_detail = ""
        self._status_suspended = False
        self._streaming = False
        self._stream_buffer: list[str] = []
        self._stream_title: Text | None = None
        self._live: Live | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ status
    def _write_raw(self, text: str) -> None:
        if self._quiet or self._live is not None:
            return
        file = getattr(self.console, "file", None)
        if file is None:
            return
        with self._lock:
            try:
                file.write(text)
                file.flush()
            except Exception:
                pass

    def erase_input_line(self, lines: int = 1) -> None:
        """Apaga a linha digitada no prompt (inclui quebras) antes de ecoar na caixa."""
        if not self._tty:
            return
        with self._lock:
            self._write_raw("\r\x1b[2K")
            for _ in range(max(0, lines)):
                self._write_raw("\x1b[1A\r\x1b[2K")

    def _erase_status(self) -> None:
        if self._status_visible:
            self._write_raw("\r\x1b[2K")
            self._status_visible = False

    def _draw_status(self) -> None:
        if not self._tty or self._status_suspended or self._streaming:
            return
        label = self._status_label
        detail = self._status_detail
        if not label and not detail:
            return
        text = "  ".join(part for part in (label, detail) if part)
        self._write_raw("\r\x1b[2K" + text)
        self._status_visible = True

    def set_status(self, label: str, detail: str = "") -> None:
        if self._quiet or self._streaming or self._live is not None:
            return
        if self._tty and (label, detail) == (self._status_label, self._status_detail):
            if self._status_visible:
                return
        self._status_label = label
        self._status_detail = detail
        if self._tty:
            self._erase_status()
            self._draw_status()

    def clear_status(self) -> None:
        if self._quiet or self._streaming or self._live is not None:
            return
        self._status_label = ""
        self._status_detail = ""
        self._erase_status()

    def suspend_status(self) -> None:
        self._status_suspended = True
        self._erase_status()

    def resume_status(self) -> None:
        self._status_suspended = False
        self._draw_status()

    def is_streaming(self) -> bool:
        return self._streaming

    def _print(self, *args, **kwargs) -> None:
        """Imprime conteudo, preservando (ou limpando) a linha de status."""
        if self._quiet:
            return
        manage = self._tty and not self._streaming and self._live is None
        with self._lock:
            if manage:
                self._erase_status()
            self.console.print(*args, **kwargs)
            if manage:
                self._draw_status()

    # ------------------------------------------------------------------ banner
    def banner(
        self,
        project: Path,
        session_id: str,
        model: str,
        sb_notes: int,
        voice: str,
        tts_enabled: bool,
    ) -> None:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style=MUTED, no_wrap=True)
        grid.add_column()
        grid.add_row("Projeto", f"[bold]{escape(str(project))}[/bold]")
        grid.add_row("Sessão", escape(session_id))
        grid.add_row("Modelo", escape(model))
        grid.add_row("Memória", f"Second Brain conectado · {sb_notes} nota(s)")
        grid.add_row("Voz", ("ativa · " if tts_enabled else "desativada · ") + escape(voice))
        title = Text("Zaya", style=f"bold {ACCENT}")
        self._print()
        for line, color in zip(_ASCII_ART, _ASCII_COLORS, strict=False):
            self._print(Text(line, style=f"bold {color}"))
        self._print(Text("assistente pessoal · OpenCode · Second Brain", style=MUTED))
        self._print()
        self._print(Panel(grid, title=title, border_style=ACCENT, padding=(0, 1)))
        self._print()

    def status(self, project: Path, session_id: str, model: str, sb_notes: int, voice: str) -> None:
        self._print(
            f"[{MUTED}]projeto[/] {escape(str(project))}  "
            f"[{MUTED}]sessão[/] {escape(session_id)}  "
            f"[{MUTED}]modelo[/] {escape(model)}  "
            f"[{MUTED}]memória[/] {sb_notes} nota(s)  "
            f"[{MUTED}]voz[/] {escape(voice)}"
        )

    # ------------------------------------------------------------------- msgs
    def hint(self, text: str) -> None:
        self._print(f"[{MUTED}]{escape(text)}[/]")

    def info(self, text: str) -> None:
        self._print(f"[{CYAN}]•[/] {escape(text)}")

    def success(self, text: str) -> None:
        self._print(f"[{MINT}]✓[/] {escape(text)}")

    def warning(self, text: str) -> None:
        self._print(f"[yellow]![/] {escape(text)}")

    def error(self, text: str) -> None:
        self._print(f"[red]✗[/] {escape(text)}")

    def print_user(self, text: str, voice: bool = False) -> None:
        self._print(
            Panel(
                escape(text),
                title=_message_title("Talita", CYAN),
                title_align="left",
                subtitle=Text("voz", style=MUTED) if voice else None,
                subtitle_align="right",
                border_style=CYAN,
                padding=(0, 1),
            )
        )

    def print_transcript(self, text: str) -> None:
        self.print_user(text, voice=True)

    def partial(self, text: str) -> None:
        clean = " ".join((text or "").split())
        if not clean:
            return
        if self._tty:
            self.set_status("", f"\x1b[2m… {clean}\x1b[0m")
        else:
            self._print(f"[{MUTED}]… {escape(clean)}[/]")

    # ---------------------------------------------------------------- streaming
    def _render_stream_panel(self) -> Panel:
        text = "".join(self._stream_buffer)
        title = self._stream_title or _message_title("Zaya", ACCENT)
        return Panel(
            escape(text),
            title=title,
            title_align="left",
            border_style=ACCENT,
            padding=(0, 1),
        )

    def _start_live(self) -> None:
        with self._lock:
            if not self._tty or self._live is not None or not self._streaming:
                return
            live = Live(
                self._render_stream_panel(),
                console=self.console,
                refresh_per_second=10,
                transient=True,
                vertical_overflow="ellipsis",
            )
            self._live = live
        live.start(refresh=True)

    def _suspend_live(self) -> None:
        with self._lock:
            live = self._live
            self._live = None
        if live is not None:
            live.stop()

    def _resume_live(self) -> None:
        self._start_live()

    def begin_stream(self) -> None:
        if self._quiet:
            return
        with self._lock:
            self._status_label = ""
            self._status_detail = ""
            self._erase_status()
            self._streaming = True
            self._stream_buffer = []
            self._stream_title = _message_title("Zaya", ACCENT)
            tty = self._tty
            if not tty:
                self._write_raw("\r\x1b[2K◐ escrevendo…")
                self._status_visible = True
        if tty:
            self._start_live()

    def token(self, text: str) -> None:
        if not text or self._quiet:
            return
        with self._lock:
            self._stream_buffer.append(text)
            live = self._live
        if live is not None:
            live.update(self._render_stream_panel(), refresh=True)

    def close(self) -> None:
        with self._lock:
            live = self._live
            self._live = None
            self._streaming = False
        if live is not None:
            try:
                live.stop()
            except Exception:
                pass
        self._erase_status()

    def end_stream(self) -> None:
        if self._quiet:
            return
        with self._lock:
            self._streaming = False
            text = "".join(self._stream_buffer)
            self._stream_buffer = []
            live = self._live
            self._live = None
            title = self._stream_title
        if live is not None:
            live.stop()
        if text.strip():
            self._print(
                Panel(
                    escape(text),
                    title=title or _message_title("Zaya", ACCENT),
                    title_align="left",
                    border_style=ACCENT,
                    padding=(0, 1),
                )
            )
        else:
            self._draw_status()

    def print_markdown(self, text: str, title: str | None = None) -> None:
        header = title or _message_title("Zaya", ACCENT)
        self._print(
            Panel(
                Markdown(text or ""),
                title=header,
                title_align="left",
                border_style=ACCENT,
                padding=(0, 1),
            )
        )

    def proactive(self, text: str) -> None:
        self._print()
        self._print(
            Panel(
                escape(text),
                title=_message_title("Zaya", ACCENT),
                title_align="left",
                subtitle=Text("iniciativa", style=MUTED),
                subtitle_align="right",
                border_style=ACCENT,
                padding=(0, 1),
            )
        )

    def reminders(self, rows) -> None:
        if not rows:
            self.info("nenhum lembrete pendente")
            return
        table = Table(title="Lembretes", border_style=CYAN)
        table.add_column("#", style=f"bold {CYAN}", no_wrap=True)
        table.add_column("quando", no_wrap=True)
        table.add_column("lembrete")
        for row in rows:
            due = str(row["due_at"])
            try:
                from datetime import datetime

                due = datetime.fromisoformat(due).strftime("%d/%m %H:%M")
            except ValueError:
                pass
            table.add_row(str(row["id"]), due, str(row["text"]))
        self._print(table)

    def tool(self, name: str, detail: str = "") -> None:
        detail_text = f" [white]{escape(detail)}[/]" if detail else ""
        self._print(f"  [{CYAN}]▸ {escape(name)}[/]{detail_text}")

    def dashboard(self, focus, goals: list, moon=None, moon_note: str = "") -> None:
        if not focus and not goals:
            self.warning("nenhuma meta configurada")
            return
        self._print()
        for line, color in zip(_ASCII_METAS, _ASCII_COLORS, strict=False):
            self._print(Text(line, style=f"bold {color}"))
        if focus:
            from zaya.focus import period_label

            body = Text()
            if focus.label:
                body.append(focus.label + "\n", style="bold")
            for goal in focus.goals:
                body.append(f"• {goal}\n")
            body.append(f"\n{focus.start} → {focus.end}", style=MUTED)
            self._print(
                Panel(
                    body,
                    title=Text(
                        f"Foco {period_label(focus.period)}", style=f"bold {CYAN}"
                    ),
                    title_align="left",
                    border_style=CYAN,
                    padding=(0, 1),
                )
            )
        else:
            self.info("nenhum foco definido; use /foco definir semanal <texto>")
        if goals:
            table = Table(
                box=box.SIMPLE_HEAD,
                border_style=ACCENT,
                header_style=f"bold {ACCENT}",
            )
            table.add_column("Meta", style="bold")
            table.add_column("Próximo passo", style=MUTED)
            for goal in goals:
                table.add_row(escape(goal.label), escape(goal.next_step or "—"))
            self._print(
                Panel(
                    table,
                    title=Text("Metas de longo prazo", style=f"bold {ACCENT}"),
                    title_align="left",
                    border_style=ACCENT,
                    padding=(0, 1),
                )
            )
        if moon:
            if hasattr(moon, "phase_name"):
                line = (
                    f"{moon.moon_emoji} {moon.phase_name} · "
                    f"{moon.sign_symbol} {moon.sign_name} · "
                    f"{moon.element_emoji} {moon.element} · {moon.phase_emoji}"
                )
            else:
                _key, name, illumination = moon
                line = f"Lua: {name} · {illumination * 100:.0f}% iluminada"
            self._print(Text(line, style=MUTED))
        if moon_note:
            self._print(Text(moon_note, style=MUTED))

    def watchlist(self, rows: list[dict]) -> None:
        if not rows:
            self.info("carteira vazia; use /carteira add <ticker>")
            return
        table = Table(
            title="Carteira",
            border_style=ACCENT,
            header_style=f"bold {ACCENT}",
        )
        table.add_column("Ativo")
        table.add_column("Ticker", style=MUTED)
        table.add_column("Preço", justify="right")
        table.add_column("Variação", justify="right")
        for row in rows:
            change = str(row.get("change") or "?")
            if change.startswith("+"):
                change_text = Text(change, style="green")
            elif change.startswith("-"):
                change_text = Text(change, style="red")
            else:
                change_text = Text(change, style=MUTED)
            table.add_row(
                escape(str(row.get("label") or "")),
                escape(str(row.get("ticker") or "")),
                escape(str(row.get("price") or "?")),
                change_text,
            )
        self._print(table)

    def reasoning(self, text: str) -> None:
        clean = " ".join((text or "").split())
        if not clean:
            return
        self.set_status("◐ pensando…", clean[-110:])

    def diff(self, filename: str, patch: str) -> None:
        if not patch.strip():
            return
        self._print()
        self._print(
            Panel(
                Syntax(patch, "diff", theme="monokai", word_wrap=True),
                title=f"diff · {escape(filename)}",
                title_align="left",
                border_style=MINT,
                padding=(0, 1),
            )
        )

    # ---------------------------------------------------------------- context
    def context_hits(self, hits) -> None:
        if not hits:
            return
        self._print(f"[{MUTED}]notas relacionadas:[/]")
        for hit in hits:
            label = f"{hit.title} — {hit.section}" if getattr(hit, "section", "") else hit.title
            self._print(
                f"  [{MUTED}]•[/] [bold]{escape(label)}[/] [{MUTED}]({escape(hit.rel_path)})[/]"
            )

    def search_results(self, hits) -> None:
        if not hits:
            self.warning("nenhuma nota encontrada")
            return
        for hit in hits:
            label = f"{hit.title} — {hit.section}" if getattr(hit, "section", "") else hit.title
            self._print(f"[bold]{escape(label)}[/] [{MUTED}]({escape(hit.rel_path)})[/]")
            if hit.snippet:
                self._print(f"  [{MUTED}]{escape(hit.snippet)}[/]")

    def note_preview(
        self,
        title: str,
        tags: list[str],
        links: list[str],
        content: str,
        folder: str,
    ) -> None:
        preview = content if len(content) <= 600 else content[:600].rstrip() + "..."
        body = Text()
        body.append(f"pasta: {folder}\n", style=MUTED)
        body.append(f"tags: {', '.join(tags) or '-'}\n", style=MUTED)
        if links:
            body.append(f"links: {', '.join('[[' + link + ']]' for link in links)}\n", style=MUTED)
        body.append("\n")
        body.append(preview)
        self._print(Panel(body, title=f"Nova nota · {title}", border_style=MINT))

    def print_note(self, note) -> None:
        header = f"{note.title}  ({note.rel_path})"
        self._print(Panel(escape(note.body[:4000]), title=escape(header), border_style=MUTED))

    # ------------------------------------------------------------------ input
    def prompt(self, label: str = "Você") -> str:
        if self._quiet:
            return ""
        self._suspend_live()
        self.suspend_status()
        try:
            return self.console.input(f"[cyan]{escape(label)}[/]: ")
        finally:
            self.resume_status()
            self._resume_live()

    def confirm(self, question: str, default: bool = False) -> bool:
        if self._quiet:
            return default
        suffix = " [S/n]" if default else " [s/N]"
        self._suspend_live()
        self.suspend_status()
        try:
            raw = self.console.input(f"[yellow]?[/] {escape(question)}{suffix} ").strip().lower()
        except EOFError:
            return False
        finally:
            self.resume_status()
            self._resume_live()
        if not raw:
            return default
        return raw in {"s", "sim", "y", "yes"}

    def permission(self, tool: str, patterns: list[str]) -> str:
        if self._quiet:
            return "reject"
        self._suspend_live()
        self.suspend_status()
        try:
            self._print(
                Panel(
                    f"O OpenCode quer usar [bold]{escape(tool)}[/]\n"
                    + ("padrões: " + escape(", ".join(patterns)) if patterns else ""),
                    title="permissão necessária",
                    border_style="yellow",
                )
            )
            try:
                raw = (
                    self.console.input(
                        "[yellow]?[/] o = uma vez · a = sempre · r = rejeitar (padrão: rejeitar): "
                    )
                    .strip()
                    .lower()
                )
            except EOFError:
                return "reject"
        finally:
            self.resume_status()
            self._resume_live()
        if raw in {"o", "once", "uma"}:
            return "once"
        if raw in {"a", "always", "sempre"}:
            return "always"
        return "reject"

    # ------------------------------------------------------------------- misc
    def help(self) -> None:
        table = Table(title="Comandos da Zaya", border_style=ACCENT, show_header=False)
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column()
        rows = [
            ("[Enter]", "gravar uma instrução por voz"),
            ("/ajuda", "mostra esta ajuda"),
            ("/status", "servidor, sessão, modelo, memória e voz"),
            ("/buscar <termo>", "pesquisa no Second Brain"),
            ("/nota <título>", "lê uma nota do Second Brain"),
            ("/indexar", "reindexa o Second Brain"),
            ("/novo", "inicia nova conversa e sessão no OpenCode"),
            ("/modelo <provider/model>", "troca o modelo da sessão"),
            ("/modo [nome]", "lista ou troca o modo (academica, carreira, assistente, conversa, financas)"),
            ("/bolsa <ticker>", "cotação rápida (ex.: PETR4, AAPL)"),
            ("/carteira [add|remover]", "lista/acompanha seus ativos"),
            ("/metas", "painel visual com foco atual e metas de longo prazo"),
            ("/painel", "mesmo painel de metas"),
            ("/foco [definir|limpar]", "define/limpa o foco do ciclo"),
            ("/contexto on|off", "liga/desliga contexto automático do Second Brain"),
            ("/rapido on|off", "modo rápido (sem contexto, respostas curtas)"),
            ("/chines on|off", "mostra chinês + pinyin de cada mensagem"),
            ("/tarefas [sync|limpar]", "lista/sincroniza/limpa tarefas concluídas"),
            ("/voz on|off|edge|say|none", "controla a voz"),
            ("/preferencia <texto>", "aprende uma preferência (taste)"),
            ("/preferencias", "mostra as preferências aprendidas"),
            ("/consolidar [hoje|ontem|AAAA-MM-DD]", "salva a memória do dia no Second Brain"),
            ("/lembrar <texto> em <tempo>", "cria um lembrete falado"),
            ("/lembretes", "lista lembretes pendentes"),
            ("/cancelar <id>", "cancela um lembrete"),
            ("/proativo on|off", "liga/desliga iniciativa da Zaya"),
            ("/continuo on|off", "escuta contínua (sem Enter a cada fala)"),
            ("/fala", "volta para a escuta contínua por voz"),
            ("/texto", "volta para o modo texto (digitar)"),
            ("/confirmar on|off", "pede (ou não) confirmação ao criar nota"),
            ("/autoaprovar on|off", "auto-aprova ferramentas do OpenCode (perigoso)"),
            ("/interromper", "aborta a execução atual no OpenCode"),
            ("/sessao", "mostra o ID da sessão OpenCode"),
            ("/reiniciar", "reinicia a Zaya com o código novo"),
            ("/sair", "encerra a Zaya"),
        ]
        for command, description in rows:
            table.add_row(command, description)
        self._print(table)

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        mark = f"[{MINT}]✓[/]" if ok else "[red]✗[/]"
        suffix = f" [{MUTED}]{escape(detail)}[/]" if detail else ""
        self._print(f"{mark} {escape(label)}{suffix}")
