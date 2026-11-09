"""Interface Textual (fase 1): layout premium com chat, entrada e status."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, ClassVar

from rich import box
from rich.markup import escape
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Input, Static

from zora.audio.recorder import ContinuousListener
from zora.controller.modes import PERSONAS, get_persona
from zora.focus import moon_details, period_label, principal_phase_today
from zora.interface.textual_render import (
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
    ZORA_BG,
    _welcome_art,
    card,
)
from zora.interface.textual_screens import CommandPalette, ModePicker, SessionPicker
from zora.interface.textual_sink import TextualSink


class ZoraTextualApp(App):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("i", "insert", "Digitar"),
        Binding("enter", "insert", "Digitar"),
        Binding("t", "text_mode", "Texto"),
        Binding("m", "mute", "Mudo"),
        Binding("v", "toggle_voice", "Voz"),
        Binding("f", "push_to_talk", "Falar"),
        Binding("c", "toggle_listen", "Contínua"),
        Binding("escape", "escape", "Voltar", priority=True),
        Binding("f1", "command_palette", "Comandos"),
        Binding("f2", "sessions", "Sessões"),
        Binding("f3", "modes", "Modo"),
        Binding("ctrl+t", "toggle_theme", "Tema"),
        Binding("ctrl+l", "clear_chat", "Limpar"),
    ]

    CSS = """
    Screen {
        background: #0b0614;
        color: #e5e7eb;
    }
    #chat {
        border: none;
        padding: 0 1;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-color: #79DCFD;
        scrollbar-background: #140a24;
    }
    .card {
        margin-bottom: 1;
    }
    #modebar {
        height: 1;
        background: #140a24;
        color: #e6c3fd;
        padding: 0 1;
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
    #input-frame {
        height: 5;
        margin-bottom: 1;
        background: #1a1230;
    }
    #input-rail {
        width: 1;
        height: 1fr;
        background: #79DCFD;
    }
    #input {
        border: none;
        background: #1a1230;
        color: #e5e7eb;
        height: 1fr;
    }
    #input:focus {
        background: #241a3d;
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
        self._listener: ContinuousListener | None = None
        self._listen_task: asyncio.Task | None = None
        self._insert_mode = False
        self._turn_running = False
        self._status_base = ""
        self._dot_phase = 0
        self._stream_widget: Static | None = None
        self._modebar: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Vertical(
            Static("", id="modebar"),
            VerticalScroll(id="chat"),
            Static("", id="status"),
            Horizontal(
                Static("", id="input-rail"),
                Input(id="input"),
                id="input-frame",
            ),
        )
        yield Static(_SHORTCUTS, id="commandbar")

    async def on_mount(self) -> None:
        self.title = "Zora"
        self.sub_title = self._subtitle
        self._modebar = self.query_one("#modebar", Static)
        await self._render_welcome()
        self._normal_mode()
        self._update_modebar()
        self.set_interval(0.4, self._tick_status)
        self.call_after_refresh(self._prompt_mode)

    def _render_banner(self) -> None:
        width = 78
        try:
            width = max(40, min(96, self.size.width - 6))
        except Exception:
            pass
        art = _welcome_art(width)
        banner = Text()
        for index, (line, color) in enumerate(art):
            style = f"bold {color}" if line.strip() else ""
            banner.append(line, style=style)
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
        self._mount(Text("assistente pessoal · OpenCode · Second Brain", style=MUTED))
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
        return persona.name if persona else "Zora"

    def _update_modebar(self) -> None:
        if self._modebar is None:
            return
        name = self._mode_name()
        session = ""
        if self._voice is not None:
            session = getattr(self._voice, "session_id", "") or ""
        bar = Text()
        bar.append("▌ ", style=f"bold {ACCENT}")
        bar.append(name, style=f"bold {ACCENT}")
        bar.append("  ·  ", style=MUTED)
        if session:
            bar.append(f"sessão {session[:14]}", style=CYAN)
        else:
            bar.append("sem sessão", style=MUTED)
        bar.append("  ·  ", style=MUTED)
        bar.append("F2 sessões · F3 modo", style=MUTED)
        self._modebar.update(bar)
        self.sub_title = f"{self._subtitle} · {name}" if self._subtitle else name

    def _prompt_mode(self) -> None:
        if self._voice is None:
            return
        self.push_screen(ModePicker(self._mode_list(), self._mode_key()), self._on_mode_chosen)

    def _on_mode_chosen(self, key: str | None) -> None:
        if not key:
            self._update_modebar()
            return
        self.run_worker(self._apply_mode(key), exclusive=False)

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
                self._write_zora(text)

    def action_modes(self) -> None:
        self._prompt_mode()

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
            self.run_worker(self._new_session_ui(), exclusive=False)
            return
        if not isinstance(target, int):
            return
        self.run_worker(self._apply_session(target), exclusive=False)

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
        table.add_column("Meta", style="bold")
        table.add_column("Próximo passo", style=MUTED)
        for goal in goals:
            table.add_row(escape(goal.label), escape(goal.next_step or "—"))
        self._mount(card("🎯 Metas", table, ACCENT, ZORA_BG))

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
        self._mount(card("🌙 Lua", body, LILAC, MINT_BG))

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
        if not self._insert_mode and event.character == "/":
            event.stop()
            self.action_slash()

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
        if text.startswith(("/chines", "/chinês", "/中文")):
            self._toggle_chinese(text)
            return
        if text in {"/modo", "/modos", "/perfil", "/persona"}:
            self._prompt_mode()
            return
        if text in {"/sessoes", "/sessões", "/sessao", "/sessão"}:
            self.action_sessions()
            return
        if text in {"/novo", "/nova"}:
            self.run_worker(self._new_session_ui(), exclusive=False)
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

    def action_slash(self) -> None:
        self.action_insert()
        field = self.query_one("#input", Input)
        field.value = "/"
        try:
            field.cursor_position = len(field.value)
        except Exception:
            pass

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
            "Trocar modo",
            "Sessões",
            "Nova sessão",
            "Alternar tema",
            "Limpar conversa",
            "Focar entrada",
            "Chinês on/off",
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
        elif label == "Trocar modo":
            self._prompt_mode()
        elif label == "Sessões":
            self.action_sessions()
        elif label == "Nova sessão":
            self.run_worker(self._new_session_ui(), exclusive=False)
        elif label == "Alternar tema":
            self.action_toggle_theme()
        elif label == "Limpar conversa":
            self.action_clear_chat()
        elif label == "Focar entrada":
            self.query_one("#input", Input).focus()
        elif label == "Chinês on/off":
            self._toggle_chinese("")
        elif label == "Reiniciar Zora":
            self.exit(42)
        elif label == "Sair":
            self.exit()

    def action_toggle_theme(self) -> None:
        current = str(getattr(self, "theme", ""))
        self.theme = "textual-light" if "dark" in current else "textual-dark"

    def action_clear_chat(self) -> None:
        self._clear_messages()

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

    def update_stream(self, text: str) -> None:
        if self._stream_widget is None:
            self._stream_widget = Static(classes="card msg")
            self._chat().mount(self._stream_widget)
        preview = text
        if len(preview) > _STREAM_PREVIEW_CHARS:
            preview = "… " + preview[-_STREAM_PREVIEW_CHARS:]
        self._stream_widget.update(
            card("✦ Zora", Text(preview), ACCENT, ZORA_BG)
        )
        self.call_after_refresh(self._scroll_chat)

    def finish_stream(self, answer: str) -> None:
        if self._stream_widget is None:
            if answer:
                self._write_zora(answer)
            return
        self._stream_widget.update(
            card("✦ Zora", Text(answer), ACCENT, ZORA_BG)
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
                Syntax(patch, "diff", theme="monokai", word_wrap=True),
                MINT,
                MINT_BG,
            ),
            message=True,
        )

    def _chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    def _write_user(self, text: str) -> None:
        self._mount(card("❯ Talita", Text(text), CYAN, USER_BG), message=True)  # noqa: RUF001
        if self._voice is not None and self._voice.cfg.interface.chinese_echo:
            self.run_worker(self._echo_zh(text), exclusive=False)

    def _write_zora(self, text: str) -> None:
        self._mount(card("✦ Zora", Text(text), ACCENT, ZORA_BG), message=True)

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
