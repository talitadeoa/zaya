"""Interface Textual (fase 1): layout premium com chat, entrada e status."""

from __future__ import annotations

from typing import Awaitable, Callable

from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, OptionList, RichLog, Static

ACCENT = "magenta"
MUTED = "grey62"

_HELP = (
    "Comandos nesta interface: /sair, /ajuda. "
    "Atalhos: F1 abre a paleta de comandos, Ctrl+T alterna o tema, Ctrl+L limpa a conversa. "
    "Os demais comandos (agenda, tarefas, metas, finanças) continuam no modo terminal."
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
        border: round #a855f7;
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

    def token(self, text: str) -> None:
        self.buffer += text
        self.app.update_stream(self.buffer)

    def reasoning(self, text: str) -> None:
        snippet = " ".join((text or "").split())[-90:]
        if snippet:
            self.app.query_one("#status", Static).update(f"◐ {snippet}")

    def tool(self, name: str, detail: str = "") -> None:
        line = f"▸ {name} {detail}".strip()
        self.app.query_one("#status", Static).update(line)

    def diff(self, filename: str, patch: str) -> None:
        self.app.write_diff(filename, patch)


class ZoraTextualApp(App):
    BINDINGS = [
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
        border: round #a855f7;
        padding: 0 1;
    }
    #stream {
        height: auto;
        max-height: 20;
        padding: 0 1;
    }
    #status {
        height: 1;
        color: #9ca3af;
        padding: 0 1;
    }
    #input {
        dock: bottom;
        border: round #22d3ee;
    }
    """

    def __init__(
        self,
        answer: Callable[[str, TextualSink], Awaitable[str]],
        cleanup: Callable[[], Awaitable[None]] | None = None,
        subtitle: str = "",
    ):
        super().__init__()
        self._answer = answer
        self._cleanup = cleanup
        self._subtitle = subtitle

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Vertical(
            RichLog(id="chat", highlight=True, markup=True, wrap=True),
            Static("", id="stream"),
            Static("● pronto", id="status"),
        )
        yield Input(placeholder="Digite uma mensagem ou /ajuda", id="input")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "Zora"
        self.sub_title = self._subtitle
        self._write_zora(
            "Oi, Talita. Esta é a interface nova (fase 1). "
            "Digite uma mensagem ou use /ajuda."
        )
        self.query_one("#input", Input).focus()

    async def on_unmount(self) -> None:
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
        if text in {"/", "/ajuda", "/help"}:
            self._write_note(_HELP)
            return

        self._write_user(text)
        status = self.query_one("#status", Static)
        stream = self.query_one("#stream", Static)
        stream.update("")
        status.update("◐ pensando…")
        sink = TextualSink(self)
        try:
            answer = await self._answer(text, sink)
        except Exception as exc:  # pragma: no cover - depende do backend
            answer = f"Erro: {exc}"
        stream.update("")
        if answer:
            self._write_zora(answer)
        elif sink.buffer:
            self._write_zora(sink.buffer)
        status.update("● pronto")

    # --------------------------------------------------------------- palette
    def palette_labels(self) -> list[str]:
        return [
            "Ajuda",
            "Alternar tema",
            "Limpar conversa",
            "Focar entrada",
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
        elif label == "Sair":
            self.exit()

    def action_toggle_theme(self) -> None:
        self.theme = "textual-light" if "dark" in self.theme else "textual-dark"

    def action_clear_chat(self) -> None:
        self.query_one("#chat", RichLog).clear()

    # ------------------------------------------------------------------ helpers
    def update_stream(self, text: str) -> None:
        self.query_one("#stream", Static).update(
            Panel(
                Text(text),
                title=Text("Zora:", style=f"bold {ACCENT}"),
                title_align="left",
                border_style=ACCENT,
                padding=(0, 1),
            )
        )

    def write_diff(self, filename: str, patch: str) -> None:
        if not patch.strip():
            return
        self._log().write(
            Panel(
                Syntax(patch, "diff", theme="monokai", word_wrap=True),
                title=f"diff · {filename}",
                title_align="left",
                border_style="green",
                padding=(0, 1),
            )
        )

    def _log(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    def _write_user(self, text: str) -> None:
        self._log().write(
            Panel(
                Text(text),
                title=Text("Talita:", style=f"bold {ACCENT}"),
                title_align="left",
                border_style=ACCENT,
                padding=(0, 1),
            )
        )

    def _write_zora(self, text: str) -> None:
        self._log().write(
            Panel(
                Text(text),
                title=Text("Zora:", style=f"bold {ACCENT}"),
                title_align="left",
                border_style=ACCENT,
                padding=(0, 1),
            )
        )

    def _write_note(self, text: str) -> None:
        self._log().write(Text(text, style=MUTED))
