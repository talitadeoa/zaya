"""Operacoes seguras de leitura/escrita no Second Brain.

Regras:
  - nunca escreve fora da raiz configurada;
  - escrita/edicao/exclusao exigem `confirm=True` (alem da confirmacao na UI);
  - edicao e exclusao fazem backup em `.zaya/backups` / `.zaya/trash`;
  - links internos sao gravados no padrao `[[Nome da Nota]]`.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from zaya.config import SecondBrainConfig
from zaya.secondbrain.frontmatter import (
    dump_frontmatter,
    extract_links,
    extract_title,
    normalize_tags,
    parse_frontmatter,
)
from zaya.secondbrain.index import SearchHit, SecondBrainIndex


class ConfirmationRequired(RuntimeError):
    pass


class NoteConflict(RuntimeError):
    pass


@dataclass
class Note:
    path: Path
    rel_path: str
    title: str
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


class SecondBrain:
    def __init__(self, cfg: SecondBrainConfig, index: SecondBrainIndex):
        self.cfg = cfg
        self.index = index
        self.root = Path(cfg.path).expanduser().resolve()

    # ------------------------------------------------------------------- paths
    def ensure_root(self, create: bool = False) -> Path:
        if not self.root.exists():
            if create:
                self.root.mkdir(parents=True, exist_ok=True)
            else:
                raise FileNotFoundError(f"Second Brain nao encontrado em {self.root}")
        return self.root

    def relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root))

    def _safe_path(self, relative: str | Path) -> Path:
        candidate = (self.root / relative).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ValueError(f"caminho invalido: {relative}") from exc
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"caminho fora do Second Brain: {relative}")
        return resolved

    @staticmethod
    def _filename(title: str) -> str:
        name = re.sub(r'[\\/:*?"<>|]', "-", title).strip()
        name = re.sub(r"\s+", " ", name)
        name = name.strip(" .")
        return (name or "Sem titulo")[:120]

    def resolve(self, name: str) -> Path | None:
        raw = name.strip().strip("[]")
        if not raw:
            return None
        if raw.lower().endswith(".md"):
            candidate = self._safe_path(raw)
            if candidate.exists():
                return candidate
        if raw.startswith(("/", "~")):
            candidate = Path(raw).expanduser().resolve()
            if candidate.is_relative_to(self.root) and candidate.exists():
                return candidate
        candidate = self._safe_path(raw + ("" if raw.endswith(".md") else ".md"))
        if candidate.exists():
            return candidate
        hit = self.index.find_by_title(raw)
        if hit:
            return Path(hit.path)
        return None

    # -------------------------------------------------------------------- read
    def read_note(self, name: str | Path) -> Note:
        path = self.resolve(str(name))
        if not path:
            raise FileNotFoundError(f"nota nao encontrada: {name}")
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_frontmatter(text)
        title = extract_title(meta, body, path.name)
        return Note(
            path=path,
            rel_path=self.relative(path),
            title=title,
            meta=meta,
            body=body,
            tags=normalize_tags(meta, body),
            links=extract_links(body),
        )

    # ------------------------------------------------------------------- write
    def _confirm(self, confirm: bool) -> None:
        if self.cfg.require_confirmation and not confirm:
            raise ConfirmationRequired(
                "operacao destrutiva no Second Brain requer confirmacao explicita"
            )

    def _confirm_create(self, confirm: bool) -> None:
        if self.cfg.confirm_create and not confirm:
            raise ConfirmationRequired(
                "criacao de nota requer confirmacao (secondbrain.confirm_create=true)"
            )

    def _backup(self, path: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.root / ".zaya" / "backups" / stamp / self.relative(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target

    def _atomic_write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".zaya-tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def create_note(
        self,
        title: str,
        content: str,
        folder: str | None = None,
        tags: list[str] | None = None,
        links: list[str] | None = None,
        confirm: bool = False,
    ) -> Note:
        self._confirm_create(confirm)
        self.ensure_root(create=True)
        title = title.strip()
        if not title:
            raise ValueError("titulo vazio")

        folder = (folder or self.cfg.default_folder).strip().strip("/")
        target = self._safe_path(Path(folder) / f"{self._filename(title)}.md")
        if target.exists():
            raise NoteConflict(f"ja existe uma nota em {self.relative(target)}")

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        tags = [tag.lstrip("#") for tag in (tags or []) if tag.strip()]
        links = [link.strip() for link in (links or []) if link.strip()]
        meta: dict[str, Any] = {
            "title": title,
            "created": now,
            "updated": now,
            "tags": tags,
            "source": "zaya",
        }
        sections = [f"# {title}", "", content.strip()]
        if links:
            sections.extend(["", "## Relações", ""])
            sections.extend(f"- [[{link}]]" for link in links)
        text = dump_frontmatter(meta) + "\n" + "\n".join(sections).strip() + "\n"
        self._atomic_write(target, text)
        self.index.index_note(target)
        return self.read_note(target)

    def update_note(
        self,
        name: str,
        body: str,
        confirm: bool = False,
        tags: list[str] | None = None,
    ) -> Note:
        self._confirm(confirm)
        note = self.read_note(name)
        if self.cfg.backup_before_write:
            self._backup(note.path)
        meta = dict(note.meta)
        meta["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
        if tags is not None:
            meta["tags"] = [tag.lstrip("#") for tag in tags if tag.strip()]
        text = dump_frontmatter(meta) + "\n" + body.strip() + "\n"
        self._atomic_write(note.path, text)
        self.index.index_note(note.path)
        return self.read_note(note.path)

    def delete_note(self, name: str, confirm: bool = False) -> Path:
        self._confirm(confirm)
        note = self.read_note(name)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.root / ".zaya" / "trash" / stamp / note.rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(note.path), str(target))
        with self.index._lock, self.index._conn:
            self.index._conn.execute("DELETE FROM notes WHERE path = ?", (str(note.path),))
            if self.index._has_fts:
                self.index._conn.execute("DELETE FROM notes_fts WHERE path = ?", (str(note.path),))
        return target

    # ------------------------------------------------------------------ helpers
    def suggest_links(self, title: str, content: str, limit: int = 3) -> list[str]:
        query = f"{title} {content[:600]}"
        hits = self.index.search(query, limit=limit + 4)
        suggestions: list[str] = []
        seen: set[str] = set()
        for hit in hits:
            key = hit.title.casefold()
            if key == title.strip().casefold() or key in seen:
                continue
            seen.add(key)
            suggestions.append(hit.title)
            if len(suggestions) >= limit:
                break
        return suggestions

    def recent(self, limit: int = 10) -> list[SearchHit]:
        return self.index.recent(limit)

    def stats(self) -> dict[str, int]:
        return self.index.stats()

    # ------------------------------------------------------------- preferences
    @property
    def preferences_path(self) -> Path:
        if self.cfg.preferences_file:
            return Path(self.cfg.preferences_file).expanduser().resolve()
        return self.root / "preferences.md"

    def load_preferences(self, max_chars: int = 2500) -> str:
        path = self.preferences_path
        if not path.is_file():
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        _meta, body = parse_frontmatter(text)
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        body = re.sub(r"^#\s+.*\n+", "", body, count=1).strip()
        if len(body) > max_chars:
            cut = body[:max_chars]
            position = cut.rfind("\n")
            body = (cut[:position] if position > max_chars * 0.5 else cut).rstrip() + " ..."
        return body

    def append_preference(self, text: str, confirm: bool = False) -> bool:
        """Adiciona uma preferencia (taste) ao preferences.md. Idempotente."""
        clean = re.sub(r"\s+", " ", text).strip().lstrip("-* ").strip()
        if not clean:
            return False
        self._confirm_create(confirm)
        path = self.preferences_path
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone().isoformat(timespec="seconds")

        if path.is_file():
            current = path.read_text(encoding="utf-8", errors="replace")
            normalized = clean.casefold()
            existing = {
                line.strip().lstrip("-* ").strip().casefold()
                for line in current.splitlines()
                if line.strip().startswith(("-", "*"))
            }
            if normalized in existing:
                return False
            if self.cfg.backup_before_write:
                self._backup_any(path)
            meta, body = parse_frontmatter(current)
            meta["updated"] = now
            new_body = body.rstrip()
            marker = "## Aprendido com o tempo"
            if marker in new_body:
                new_body = new_body + f"\n- {clean}\n"
            else:
                new_body = new_body + f"\n\n{marker}\n\n- {clean}\n"
            self._atomic_write(path, dump_frontmatter(meta) + "\n" + new_body.lstrip("\n"))
        else:
            meta = {
                "title": "Preferências de interação",
                "created": now,
                "updated": now,
                "tags": ["preferencias", "zaya", "taste"],
                "source": "zaya",
            }
            body = f"# Preferências de interação\n\n## Aprendido com o tempo\n\n- {clean}\n"
            self._atomic_write(path, dump_frontmatter(meta) + "\n" + body)

        try:
            self.index.index_note(path)
        except ValueError:
            pass
        return True

    def _backup_any(self, path: Path) -> Path:
        try:
            return self._backup(path)
        except ValueError:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.root / ".zaya" / "backups" / stamp / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            return target
