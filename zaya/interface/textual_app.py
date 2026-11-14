"""Interface Textual (fase 1): layout premium com chat, entrada e status."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, ClassVar

from rich import box
from rich.markup import escape
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Input, Static

from zaya.audio.recorder import ContinuousListener
from zaya.controller.modes import PERSONAS, get_persona
from zaya.focus import moon_details, period_label, principal_phase_today
from zaya.interface.textual_markdown import CODE_THEME, MARKDOWN_THEME, markdown_body
from zaya.interface.textual_render import (
    _HELP,
    _SHORTCUTS,
    _STREAM_PREVIEW_CHARS,
    ACCENT,
    CYAN,
    LILAC,
    MINT,
    MINT_BG,
    MUTED,
    USER_BG,
    ZAYA_BG,
    _welcome_art,
    card,
)
from zaya.interface.textual_screens import (
    CommandPalette,
    ModePicker,
    PermissionScreen,
    SessionPicker,
)
from zaya.interface.textual_sink import TextualSink


class ModeBar(Static):
    """Barra do modo: clique abre o modal de escolha de modo."""

    async def on_click(self, event) -> None:
        event.stop()
        await self.app.run_action("modes")


class SessionButton(Static):
    """Atalho clicável para o modal de sessões."""

    def __init__(self) -> None:
        super().__init__("Sessões", id="session-button")

    async def on_click(self, event) -> None:
        event.stop()
        await self.app.run_action("sessions")


class CollapsibleCard(Static):
    """Card de painel: clique recolhe/expande o conteúdo."""

    def __init__(
        self, expanded_view, collapsed_view, *, start_expanded: bool = True, **kwargs
    ) -> None:
        super().__init__(
            expanded_view if start_expanded else collapsed_view, **kwargs
        )
        self._expanded_view = expanded_view
        self._collapsed_view = collapsed_view
        self.expanded = start_expanded

    def on_click(self, event) -> None:
        # Um arrasto para copiar texto não deve recolher o painel.
        if self.app.screen.selections:
            return
        event.stop()
        self.expanded = not self.expanded
        self.update(self._expanded_view if self.expanded else self._collapsed_view)


class MenuButton(Static):
    """Botão de atalhos no canto esquerdo da barra superior."""

    def __init__(self) -> None:
        super().__init__("⭘", id="menu-button")

    async def on_click(self, event) -> None:
        await self.app.run_action("command_palette")


class ZayaTextualApp(App):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("i", "insert", "Digitar"),
        Binding("enter", "insert", "Digitar"),
        Binding("t", "text_mode", "Texto"),
        Binding("m", "mute", "Mudo"),
        Binding("v", "toggle_voice", "Voz"),
        Binding("f", "push_to_talk", "Falar"),
        Binding("c", "toggle_listen", "Contínua"),
        Binding("escape", "escape", "Voltar"),
        Binding("tab", "modes", "Modo", priority=True),
        Binding("f1", "command_palette", "Comandos"),
        Binding("f2", "sessions", "Sessões"),
        Binding("f3", "modes", "Modo"),
        Binding("f4", "toggle_mouse", "Seleção"),
        Binding("f5", "toggle_dashboard", "Painéis"),
        Binding("ctrl+c", "copy_selection", "Copiar", show=False, priority=True),
        Binding("ctrl+l", "clear_chat", "Limpar"),
    ]

    CSS = """
    Screen {
        background: #0f1115;
        color: #e3e6eb;
    }
    #chat {
        border: none;
        padding: 1 1 0 1;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-color: #3a424f;
        scrollbar-background: #0f1115;
    }
    .card {
        margin-bottom: 1;
    }
    #topbar {
        height: 1;
        background: #12151b;
    }
    #menu-button {
        width: 3;
        height: 1;
        background: #12151b;
        color: #c9bce8;
        content-align: center middle;
    }
    #modebar {
        width: auto;
        height: 1;
        background: #12151b;
        color: #c9bce8;
        padding: 0;
    }
    #session-button {
        width: auto;
        height: 1;
        background: #12151b;
        color: #8fb0c9;
        padding: 0 1 0 1;
    }
    #status {
        height: 1;
        color: #8fbfae;
        padding: 0 1;
    }
    #commandbar {
        height: 1;
        background: #12151b;
        color: #7b8494;
        padding: 0 1;
    }
    #input {
        height: 3;
        margin: 0 1;
        border: round #262c37;
        background: #151922;
        color: #e3e6eb;
        padding: 0 1;
    }
    #input:focus {
        background: #1b202a;
        border: round #b3a1e0;
    }
    """

    def __init__(
        self,
        answer: Callable[[str, TextualSink], Awaitable[str]],
        cleanup: Callable[[], Awaitable[None]] | None = None,
        subtitle: str = "",
        voice: Any | None = None,
        start_selecting: bool = False,
    ):
        super().__init__()
        self._answer = answer
        self._cleanup = cleanup
        self._subtitle = subtitle
        self._voice = voice
        self._voice_replies = False
        self._continuous = False
        self._listener: ContinuousListener | None = None
        self._listen_task: asyncio.Task | None = None
        self._insert_mode = False
        self._selection_mode = bool(start_selecting)
        self.ALLOW_SELECT = self._selection_mode
        self._show_dashboard = True
        self._turn_running = False
        self._status_base = ""
        self._dot_phase = 0
        self._stream_widget: Static | None = None
        self._modebar: Static | None = None
        self._status_ref: Static | None = None
        self._input_ref: Input | None = None
        self._chat_ref: VerticalScroll | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(
            Horizontal(
                MenuButton(),
                ModeBar("", id="modebar"),
                SessionButton(),
                id="topbar",
            ),
            VerticalScroll(id="chat"),
            Static("", id="status"),
            Input(id="input", placeholder="fale ou digite…"),
        )
        yield Static(_SHORTCUTS, id="commandbar")

    async def on_mount(self) -> None:
        self.title = "Zaya"
        self.sub_title = self._subtitle
        # Tema do Rich Markdown (títulos, ênfase, código) no console do app:
        # widgets renderizam com este console, então as cores valem no chat.
        self.console.push_theme(MARKDOWN_THEME)
        self._modebar = self.query_one("#modebar", Static)
        self._status_ref = self.query_one("#status", Static)
        self._input_ref = self.query_one("#input", Input)
        self._chat_ref = self.query_one("#chat", VerticalScroll)
        await self._render_welcome()
        self._normal_mode()
        self._update_modebar()
        if self._selection_mode:
            self.call_after_refresh(self._start_with_selection)
        self.set_interval(0.4, self._tick_status)
        ui = getattr(self._voice, "ui", None)
        if ui is not None and hasattr(ui, "permission_callback"):
            ui.permission_callback = self.request_permission

    def request_permission(self, tool: str, resources: list[str]) -> str:
        """Chamado de uma thread pelo orchestrator (asyncio.to_thread)."""
        result = {"value": "reject"}
        done = threading.Event()

        def show() -> None:
            def finish(value: str | None) -> None:
                result["value"] = value or "reject"
                done.set()

            self.push_screen(PermissionScreen(tool, list(resources)), finish)

        try:
            self.call_from_thread(show)
        except Exception:
            return "reject"
        if not done.wait(timeout=300):
            return "reject"
        return result["value"]

    def _render_banner(self) -> None:
        width = 78
        try:
            width = max(40, min(96, self.size.width - 6))
        except Exception:
            pass
        banner = Text()
        art = _welcome_art(width)
        for index, segments in enumerate(art):
            for chunk, color in segments:
                if not chunk:
                    continue
                style = f"bold {color}" if chunk.strip() else ""
                banner.append(chunk, style=style)
            if index < len(art) - 1:
                banner.append("\n")
        self._mount(banner)

    async def _render_dashboard(self) -> None:
        if self._voice is None:
            return
        try:
            self._write_focus_panel()
            self._write_goals_table()
            await self._write_moon_note()
        except Exception:
            pass

    async def _render_welcome(self) -> None:
        self._render_banner()
        await self._render_dashboard()
        self.call_after_refresh(self._scroll_top)

    # ------------------------------------------------------ modos e sessoes
    def _mode_list(self) -> list:
        if self._voice is not None and hasattr(self._voice, "modes"):
            return self._voice.modes()
        return list(PERSONAS.values())

    def _mode_key(self) -> str:
        if self._voice is not None and hasattr(self._voice, "mode_key"):
            return self._voice.mode_key
        return "assistente"

    def _mode_name(self) -> str:
        if self._voice is not None and hasattr(self._voice, "mode_name"):
            return self._voice.mode_name
        persona = get_persona(self._mode_key())
        return persona.name if persona else "Zaya"

    def _update_modebar(self) -> None:
        if self._modebar is None:
            return
        name = self._mode_name()
        session = ""
        if self._voice is not None:
            session = getattr(self._voice, "session_id", "") or ""
        bar = Text()
        bar.append(name, style=f"bold {ACCENT}")
        bar.append("  ·  ", style=MUTED)
        if session:
            bar.append(f"sessão {session[:14]}", style=CYAN)
        else:
            bar.append("sem sessão", style=MUTED)
        bar.append("  ·  ", style=MUTED)
        self._modebar.update(bar)
        self.sub_title = f"{self._subtitle} · {name}" if self._subtitle else name

    def action_modes(self) -> None:
        """Abre o modal para a usuária escolher o modo (Tab/F3/clique)."""
        stack = self.screen_stack
        if stack and self.screen is not stack[0]:
            # Um modal já está aberto: Tab/F3 não devem empilhar outro.
            return
        if self._voice is None:
            self._set_status("modos indisponíveis")
            return
        self.push_screen(
            ModePicker(self._mode_list(), self._mode_key()),
            self._on_mode_chosen,
        )

    def _on_mode_chosen(self, key: str | None) -> None:
        if not key:
            self._update_modebar()
            return
        self._switch_mode(key)

    def _switch_mode(self, key: str) -> None:
        self.run_worker(self._apply_mode(key), group="modo", exclusive=True)

    def _handle_modo_command(self, text: str) -> None:
        argument = text.partition(" ")[2].strip()
        if not argument:
            self.action_modes()
            return
        persona = get_persona(argument)
        if persona is None:
            self._set_status(f"modo desconhecido: {argument}")
            return
        self._switch_mode(persona.key)

    async def _apply_mode(self, key: str) -> None:
        if self._voice is None:
            return
        try:
            await self._voice.select_mode(key)
        except Exception as exc:
            self._set_status(f"erro ao trocar modo: {exc}")
            return
        self._clear_messages()
        await self._render_history()
        self._update_modebar()

    def _clear_messages(self) -> None:
        """Remove só as mensagens, preservando banner e painéis do topo."""
        self._stream_widget = None
        try:
            for widget in list(self._chat().query(".msg")):
                widget.remove()
        except Exception:
            self._chat().remove_children()

    async def _render_history(self) -> None:
        if self._voice is None or not hasattr(self._voice, "history"):
            return
        turns = self._voice.history()
        if not turns:
            persona = get_persona(self._mode_key())
            label = persona.name if persona else "novo modo"
            self._mount(Text(f"{label} · sem mensagens ainda. Pode falar.", style=MUTED))
            return
        for role, text in turns[-60:]:
            if role == "user":
                self._write_user(text)
            else:
                self._write_zaya(text)

    def action_sessions(self) -> None:
        if self._voice is None or not hasattr(self._voice, "sessions"):
            self._set_status("sessões indisponíveis")
            return
        self.push_screen(
            SessionPicker(self._voice.sessions(), self._mode_name()),
            self._on_session_chosen,
        )

    def _on_session_chosen(self, target: object) -> None:
        if target is None:
            return
        if target == "new":
            self.run_worker(self._new_session_ui(), group="modo", exclusive=True)
            return
        if not isinstance(target, int):
            return
        self.run_worker(self._apply_session(target), group="modo", exclusive=True)

    async def _new_session_ui(self) -> None:
        if self._voice is None:
            return
        try:
            await self._voice.new_session()
        except Exception as exc:
            self._set_status(f"erro ao criar sessão: {exc}")
            return
        self._clear_messages()
        await self._render_history()
        self._update_modebar()

    async def _apply_session(self, conversation_id: int) -> None:
        if self._voice is None:
            return
        try:
            await self._voice.select_conversation(conversation_id)
        except Exception as exc:
            self._set_status(f"erro ao abrir sessão: {exc}")
            return
        self._clear_messages()
        await self._render_history()
        self._update_modebar()



    def _write_focus_panel(self) -> None:
        if self._voice is None:
            return
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
            card(
                f"✨ Foco {period_label(focus.period)}", body, CYAN, USER_BG
            )
        )

    def _write_goals_table(self) -> None:
        from rich.table import Table

        if self._voice is None:
            return
        goals = self._voice.goals.all()
        if not goals:
            return
        table = Table(
            box=box.SIMPLE_HEAD,
            border_style=ACCENT,
            header_style=f"bold {ACCENT}",
            expand=True,
        )
        table.add_column("Meta", style="bold", no_wrap=True)
        table.add_column("Próximo passo", style=MUTED, no_wrap=True)
        for goal in goals:
            table.add_row(escape(goal.label), escape(goal.next_step or "—"))
        available = max(60, self.size.width - 8)
        expanded = card("🎯 Metas  ▾", table, ACCENT, ZAYA_BG, max_width=available)
        collapsed = card("🎯 Metas  ▸", None, ACCENT, ZAYA_BG, max_width=available)
        self._mount_panel(expanded, collapsed)

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
            if self._voice is not None:
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
        expanded = card("🌙 Lua  ▾", body, LILAC, MINT_BG)
        collapsed = card("🌙 Lua  ▸", None, LILAC, MINT_BG)
        self._mount_panel(expanded, collapsed)

    async def _shutdown(self) -> None:
        # O Textual limpa o contextvar ``active_app`` antes do shutdown final.
        # Sem ele o logger interno cai no ``print`` e vaza mensagens de debug
        # no terminal ao sair (ex.: "focus was removed" / "Unmount() >>> ...").
        with self._context():
            await super()._shutdown()

    async def on_unmount(self) -> None:
        self._stop_listening()
        try:
            self.workers.cancel_all()
            await asyncio.sleep(0)
        except Exception:
            pass
        if self._cleanup is not None:
            try:
                await self._cleanup()
            except Exception:
                pass

    def on_key(self, event) -> None:
        stack = self.screen_stack
        if stack and self.screen is not stack[0]:
            return
        if not self._insert_mode and event.character == "/":
            event.stop()
            self.action_slash()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        # Inputs de telas modais (ex.: busca da paleta) não são mensagens.
        if getattr(event.input, "id", None) not in (None, "input"):
            return
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
        if text.startswith(("/chines", "/chinês", "/中文")):
            self._toggle_chinese(text)
            return
        if text == "/modo" or text.startswith("/modo "):
            self._handle_modo_command(text)
            return
        if text in {"/modos", "/perfil", "/persona"}:
            self.action_modes()
            return
        if text in {"/sessoes", "/sessões", "/sessao", "/sessão"}:
            self.action_sessions()
            return
        if text in {"/novo", "/nova"}:
            self.run_worker(self._new_session_ui(), group="modo", exclusive=True)
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

    async def _speak_and_resume(
        self, answer: str, listener: ContinuousListener | None
    ) -> None:
        if self._voice is None:
            return
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
        self._voice.mark_session_dirty()
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

    def _status_widget(self) -> Static | None:
        """Retorna o widget de status, ou None antes do mount/após o unmount."""
        if self._status_ref is not None:
            return self._status_ref
        try:
            return self.query_one("#status", Static)
        except (NoMatches, ScreenStackError):
            return None

    def _set_status(self, text: str) -> None:
        self._status_base = text.rstrip(".…").strip()
        widget = self._status_widget()
        if widget is not None:
            widget.update(text)

    def _tick_status(self) -> None:
        if not self._turn_running or not self._status_base:
            return
        base = self._status_base
        if not ("ndo" in base.lower() or base.startswith(("◐", "▶", "▸", "🎙"))):
            return
        widget = self._status_widget()
        if widget is None:
            return
        self._dot_phase = (self._dot_phase + 1) % 3
        dots = "." * (self._dot_phase + 1)
        widget.update(self._status_base + dots)

    # ------------------------------------------------------------------ modes
    def _normal_mode(self) -> None:
        self._insert_mode = False
        try:
            self.set_focus(None)
        except Exception:
            pass
        self._set_status("")

    def action_slash(self) -> None:
        self.action_insert()
        field = self._input_widget()
        if field is None:
            return
        field.value = "/"
        try:
            field.cursor_position = len(field.value)
        except Exception:
            pass

    def action_insert(self) -> None:
        self._insert_mode = True
        field = self._input_widget()
        if field is not None:
            field.focus()
        self._set_status("")

    async def action_escape(self) -> None:
        if self.screen.selections:
            self.clear_selection()
            if self._insert_mode:
                self._normal_mode()
            return
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
    def palette_entries(self) -> list[tuple[str, str, str]]:
        return [
            ("Ajuda", "?", "atalhos e comandos"),
            ("Trocar modo", "◈", "escolher a persona"),
            ("Sessões", "▤", "abrir o histórico"),
            ("Nova sessão", "+", "começar do zero"),
            ("Limpar conversa", "⌫", "limpar a tela"),
            ("Lua e metas on/off", "☾", "mostrar ou ocultar painéis"),
            ("Focar entrada", "▸", "ir para o campo de texto"),
            ("Chinês on/off", "中", "eco em chinês"),
            ("Reiniciar Zaya", "⟳", "recarregar o código"),
            ("Sair", "✕", "fechar a Zaya"),
        ]

    def palette_labels(self) -> list[str]:
        return [label for label, _, _ in self.palette_entries()]

    def action_command_palette(self) -> None:
        self.push_screen(CommandPalette(), self._run_palette_action)

    def _run_palette_action(self, label: str | None) -> None:
        if not label:
            return
        if label == "Ajuda":
            self._write_note(_HELP)
        elif label == "Trocar modo":
            self.action_modes()
        elif label == "Sessões":
            self.action_sessions()
        elif label == "Nova sessão":
            self.run_worker(self._new_session_ui(), group="modo", exclusive=True)
        elif label == "Limpar conversa":
            self.action_clear_chat()
        elif label == "Lua e metas on/off":
            self.action_toggle_dashboard()
        elif label == "Focar entrada":
            field = self._input_widget()
            if field is not None:
                field.focus()
        elif label == "Chinês on/off":
            self._toggle_chinese("")
        elif label == "Reiniciar Zaya":
            self.exit(42)
        elif label == "Sair":
            self.exit()

    def action_clear_chat(self) -> None:
        self._clear_messages()

    def action_toggle_dashboard(self) -> None:
        """Mostra ou oculta os painéis de lua e metas."""
        self._show_dashboard = not self._show_dashboard
        for widget in self._chat().query(".dashboard-panel"):
            widget.display = self._show_dashboard
        self._set_status(
            "lua e metas visíveis" if self._show_dashboard else "lua e metas ocultas"
        )

    def _start_with_selection(self) -> None:
        """Liga a seleção nativa de texto ao abrir (se configurada)."""
        self.ALLOW_SELECT = self._selection_mode
        if self._selection_mode:
            self._set_status(
                "seleção ligada: arraste no chat para copiar · Ctrl+C copia · F4 desliga"
            )

    def action_toggle_mouse(self) -> None:
        """Liga/desliga a seleção de texto com destaque."""
        self._selection_mode = not self._selection_mode
        self.ALLOW_SELECT = self._selection_mode
        if not self._selection_mode:
            self.clear_selection()
        if self._selection_mode:
            self._set_status(
                "seleção ligada: arraste no chat para copiar · Ctrl+C copia · F4 desliga"
            )
        else:
            self._set_status("seleção desligada: arraste não marca texto · F4 liga")

    # ---------------------------------------------------------------- seleção
    def on_text_selected(self) -> None:
        """Arrastar para selecionar já copia, sem precisar de Ctrl+C."""
        text = self.screen.get_selected_text()
        if not text:
            return
        self._copy_selection(text)

    def action_copy_selection(self) -> None:
        # Ctrl+C é prioritário para o pbcopy do Terminal.app; no campo de
        # texto ele ainda copia a seleção do próprio input.
        focused = self.focused
        if isinstance(focused, Input) and focused.selected_text:
            self._copy_selection(focused.selected_text)
            return
        text = self.screen.get_selected_text()
        if not text:
            if not self._insert_mode:
                self._set_status("nada selecionado: arraste sobre o texto do chat")
            return
        self._copy_selection(text)

    def _copy_selection(self, text: str) -> None:
        self._copy_text(text)
        self._set_status(f"copiado: {len(text)} caracteres")

    def _copy_text(self, text: str) -> None:
        try:
            self.copy_to_clipboard(text)
        except Exception:
            pass
        # OSC 52 não funciona no Terminal.app do macOS nem em vários terminais
        # do Linux; a ferramenta nativa cobre esses casos.
        command = self._clipboard_command()
        if not command:
            return
        try:
            subprocess.run(
                command, input=text.encode("utf-8"), timeout=3, check=False
            )
        except (OSError, subprocess.SubprocessError):
            pass

    @staticmethod
    def _clipboard_command() -> list[str] | None:
        if sys.platform == "darwin":
            return ["pbcopy"] if shutil.which("pbcopy") else None
        if sys.platform.startswith("linux"):
            for tool, args in (
                ("wl-copy", []),
                ("xclip", ["-selection", "clipboard"]),
                ("xsel", ["--clipboard", "--input"]),
            ):
                if shutil.which(tool):
                    return [tool, *args]
        return None

    # ------------------------------------------------------------------ helpers
    def _scroll_chat(self) -> None:
        try:
            self._chat().scroll_end(animate=False)
        except Exception:
            pass

    def _scroll_top(self) -> None:
        try:
            self._chat().scroll_home(animate=False)
        except Exception:
            pass

    def _mount(self, renderable, message: bool = False) -> None:
        classes = "card msg" if message else "card"
        self._chat().mount(Static(renderable, classes=classes))
        self.call_after_refresh(self._scroll_chat)

    def _dashboard_start_expanded(self) -> bool:
        cfg = getattr(self._voice, "cfg", None)
        interface = getattr(cfg, "interface", None)
        return not bool(getattr(interface, "dashboard_collapsed", True))

    def _mount_panel(self, expanded_view, collapsed_view) -> None:
        self._chat().mount(
            CollapsibleCard(
                expanded_view,
                collapsed_view,
                start_expanded=self._dashboard_start_expanded(),
                classes="card dashboard-panel",
            )
        )
        self.call_after_refresh(self._scroll_chat)

    def _markdown_enabled(self) -> bool:
        cfg = getattr(self._voice, "cfg", None)
        interface = getattr(cfg, "interface", None)
        return bool(getattr(interface, "markdown_render", True))

    def _body(self, text: str, *, streaming: bool = False):
        """Corpo da resposta: Markdown rico ou texto puro (configurável)."""
        if self._markdown_enabled():
            return markdown_body(text, streaming=streaming)
        if streaming and len(text) > _STREAM_PREVIEW_CHARS:
            text = "… " + text[-_STREAM_PREVIEW_CHARS:]
        return Text(text)

    def update_stream(self, text: str) -> None:
        if self._stream_widget is None:
            self._stream_widget = Static(classes="card msg")
            self._chat().mount(self._stream_widget)
        self._stream_widget.update(
            card("✦ Zaya", self._body(text, streaming=True), ACCENT, ZAYA_BG)
        )
        self.call_after_refresh(self._scroll_chat)

    def finish_stream(self, answer: str) -> None:
        if self._stream_widget is None:
            if answer:
                self._write_zaya(answer)
            return
        self._stream_widget.update(
            card("✦ Zaya", self._body(answer), ACCENT, ZAYA_BG)
        )
        self._stream_widget = None
        self.call_after_refresh(self._scroll_chat)
        if answer and self._voice is not None and self._voice.cfg.interface.chinese_echo:
            self.run_worker(self._echo_zh(answer), exclusive=False)

    def write_diff(self, filename: str, patch: str) -> None:
        if not patch.strip():
            return
        self._mount(
            card(
                f"diff · {filename}",
                Syntax(patch, "diff", theme=CODE_THEME, word_wrap=True),
                MINT,
                MINT_BG,
            ),
            message=True,
        )

    def _chat(self) -> VerticalScroll:
        if self._chat_ref is not None:
            return self._chat_ref
        return self.query_one("#chat", VerticalScroll)

    def _input_widget(self) -> Input | None:
        if self._input_ref is not None:
            return self._input_ref
        try:
            return self.query_one("#input", Input)
        except NoMatches:
            return None

    def _write_user(self, text: str) -> None:
        self._mount(card("❯ Talita", Text(text), CYAN, USER_BG), message=True)  # noqa: RUF001
        if self._voice is not None and self._voice.cfg.interface.chinese_echo:
            self.run_worker(self._echo_zh(text), exclusive=False)

    def _write_zaya(self, text: str) -> None:
        self._mount(card("✦ Zaya", self._body(text), ACCENT, ZAYA_BG), message=True)

    async def _echo_zh(self, text: str) -> None:
        if self._voice is None:
            return
        try:
            translated = await self._voice.translate_zh(text)
        except Exception:
            return
        if translated:
            self._mount(Text(f"中文: {translated}", style=MUTED))

    def _toggle_chinese(self, argument: str) -> None:
        if self._voice is None:
            self._set_status("chinês indisponível")
            return
        parts = argument.split(maxsplit=1)
        current = self._voice.cfg.interface.chinese_echo
        if len(parts) > 1 and parts[1].lower() in {"on", "off"}:
            current = parts[1].lower() == "on"
        else:
            current = not current
        self._voice.cfg.interface.chinese_echo = current
        self._set_status("chinês ligado" if current else "chinês desativado")

    def _write_note(self, text: str) -> None:
        self._mount(Text(text, style=MUTED))
