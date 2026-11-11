"""Telas modais da interface Textual (paleta, modos e sessões)."""

from __future__ import annotations

from typing import ClassVar, Protocol, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option as OptionItem


class _PaletteHost(Protocol):
    def palette_entries(self) -> list[tuple[str, str, str]]: ...


class CommandPalette(ModalScreen[str | None]):
    CSS = """
    CommandPalette {
        align: center middle;
        background: #05070add;
    }
    #palette {
        width: 72;
        height: auto;
        max-height: 24;
        border: round #b3a1e0;
        background: #151922;
        padding: 1 2;
    }
    #palette-title {
        color: #b3a1e0;
        text-style: bold;
    }
    #palette-subtitle {
        color: #7b8494;
        margin-bottom: 1;
    }
    #palette-input {
        border: round #2a3140;
        background: #10141b;
        padding: 0 1;
        margin-bottom: 1;
    }
    #palette-input:focus {
        border: round #b3a1e0;
        background: #12161f;
    }
    #palette-list {
        border: none;
        background: #151922;
        height: auto;
        max-height: 14;
        padding: 0;
    }
    #palette-list > .option-list--option {
        padding: 0 1;
    }
    #palette-list > .option-list--option-highlighted {
        background: #b3a1e0;
        color: #0f1115;
        text-style: bold;
    }
    #palette-hint {
        color: #7b8494;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Voltar")]

    def __init__(self) -> None:
        super().__init__()
        self._labels: list[str] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Comandos", id="palette-title"),
            Static("busque e execute sem sair do teclado", id="palette-subtitle"),
            Input(placeholder="Buscar comando…", id="palette-input"),
            OptionList(id="palette-list"),
            Static("↑↓ escolher · Enter executar · Esc fechar", id="palette-hint"),
            id="palette",
        )

    def on_mount(self) -> None:
        self._refresh("")
        self.query_one("#palette-input", Input).focus()

    def _refresh(self, query: str) -> None:
        options = self.query_one("#palette-list", OptionList)
        options.clear_options()
        self._labels.clear()
        needle = query.lower().strip()
        for label, icon, description in cast(_PaletteHost, self.app).palette_entries():
            haystack = f"{label} {description}".lower()
            if needle and needle not in haystack:
                continue
            prompt = Text()
            prompt.append(f"{icon}  ", style="bold")
            prompt.append(label, style="bold")
            prompt.append(f"   {description}")
            options.add_option(prompt)
            self._labels.append(label)
        if not self._labels:
            options.add_option(
                OptionItem(Text("nenhum comando encontrado", style="italic"), disabled=True)
            )
        options.highlighted = 0 if self._labels else None

    def on_key(self, event) -> None:
        """Seta ↑/↓ movem o destaque mesmo com o foco no campo de busca."""
        if event.key not in ("up", "down") or not self._labels:
            return
        options = self.query_one("#palette-list", OptionList)
        current = options.highlighted if options.highlighted is not None else -1
        candidate = current + (1 if event.key == "down" else -1)
        options.highlighted = max(0, min(len(self._labels) - 1, candidate))
        event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "palette-input":
            return
        event.stop()
        self._select_highlighted()

    def _select_highlighted(self) -> None:
        options = self.query_one("#palette-list", OptionList)
        index = options.highlighted
        if index is None or index >= len(self._labels):
            return
        self.dismiss(self._labels[index])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_index >= len(self._labels):
            return
        self.dismiss(self._labels[event.option_index])


class ModePicker(ModalScreen[str | None]):
    """Escolha de um dos modos (personas) da Zaya."""

    CSS = """
    ModePicker {
        align: center middle;
        background: #05070acc;
    }
    #mode-box {
        width: 76;
        height: auto;
        max-height: 24;
        border: round #b3a1e0;
        background: #151922;
        padding: 1 2;
    }
    #mode-title {
        color: #b3a1e0;
        text-style: bold;
        margin-bottom: 1;
    }
    #mode-hint {
        color: #7b8494;
        margin-top: 1;
    }
    #mode-list {
        border: none;
        background: #151922;
        padding: 0;
    }
    #mode-list > .option-list--option-highlighted {
        background: #b3a1e0;
        color: #0f1115;
        text-style: bold;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Voltar")]

    def __init__(self, modes: list, current: str):
        super().__init__()
        self._modes = list(modes)
        self._current = current
        self._keys: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="mode-box"):
            yield Static("Escolha um modo", id="mode-title")
            yield OptionList(id="mode-list")
            yield Static("↑↓ escolher · Enter confirmar · Esc manter", id="mode-hint")

    def on_mount(self) -> None:
        options = self.query_one("#mode-list", OptionList)
        options.clear_options()
        self._keys.clear()
        highlighted = 0
        for index, persona in enumerate(self._modes):
            self._keys.append(persona.key)
            mark = "●" if persona.key == self._current else "○"
            options.add_option(f"{mark} {persona.name}  —  {persona.summary}")
            if persona.key == self._current:
                highlighted = index
        options.highlighted = highlighted
        options.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._keys[event.option_index])


class SessionPicker(ModalScreen[object]):
    """Lista as sessões (histórico) do modo ativo."""

    CSS = """
    SessionPicker {
        align: center middle;
        background: #05070add;
    }
    #session-box {
        width: 84;
        height: auto;
        max-height: 26;
        border: round #8fb0c9;
        background: #12171f;
        padding: 1 2;
    }
    #session-title {
        color: #8fb0c9;
        text-style: bold;
        margin-bottom: 1;
    }
    #session-hint {
        color: #7b8494;
        margin-top: 1;
    }
    #session-list {
        border: none;
        background: #12171f;
        padding: 0;
    }
    #session-list > .option-list--option-highlighted {
        background: #8fb0c9;
        color: #0f1115;
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


class PermissionScreen(ModalScreen[str]):
    """Confirma uma acao destrutiva do agente (escrita, shell)."""

    CSS = """
    PermissionScreen {
        align: center middle;
        background: #05070acc;
    }
    #permission-box {
        width: 72;
        height: auto;
        border: round #e0b341;
        background: #151922;
        padding: 1 2;
    }
    #permission-title {
        color: #e0b341;
        text-style: bold;
        margin-bottom: 1;
    }
    #permission-detail {
        color: #e3e6eb;
        margin-bottom: 1;
    }
    #permission-hint {
        color: #7b8494;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("o", "once", "Uma vez"),
        Binding("a", "always", "Sempre"),
        Binding("r", "reject", "Rejeitar"),
        Binding("escape", "reject", "Rejeitar"),
    ]

    def __init__(self, tool: str, resources: list[str]):
        super().__init__()
        self._tool = tool
        self._resources = list(resources)

    def compose(self) -> ComposeResult:
        with Vertical(id="permission-box"):
            yield Static(f"O agente quer usar: {self._tool}", id="permission-title")
            if self._resources:
                yield Static("alvo: " + ", ".join(self._resources), id="permission-detail")
            yield Static(
                "o = uma vez · a = sempre · r = rejeitar", id="permission-hint"
            )

    def action_once(self) -> None:
        self.dismiss("once")

    def action_always(self) -> None:
        self.dismiss("always")

    def action_reject(self) -> None:
        self.dismiss("reject")
