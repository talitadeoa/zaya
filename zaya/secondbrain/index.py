"""Indice local do Second Brain: SQLite + FTS5, incremental e offline."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from zaya.config import SecondBrainConfig
from zaya.secondbrain.frontmatter import (
    extract_links,
    extract_title,
    normalize_tags,
    parse_frontmatter,
)


@dataclass
class SearchHit:
    path: str
    rel_path: str
    title: str
    snippet: str
    tags: list[str] = field(default_factory=list)
    score: float = 0.0
    section: str = ""

    def as_context(self, max_chars: int = 1200) -> str:
        snippet = self.snippet.strip()
        if len(snippet) > max_chars:
            snippet = snippet[:max_chars].rstrip() + "..."
        label = self.title
        if self.section and self.section.casefold() not in {
            "introdução",
            "introducao",
            "conteúdo",
            "conteudo",
        }:
            label = f"{self.title} — {self.section}"
        return f"- **{label}** (`{self.rel_path}`): {snippet}"


class SecondBrainIndex:
    def __init__(self, cfg: SecondBrainConfig, index_path: Path):
        self.cfg = cfg
        self.root = Path(cfg.path).expanduser().resolve()
        self.index_path = Path(index_path).expanduser().resolve()
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.index_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._has_fts = True
        self._has_chunks = True
        self._init_schema()

    # ----------------------------------------------------------------- schema
    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    path TEXT PRIMARY KEY,
                    rel_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    tags TEXT NOT NULL DEFAULT '',
                    links TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )
            try:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                        path UNINDEXED,
                        title,
                        body,
                        tags,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                    """
                )
            except sqlite3.OperationalError:
                self._has_fts = False

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    section TEXT NOT NULL,
                    content TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    mtime REAL NOT NULL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
            try:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                        path UNINDEXED,
                        title,
                        section,
                        content,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                    """
                )
            except sqlite3.OperationalError:
                self._has_chunks = False

    # ------------------------------------------------------------------ files
    def _iter_markdown(self):
        if not self.root.exists():
            return
        excluded = {name.casefold() for name in self.cfg.exclude}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [
                d for d in dirnames if d.casefold() not in excluded and not d.startswith(".")
            ]
            for filename in filenames:
                if not filename.lower().endswith(".md"):
                    continue
                yield Path(dirpath) / filename

    def _record(self, path: Path) -> dict | None:
        try:
            stat = path.stat()
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        meta, body = parse_frontmatter(text)
        title = extract_title(meta, body, path.name)
        tags = normalize_tags(meta, body)
        links = extract_links(body)
        rel = str(path.resolve().relative_to(self.root))
        return {
            "path": str(path.resolve()),
            "rel_path": rel,
            "title": title,
            "mtime": stat.st_mtime,
            "size": stat.st_size,
            "tags": ",".join(tags),
            "links": ",".join(links),
            "body": body,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def index_note(self, path: str | Path) -> bool:
        path = Path(path).expanduser().resolve()
        record = self._record(path)
        if record is None:
            return False
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO notes (path, rel_path, title, mtime, size, tags, links, body, updated_at)
                VALUES (:path, :rel_path, :title, :mtime, :size, :tags, :links, :body, :updated_at)
                ON CONFLICT(path) DO UPDATE SET
                    rel_path=excluded.rel_path,
                    title=excluded.title,
                    mtime=excluded.mtime,
                    size=excluded.size,
                    tags=excluded.tags,
                    links=excluded.links,
                    body=excluded.body,
                    updated_at=excluded.updated_at
                """,
                record,
            )
            if self._has_fts:
                self._conn.execute("DELETE FROM notes_fts WHERE path = ?", (record["path"],))
                self._conn.execute(
                    "INSERT INTO notes_fts (path, title, body, tags) VALUES (?, ?, ?, ?)",
                    (record["path"], record["title"], record["body"], record["tags"]),
                )
            self._index_chunks(record)
        return True

    def _index_chunks(self, record: dict) -> None:
        if not self._has_chunks:
            return
        path = record["path"]
        self._conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
        self._conn.execute("DELETE FROM chunks_fts WHERE path = ?", (path,))
        position = 0
        for section, content in _split_sections(record["body"]):
            for piece in _chunk_text(content):
                cursor = self._conn.execute(
                    """
                    INSERT INTO chunks (path, rel_path, title, section, content, position, mtime)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        path,
                        record["rel_path"],
                        record["title"],
                        section,
                        piece,
                        position,
                        record["mtime"],
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO chunks_fts (rowid, path, title, section, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (cursor.lastrowid, path, record["title"], section, piece),
                )
                position += 1

    def remove_missing(self) -> int:
        removed = 0
        with self._lock, self._conn:
            rows = self._conn.execute("SELECT path FROM notes").fetchall()
            for row in rows:
                if not Path(row["path"]).exists():
                    self._conn.execute("DELETE FROM notes WHERE path = ?", (row["path"],))
                    if self._has_fts:
                        self._conn.execute("DELETE FROM notes_fts WHERE path = ?", (row["path"],))
                    if self._has_chunks:
                        self._conn.execute("DELETE FROM chunks WHERE path = ?", (row["path"],))
                        self._conn.execute("DELETE FROM chunks_fts WHERE path = ?", (row["path"],))
                    removed += 1
        return removed

    def reindex(self, force: bool = False) -> dict[str, int]:
        if not self.root.exists():
            raise FileNotFoundError(
                f"Second Brain nao encontrado em {self.root}. "
                "Ajuste secondbrain.path na configuracao."
            )
        if not force and self.count() > 0:
            chunk_count = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if chunk_count == 0:
                force = True
        indexed = 0
        skipped = 0
        seen: set[str] = set()
        for path in self._iter_markdown():
            resolved = str(path.resolve())
            seen.add(resolved)
            if not force:
                row = self._conn.execute(
                    "SELECT mtime, size FROM notes WHERE path = ?", (resolved,)
                ).fetchone()
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if row and abs(row["mtime"] - stat.st_mtime) < 1e-6 and row["size"] == stat.st_size:
                    skipped += 1
                    continue
            if self.index_note(path):
                indexed += 1
        removed = self.remove_missing()
        return {"indexed": indexed, "skipped": skipped, "removed": removed, "total": self.count()}

    # ----------------------------------------------------------------- search
    @staticmethod
    def _tokens(query: str) -> list[str]:
        seen: set[str] = set()
        tokens: list[str] = []
        for token in re.findall(r"\w+", query, flags=re.UNICODE):
            key = token.casefold()
            if len(key) < 2 or key in seen:
                continue
            seen.add(key)
            tokens.append(token)
        return tokens

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        tokens = self._tokens(query)
        if not tokens:
            return []
        if self._has_chunks:
            try:
                return self._search_chunks_fts(tokens, limit)
            except sqlite3.OperationalError:
                pass
        if self._has_fts:
            try:
                return self._search_fts(tokens, limit)
            except sqlite3.OperationalError:
                pass
        return self._search_like(tokens, limit)

    def _search_chunks_fts(self, tokens: list[str], limit: int) -> list[SearchHit]:
        match = " OR ".join(f'"{token}"' for token in tokens)
        sql = """
            SELECT c.path, c.rel_path, c.title, c.section, c.content,
                   bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY score ASC
            LIMIT ?
        """
        rows = self._conn.execute(sql, (match, limit)).fetchall()
        return [
            SearchHit(
                path=row["path"],
                rel_path=row["rel_path"],
                title=row["title"],
                section=row["section"],
                snippet=_preview(row["content"], limit=1400),
                score=float(row["score"]),
            )
            for row in rows
        ]

    def _search_fts(self, tokens: list[str], limit: int) -> list[SearchHit]:
        match = " OR ".join(f'"{token}"' for token in tokens)
        sql = """
            SELECT n.path, n.rel_path, n.title, n.tags, n.body,
                   snippet(notes_fts, 2, '', '', '...', 16) AS snip,
                   bm25(notes_fts) AS score
            FROM notes_fts
            JOIN notes n ON n.path = notes_fts.path
            WHERE notes_fts MATCH ?
            ORDER BY score ASC
            LIMIT ?
        """
        rows = self._conn.execute(sql, (match, limit)).fetchall()
        hits: list[SearchHit] = []
        for row in rows:
            hits.append(
                SearchHit(
                    path=row["path"],
                    rel_path=row["rel_path"],
                    title=row["title"],
                    snippet=row["snip"] or _preview(row["body"]),
                    tags=[t for t in (row["tags"] or "").split(",") if t],
                    score=float(row["score"]),
                )
            )
        return hits

    def _search_like(self, tokens: list[str], limit: int) -> list[SearchHit]:
        clauses = " OR ".join(["title LIKE ? OR body LIKE ?" for _ in tokens])
        params: list[str] = []
        for token in tokens:
            params.extend([f"%{token}%", f"%{token}%"])
        sql = f"""
            SELECT path, rel_path, title, tags, body FROM notes
            WHERE {clauses}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(str(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [
            SearchHit(
                path=row["path"],
                rel_path=row["rel_path"],
                title=row["title"],
                snippet=_preview(row["body"]),
                tags=[t for t in (row["tags"] or "").split(",") if t],
            )
            for row in rows
        ]

    def find_by_title(self, title: str) -> SearchHit | None:
        needle = title.strip()
        if not needle:
            return None
        row = self._conn.execute(
            "SELECT path, rel_path, title, tags, body FROM notes WHERE lower(title) = lower(?)",
            (needle,),
        ).fetchone()
        if not row:
            row = self._conn.execute(
                "SELECT path, rel_path, title, tags, body FROM notes WHERE lower(rel_path) LIKE lower(?) LIMIT 1",
                (f"%{needle}.md",),
            ).fetchone()
        if not row:
            return None
        return SearchHit(
            path=row["path"],
            rel_path=row["rel_path"],
            title=row["title"],
            snippet=_preview(row["body"]),
            tags=[t for t in (row["tags"] or "").split(",") if t],
        )

    def get(self, path: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM notes WHERE path = ?", (path,)).fetchone()

    def recent(self, limit: int = 10) -> list[SearchHit]:
        rows = self._conn.execute(
            "SELECT path, rel_path, title, tags, body FROM notes ORDER BY mtime DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            SearchHit(
                path=row["path"],
                rel_path=row["rel_path"],
                title=row["title"],
                snippet=_preview(row["body"]),
                tags=[t for t in (row["tags"] or "").split(",") if t],
            )
            for row in rows
        ]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])

    def stats(self) -> dict[str, int]:
        chunks = 0
        if self._has_chunks:
            chunks = int(self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return {"notes": self.count(), "chunks": chunks, "fts": int(self._has_fts)}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _preview(body: str, limit: int = 240) -> str:
    clean = re.sub(r"\s+", " ", body or "").strip()
    if len(clean) > limit:
        return clean[:limit].rstrip() + "..."
    return clean


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Divide o corpo em secoes por cabecalho Markdown."""
    sections: list[tuple[str, str]] = []
    heading = "Introdução"
    buffer: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            text = "\n".join(buffer).strip()
            if text:
                sections.append((heading, text))
            heading = match.group(2).strip()
            buffer = [line]
        else:
            buffer.append(line)
    text = "\n".join(buffer).strip()
    if text:
        sections.append((heading, text))
    if not sections:
        sections.append(("Introdução", body.strip()))
    return sections


def _chunk_text(text: str, max_chars: int = 2500) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    parts = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(part), max_chars):
                chunks.append(part[start : start + max_chars])
            continue
        candidate = f"{current}\n\n{part}" if current else part
        if len(candidate) > max_chars:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]
