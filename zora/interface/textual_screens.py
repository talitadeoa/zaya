"""Telas modais da interface Textual (paleta e sessões)."""

from __future__ import annotations

from typing import ClassVar, Protocol, cast

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static


class _PaletteHost(Protocol):
    def palette_labels(self) -> list[str]: ...


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

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Voltar")]

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
        for label in cast(_PaletteHost, self.app).palette_labels():
            if needle in label.lower():
                options.add_option(label)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.prompt))


class SessionPicker(ModalScreen[object]):
    """Lista as sessões (histórico) do modo ativo, como no OpenCode."""

    CSS = """
    SessionPicker {
        align: center middle;
        background: #000000cc;
    }
    #session-box {
        width: 84;
        height: auto;
        max-height: 26;
        border: round #79DCFD;
        background: #0b1a22;
        padding: 1 2;
    }
    #session-title {
        color: #79DCFD;
        text-style: bold;
        margin-bottom: 1;
    }
    #session-hint {
        color: #8a8a99;
        margin-top: 1;
    }
    #session-list {
        border: none;
        background: #0b1a22;
        padding: 0;
    }
    #session-list > .option-list--option-highlighted {
        background: #79DCFD;
        color: #041019;
        text-style: bold;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Voltar")]

    def __init__(self, sessions: list[dict], mode_name: str):
        super().__init__()
        self._sessions = list(sessions)
        self._mode_name = mode_name
        self._targets: list[object] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="session-box"):
            yield Static(f"Sessões · {self._mode_name}", id="session-title")
            yield OptionList(id="session-list")
            yield Static("↑↓ escolher · Enter abrir · Esc voltar", id="session-hint")

    def on_mount(self) -> None:
        options = self.query_one("#session-list", OptionList)
        options.clear_options()
        self._targets.clear()
        options.add_option("+ Nova sessão")
        self._targets.append("new")
        highlighted = 0
        for session in self._sessions:
            when = str(session.get("updated_at", ""))[:16].replace("T", " ")
            mark = "●" if session.get("current") else "○"
            label = (
                f"{mark} {session.get('title', 'Sem título')}"
                f"  ·  {session.get('turns', 0)} msg  ·  {when}"
            )
            options.add_option(label)
            self._targets.append(int(session["id"]))
            if session.get("current"):
                highlighted = len(self._targets) - 1
        options.highlighted = highlighted
        options.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._targets[event.option_index])
