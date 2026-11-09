"""Registro de notas e preferencias no Second Brain."""

from __future__ import annotations

import asyncio

from zora.controller.contract import OrchestratorContract
from zora.controller.intents import RegisterIntent
from zora.secondbrain.notes import NoteConflict


class RegistrationMixin(OrchestratorContract):
    def _preferences_block(self) -> str:
        path = self.brain.preferences_path
        try:
            mtime = path.stat().st_mtime if path.is_file() else -1.0
        except OSError:
            mtime = -1.0
        if mtime != self._prefs_mtime:
            self._prefs_cache = self.brain.load_preferences()
            self._prefs_mtime = mtime
        if not self._prefs_cache:
            return ""
        return (
            "[Preferências de interação do usuário — siga sempre, "
            "sem mencionar este bloco]\n" + self._prefs_cache
        )

    async def _handle_preference(self, text: str) -> None:
        added = await asyncio.to_thread(self.brain.append_preference, text, False)
        if added:
            self._prefs_mtime = -1.0
            self.ui.success(f"preferência aprendida: {text}")
        else:
            self.ui.info("essa preferência já estava registrada.")

    async def _handle_register(self, intent: RegisterIntent, original: str) -> None:
        assert self.conversation_id is not None
        content = intent.content.strip()
        if not content:
            content = await self._fallback_register_content()
            if not content:
                self.ui.warning("não encontrei conteúdo para registrar.")
                return
            intent.content = content
        title = intent.title or "Nota sem titulo"
        links = await asyncio.to_thread(self.brain.suggest_links, title, content, 3)
        folder = intent.folder or self.cfg.secondbrain.default_folder
        self.ui.note_preview(title, intent.tags, links, content, folder)
        if self.cfg.secondbrain.confirm_create:
            ok = await asyncio.to_thread(self.ui.confirm, "Criar esta nota no Second Brain?", False)
            if not ok:
                self.ui.info("operação cancelada; nenhuma nota foi criada.")
                return
        try:
            note = await asyncio.to_thread(
                self.brain.create_note,
                title,
                content,
                folder,
                intent.tags,
                links,
                True,
            )
        except NoteConflict as exc:
            self.ui.warning(str(exc))
            return
        except Exception as exc:
            self.ui.error(f"falha ao criar nota: {exc}")
            return

        self.store.record_note(self.conversation_id, intent.kind, str(note.path), note.title)
        self.ui.success(f"nota criada: {note.rel_path}")
        await self._speak(f"Nota criada: {note.title}")

    async def _fallback_register_content(self) -> str:
        assert self.conversation_id is not None
        for turn in reversed(self.store.turns(self.conversation_id, limit=6)):
            if turn["role"] == "assistant":
                return str(turn["text"])
        return ""
