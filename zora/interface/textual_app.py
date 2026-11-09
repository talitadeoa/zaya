"""Interface Textual (fase 1): layout premium com chat, entrada e status."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Header, Input, OptionList, Static

from zora.audio.recorder import ContinuousListener
from zora.focus import moon_details, period_label, principal_phase_today

ACCENT = "#D644FB"
CYAN = "#79DCFD"
MINT = "#A8EFDB"
LILAC = "#E6C3FD"
MUTED = "grey62"

_ASCII_ZORA = [
    " ███████╗ ██████╗ ██████╗  █████╗ ",
    " ╚══███╔╝██╔═══██╗██╔══██╗██╔══██╗",
    "   ███╔╝ ██║   ██║██████╔╝███████║",
    "  ███╔╝  ██║   ██║██╔══██╗██╔══██║",
    " ███████╗╚██████╔╝██║  ██║██║  ██║",
    " ╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝",
]
_ASCII_COLORS = [LILAC, LILAC, ACCENT, CYAN, MINT, MINT]

_SHORTCUTS = (
    "i digitar · t texto · m mudo · v voz · f falar · c contínua · "
    "Esc interromper · F1 comandos · Ctrl+T tema · Ctrl+L limpar"
)

_HELP = (
    "Modo normal: i digitar · t texto · m mudo · v voz on/off · f falar · "
    "c escuta contínua · Esc interromper. No modo insert, Esc volta ao normal. "
    "F1 paleta · Ctrl+T tema · Ctrl+L limpar. "
    "Os demais comandos (agenda, tarefas, metas, finanças) continuam no terminal."
)


class CommandPalette(ModalScreen[str | None]):
    CSS = """
    CommandPalette {
        align: center middle;
        background: #000000aa;
    }
    #palette {
        width: 64;
        height: auto;
        max-height: 20;
        border: round #D644FB;
        background: #140a24;
        padding: 1;
    }
    #palette-input {
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Vertical(
            Input(placeholder="Buscar comando…", id="palette-input"),
            OptionList(id="palette-list"),
            id="palette",
        )

    def on_mount(self) -> None:
        self._refresh("")
        self.query_one("#palette-input", Input).focus()

    def _refresh(self, query: str) -> None:
        options = self.query_one("#palette-list", OptionList)
        options.clear_options()
        needle = query.lower().strip()
        for label in self.app.palette_labels():
            if needle in label.lower():
                options.add_option(label)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.prompt))


class TextualSink:
    """Recebe tokens e eventos do orquestrador e atualiza a UI."""

    def __init__(self, app: "ZoraTextualApp"):
        self.app = app
        self.buffer = ""
        self._last_update = 0.0

    def token(self, text: str) -> None:
        self.buffer += text
        now = time.monotonic()
        if now - self._last_update >= 0.06:
            self._last_update = now
            self.app.update_stream(self.buffer)

    def reasoning(self, text: str) -> None:
        snippet = " ".join((text or "").split())[-90:]
        if snippet:
            self.app._set_status(f"◐ {snippet}")

    def tool(self, name: str, detail: str = "") -> None:
        line = f"▸ {name} {detail}".strip()
        self.app._set_status(line)

    def diff(self, filename: str, patch: str) -> None:
        self.app.write_diff(filename, patch)


class ZoraTextualApp(App):
    BINDINGS = [
        Binding("i", "insert", "Digitar"),
        Binding("enter", "insert", "Digitar"),
        Binding("t", "text_mode", "Texto"),
        Binding("m", "mute", "Mudo"),
        Binding("v", "toggle_voice", "Voz"),
        Binding("f", "push_to_talk", "Falar"),
        Binding("c", "toggle_listen", "Contínua"),
        Binding("escape", "escape", "Voltar", priority=True),
        Binding("f1", "command_palette", "Comandos"),
        Binding("ctrl+t", "toggle_theme", "Tema"),
        Binding("ctrl+l", "clear_chat", "Limpar"),
    ]

    CSS = """
    Screen {
        background: #0b0614;
        color: #e5e7eb;
    }
    #chat {
        border: round #D644FB;
        padding: 0 1;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-color: #79DCFD;
        scrollbar-background: #140a24;
    }
    .card {
        margin-bottom: 1;
    }
    #status {
        height: 1;
        color: #A8EFDB;
        padding: 0 1;
    }
    #commandbar {
        height: 1;
        background: #0e7490;
        color: #e0f2fe;
        padding: 0 1;
    }
    #input {
        border: round #79DCFD;
        height: 5;
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        answer: Callable[[str, TextualSink], Awaitable[str]],
        cleanup: Callable[[], Awaitable[None]] | None = None,
        subtitle: str = "",
        voice: Any | None = None,
    ):
        super().__init__()
        self._answer = answer
        self._cleanup = cleanup
        self._subtitle = subtitle
        self._voice = voice
        self._voice_replies = False
        self._continuous = False
        self._listener = None
        self._listen_task: asyncio.Task | None = None
        self._insert_mode = False
        self._turn_running = False
        self._status_base = ""
        self._dot_phase = 0
        self._stream_widget: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Vertical(
            VerticalScroll(id="chat"),
            Static("", id="status"),
            Input(id="input"),
        )
        yield Static(_SHORTCUTS, id="commandbar")

    async def on_mount(self) -> None:
        self.title = "Zora"
        self.sub_title = self._subtitle
        await self._render_welcome()
        self._normal_mode()
        self.set_interval(0.4, self._tick_status)

    async def _render_welcome(self) -> None:
        log = self._log()
        for line, color in zip(_ASCII_ZORA, _ASCII_COLORS, strict=False):
            self._mount(Text(line, style=f"bold {color}"))
        self._mount(Text("assistente pessoal · OpenCode · Second Brain", style=MUTED))
        if self._voice is not None:
            try:
                self._write_focus_panel()
                self._write_goals_table()
                await self._write_moon_note()
            except Exception:
                pass


    def _write_focus_panel(self) -> None:
        focus = self._voice.focus.current
        if focus is None:
            self._mount(
                Text("Nenhum foco definido; use /foco definir semanal <texto>.", style=MUTED)
            )
            return
        body = Text()
        if focus.label:
            body.append(focus.label + "\n", style="bold")
        for goal in focus.goals:
            body.append(f"• {goal}\n")
        body.append(f"\n{focus.start} → {focus.end}", style=MUTED)
        self._mount(
            Panel(
                body,
                title=Text(
                    f" ✨ Foco {period_label(focus.period)} ",
                    style=f"bold {CYAN} on #08222b",
                ),
                title_align="left",
                border_style=CYAN,
                style="on #08222b",
                padding=(0, 1),
            )
        )

    def _write_goals_table(self) -> None:
        from rich.table import Table

        goals = self._voice.goals.all()
        if not goals:
            return
        table = Table(
            box=box.SIMPLE_HEAD,
            border_style=ACCENT,
            header_style=f"bold {ACCENT}",
        )
        table.add_column("Meta", style="bold")
        table.add_column("Próximo passo", style=MUTED)
        for goal in goals:
            table.add_row(escape(goal.label), escape(goal.next_step or "—"))
        self._mount(
            Panel(
                table,
                title=Text(" 🎯 Metas ", style=f"bold {ACCENT} on #160a28"),
                title_align="left",
                border_style=ACCENT,
                style="on #160a28",
                padding=(0, 1),
            )
        )

    async def _write_moon_note(self) -> None:
        detail = moon_details()
        body = Text(
            f"{detail.moon_emoji} {detail.phase_name} · "
            f"{detail.sign_symbol} {detail.sign_name} · "
            f"{detail.element_emoji} {detail.element} · {detail.phase_emoji} · "
            f"{detail.illumination * 100:.0f}% iluminada"
        )
        phase = principal_phase_today(datetime.now().astimezone() + timedelta(days=1))
        if phase is not None:
            _key, phase_name, moment = phase
            manifestation = ""
            try:
                manifestation = await self._voice.agenda.lunation_note(
                    moment.date().isoformat()
                )
            except Exception:
                manifestation = ""
            line = f"Amanhã: {phase_name}"
            if manifestation:
                line += f" — manifestações na Agenda: {manifestation}"
            body.append("\n" + line, style=MUTED)
        self._mount(
            Panel(
                body,
                title=Text(" 🌙 Lua ", style=f"bold {LILAC} on #0b2a24"),
                title_align="left",
                border_style=CYAN,
                style="on #0b2a24",
                padding=(0, 1),
            )
        )

    async def on_unmount(self) -> None:
        self._stop_listening()
        if self._cleanup is not None:
            try:
                await self._cleanup()
            except Exception:
                pass

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text in {"/sair", "/quit", "/exit"}:
            self.exit()
            return
        if text in {"/reiniciar", "/restart"}:
            self.exit(42)
            return
        if text in {"/", "/ajuda", "/help"}:
            self._write_note(_HELP)
            return
        self.run_worker(self.submit_text(text), exclusive=False)

    async def submit_text(self, text: str) -> None:
        if self._turn_running:
            self._set_status("aguarde o turno atual…")
            return
        self._turn_running = True
        listener = self._listener
        if listener is not None and self._continuous:
            listener.pause()
        delegated = False
        try:
            self._write_user(text)
            status = self.query_one("#status", Static)
            self._stream_widget = None
            self._set_status("◐ pensando")
            sink = TextualSink(self)
            try:
                answer = await self._answer(text, sink)
            except Exception as exc:  # pragma: no cover - depende do backend
                answer = f"Erro: {exc}"
            self.finish_stream(answer or sink.buffer or "")
            if answer and self._voice_replies and self._voice is not None:
                self._set_status("▶ falando")
                delegated = True
                self.run_worker(
                    self._speak_and_resume(answer, listener), exclusive=False
                )
            else:
                self._set_status("● pronto")
        finally:
            if not delegated:
                self._turn_running = False
            if (
                not delegated
                and listener is not None
                and self._continuous
                and listener is self._listener
            ):
                listener.resume()
                self._set_status("● ouvindo…")

    async def _speak_and_resume(self, answer: str, listener) -> None:
        try:
            await self._voice.speak_text(answer)
        except Exception:
            pass
        finally:
            self._turn_running = False
            if (
                listener is not None
                and self._continuous
                and listener is self._listener
            ):
                listener.resume()
                self._set_status("● ouvindo…")
            else:
                self._set_status("● pronto")

    def action_push_to_talk(self) -> None:
        self.run_worker(self._push_to_talk(), exclusive=False)

    async def _push_to_talk(self) -> None:
        if self._voice is None:
            self._set_status("voz indisponível")
            return
        recorder = self._voice.recorder
        transcriber = self._voice.transcriber
        self._set_status("🎙 ouvindo… fale e faça uma pausa")

        def on_state(state: str) -> None:
            try:
                self.call_from_thread(self._set_status, f"🎙 {state}")
            except Exception:
                pass

        try:
            audio = await asyncio.to_thread(
                recorder.record_until_silence, on_state, 20.0
            )
        except Exception:
            self._set_status("microfone indisponível")
            return
        if audio is None or len(audio) == 0:
            self._set_status("não ouvi nada")
            return
        self._set_status("◐ transcrevendo…")
        try:
            text = await asyncio.to_thread(transcriber.transcribe, audio)
        except Exception:
            text = ""
        if not text.strip():
            self._set_status("não entendi")
            return
        await self.submit_text(text.strip())

    async def action_toggle_voice(self) -> None:
        if self._voice is None:
            self._set_status("voz indisponível")
            return
        self._voice_replies = not self._voice_replies
        self._set_status("voz ligada" if self._voice_replies else "voz desligada")

    async def action_interrupt(self) -> None:
        if self._voice is None:
            return
        self._voice.tts.stop()
        try:
            await self._voice.client.abort(self._voice.session_id)
        except Exception:
            pass
        self._set_status("interrompido")

    def action_toggle_listen(self) -> None:
        if self._voice is None:
            self._set_status("voz indisponível")
            return
        if self._continuous:
            self._stop_listening()
            self._set_status("escuta contínua desligada")
            return
        try:
            listener = ContinuousListener(self._voice.cfg.audio)
            listener.start()
        except Exception as exc:
            self._set_status(f"microfone: {exc}")
            return
        self._listener = listener
        self._continuous = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._set_status("● ouvindo…")

    def _stop_listening(self) -> None:
        self._continuous = False
        if self._listen_task is not None:
            self._listen_task.cancel()
            self._listen_task = None
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    async def _listen_loop(self) -> None:
        listener = self._listener
        if listener is None or self._voice is None:
            return
        transcriber = self._voice.transcriber
        try:
            while self._continuous and listener is self._listener:
                utterance = await asyncio.to_thread(listener.next_utterance, 0.5)
                if utterance is None:
                    continue
                listener.pause()
                self._set_status("◐ transcrevendo…")
                try:
                    text = await asyncio.to_thread(transcriber.transcribe, utterance)
                except Exception:
                    text = ""
                if text.strip():
                    await self.submit_text(text.strip())
                else:
                    self._set_status("● ouvindo…")
                if self._continuous and listener is self._listener:
                    listener.resume()
                    self._set_status("● ouvindo…")
        except asyncio.CancelledError:
            pass

    def _set_status(self, text: str) -> None:
        self._status_base = text.rstrip(".…").strip()
        self.query_one("#status", Static).update(text)

    def _tick_status(self) -> None:
        if not self._turn_running or not self._status_base:
            return
        base = self._status_base
        if not ("ndo" in base.lower() or base.startswith(("◐", "▶", "▸", "🎙"))):
            return
        self._dot_phase = (self._dot_phase + 1) % 3
        dots = "." * (self._dot_phase + 1)
        self.query_one("#status", Static).update(self._status_base + dots)

    # ------------------------------------------------------------------ modes
    def _normal_mode(self) -> None:
        self._insert_mode = False
        try:
            self.set_focus(None)
        except Exception:
            pass
        self._set_status("")

    def action_insert(self) -> None:
        self._insert_mode = True
        self.query_one("#input", Input).focus()
        self._set_status("")

    async def action_escape(self) -> None:
        if self._insert_mode:
            self._normal_mode()
        else:
            await self.action_interrupt()

    def action_text_mode(self) -> None:
        self.action_insert()
        self._stop_listening()
        self._set_status("TEXTO · digite e Enter envia")

    def action_mute(self) -> None:
        self._stop_listening()
        if self._voice is not None:
            self._voice.tts.stop()
        self._set_status("mudo")

    # --------------------------------------------------------------- palette
    def palette_labels(self) -> list[str]:
        return [
            "Ajuda",
            "Alternar tema",
            "Limpar conversa",
            "Focar entrada",
            "Reiniciar Zora",
            "Sair",
        ]

    def action_command_palette(self) -> None:
        self.push_screen(CommandPalette(), self._run_palette_action)

    def _run_palette_action(self, label: str | None) -> None:
        if not label:
            return
        if label == "Ajuda":
            self._write_note(_HELP)
        elif label == "Alternar tema":
            self.action_toggle_theme()
        elif label == "Limpar conversa":
            self.action_clear_chat()
        elif label == "Focar entrada":
            self.query_one("#input", Input).focus()
        elif label == "Reiniciar Zora":
            self.exit(42)
        elif label == "Sair":
            self.exit()

    def action_toggle_theme(self) -> None:
        self.theme = "textual-light" if "dark" in self.theme else "textual-dark"

    def action_clear_chat(self) -> None:
        self._log().remove_children()
        self._stream_widget = None

    # ------------------------------------------------------------------ helpers
    def _scroll_chat(self) -> None:
        try:
            self._log().scroll_end(animate=False)
        except Exception:
            pass

    def _mount(self, renderable) -> None:
        self._log().mount(Static(renderable, classes="card"))
        self.call_after_refresh(self._scroll_chat)

    def update_stream(self, text: str) -> None:
        if self._stream_widget is None:
            self._stream_widget = Static(classes="card")
            self._log().mount(self._stream_widget)
        body = Text("Zora: ", style=f"bold {ACCENT}")
        body.append(text)
        self._stream_widget.update(body)
        self.call_after_refresh(self._scroll_chat)

    def finish_stream(self, answer: str) -> None:
        if self._stream_widget is None:
            if answer:
                self._write_zora(answer)
            return
        self._stream_widget.update(
            Panel(
                Text(answer),
                title=Text(" Zora: ", style=f"bold {ACCENT} on #160a28"),
                title_align="left",
                border_style=ACCENT,
                style="on #160a28",
                padding=(0, 1),
            )
        )
        self._stream_widget = None
        self.call_after_refresh(self._scroll_chat)

    def write_diff(self, filename: str, patch: str) -> None:
        if not patch.strip():
            return
        self._mount(
            Panel(
                Syntax(patch, "diff", theme="monokai", word_wrap=True),
                title=Text(
                    f" diff · {filename} ", style=f"bold {MINT} on #0b2a24"
                ),
                title_align="left",
                border_style=MINT,
                style="on #0b2a24",
                padding=(0, 1),
            )
        )

    def _log(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    def _write_user(self, text: str) -> None:
        self._mount(
            Panel(
                Text(text),
                title=Text(" Talita: ", style=f"bold {CYAN} on #08222b"),
                title_align="left",
                border_style=CYAN,
                style="on #08222b",
                padding=(0, 1),
            )
        )

    def _write_zora(self, text: str) -> None:
        self._mount(
            Panel(
                Text(text),
                title=Text(" Zora: ", style=f"bold {ACCENT} on #160a28"),
                title_align="left",
                border_style=ACCENT,
                style="on #160a28",
                padding=(0, 1),
            )
        )

    def _write_note(self, text: str) -> None:
        self._mount(Text(text, style=MUTED))
