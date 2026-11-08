"""Interface Textual (fase 1): layout premium com chat, entrada e status."""

from __future__ import annotations

from typing import Awaitable, Callable

from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

ACCENT = "magenta"
MUTED = "grey62"

_HELP = (
    "Comandos nesta interface: /sair, /ajuda. "
    "Os demais comandos (agenda, tarefas, metas, finanças) continuam no modo terminal."
)


class ZoraTextualApp(App):
    CSS = """
    Screen {
        background: #0b0614;
        color: #e5e7eb;
    }
    #chat {
        border: round #a855f7;
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
        answer: Callable[[str], Awaitable[str]],
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
        status.update("◐ pensando…")
        try:
            answer = await self._answer(text)
        except Exception as exc:  # pragma: no cover - depende do backend
            answer = f"Erro: {exc}"
        if answer:
            self._write_zora(answer)
        status.update("● pronto")

    # ------------------------------------------------------------------ helpers
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
