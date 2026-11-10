"""Memoria operacional da Zaia: conversas, turnos e registros (SQLite).

As consultas sao locais e de microssegundos, entao o acesso continua sincrono
(chamar ``asyncio.to_thread`` para cada SELECT custaria mais do que a query).
Para tornar seguro o ``check_same_thread=False``, todas as operacoes -- leitura
e escrita -- passam pelo mesmo ``RLock``, e o banco roda em WAL com
``busy_timeout`` para tolerar acesso concorrente de outras threads.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

# Versao atual do schema. Incremente ao adicionar novas migracoes em _migrate.
SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, db_path: Path):
        db_path = Path(db_path).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_path TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'assistente',
                    opencode_session_id TEXT,
                    title TEXT,
                    is_current INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                );

                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    kind TEXT NOT NULL,
                    path TEXT,
                    title TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    spoken INTEGER NOT NULL DEFAULT 0,
                    cancelled INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(spoken, cancelled, due_at);
                """
            )
            self._migrate()

    def _migrate(self) -> None:
        """Aplica migracoes pendentes e registra a versao do schema."""
        version = self.schema_version()
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        # v2: conversas passaram a ser separadas por modo.
        if "mode" not in columns:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'assistente'"
                )
        with self._conn:
            if version < SCHEMA_VERSION:
                # remove o indice antigo (project_path, is_current) e recria
                # com a coluna mode, que pode ter acabado de ser adicionada.
                self._conn.execute("DROP INDEX IF EXISTS idx_conv_project")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_project "
                "ON conversations(project_path, mode, is_current)"
            )
        if version < SCHEMA_VERSION:
            self.set_meta("schema_version", str(SCHEMA_VERSION))

    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        try:
            return int(row["value"]) if row else 1
        except (TypeError, ValueError):
            return 1

    # ------------------------------------------------------------ conversations
    def current_conversation(self, project_path: str, mode: str = "assistente") -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM conversations
                WHERE project_path = ? AND mode = ? AND is_current = 1
                ORDER BY id DESC LIMIT 1
                """,
                (project_path, mode),
            ).fetchone()

    def create_conversation(
        self, project_path: str, title: str | None = None, mode: str = "assistente"
    ) -> sqlite3.Row:
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE conversations SET is_current = 0 WHERE project_path = ? AND mode = ?",
                (project_path, mode),
            )
            cursor = self._conn.execute(
                """
                INSERT INTO conversations
                    (project_path, mode, title, is_current, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (project_path, mode, title or "", now, now),
            )
            conv_id = cursor.lastrowid
        if conv_id is None:
            raise RuntimeError("nao foi possivel criar a conversa")
        return self.get_conversation(conv_id)

    def get_conversation(self, conversation_id: int) -> sqlite3.Row:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"conversa {conversation_id} nao encontrada")
        return row

    def get_or_create_conversation(
        self, project_path: str, mode: str = "assistente"
    ) -> sqlite3.Row:
        row = self.current_conversation(project_path, mode)
        if row:
            return row
        return self.create_conversation(project_path, mode=mode)

    def list_conversations(self, project_path: str, mode: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._conn.execute(
                    """
                    SELECT c.*,
                           (SELECT COUNT(*) FROM turns t
                            WHERE t.conversation_id = c.id) AS turn_count
                    FROM conversations c
                    WHERE c.project_path = ? AND c.mode = ?
                    ORDER BY c.updated_at DESC, c.id DESC
                    """,
                    (project_path, mode),
                ).fetchall()
            )

    def set_current(self, conversation_id: int) -> None:
        conv = self.get_conversation(conversation_id)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE conversations SET is_current = 0 WHERE project_path = ? AND mode = ?",
                (conv["project_path"], conv["mode"]),
            )
            self._conn.execute(
                "UPDATE conversations SET is_current = 1, updated_at = ? WHERE id = ?",
                (_now(), conversation_id),
            )

    def set_session(self, conversation_id: int, session_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE conversations SET opencode_session_id = ?, updated_at = ? WHERE id = ?",
                (session_id, _now(), conversation_id),
            )

    def touch(self, conversation_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_now(), conversation_id),
            )

    # -------------------------------------------------------------------- turns
    def add_turn(self, conversation_id: int, role: str, text: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO turns (conversation_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, text, _now()),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_now(), conversation_id),
            )

    def turns(self, conversation_id: int, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM turns WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def turns_between(
        self, start_iso: str, end_iso: str, project_path: str | None = None
    ) -> list[sqlite3.Row]:
        """Turnos de todas as conversas num intervalo (para consolidar o dia)."""
        query = """
            SELECT t.*, c.mode AS mode, c.title AS conversation_title,
                   c.project_path AS project_path
            FROM turns t
            JOIN conversations c ON c.id = t.conversation_id
            WHERE t.created_at >= ? AND t.created_at < ?
        """
        params: list[Any] = [start_iso, end_iso]
        if project_path:
            query += " AND c.project_path = ?"
            params.append(project_path)
        query += " ORDER BY t.id ASC"
        with self._lock:
            return list(self._conn.execute(query, params).fetchall())

    # ------------------------------------------------------------------ records
    def record_note(
        self,
        conversation_id: int | None,
        kind: str,
        path: str,
        title: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO records (conversation_id, kind, path, title, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, kind, path, title, _now()),
            )

    def records_between(self, start_iso: str, end_iso: str) -> list[sqlite3.Row]:
        """Registros (notas criadas etc.) num intervalo de tempo."""
        with self._lock:
            return list(
                self._conn.execute(
                    "SELECT * FROM records WHERE created_at >= ? AND created_at < ? "
                    "ORDER BY id ASC",
                    (start_iso, end_iso),
                ).fetchall()
            )

    def recent_records(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM records ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    # ----------------------------------------------------------------- reminders
    def add_reminder(self, text: str, due_at: str) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO reminders (text, due_at, created_at)
                VALUES (?, ?, ?)
                """,
                (text, due_at, _now()),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("nao foi possivel criar o lembrete")
            return int(cursor.lastrowid)

    def due_reminders(self, now_iso: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM reminders
                WHERE spoken = 0 AND cancelled = 0 AND due_at <= ?
                ORDER BY due_at ASC
                """,
                (now_iso,),
            ).fetchall()

    def pending_reminders(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM reminders
                WHERE spoken = 0 AND cancelled = 0
                ORDER BY due_at ASC
                """
            ).fetchall()

    def mark_reminder_spoken(self, reminder_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE reminders SET spoken = 1 WHERE id = ?", (reminder_id,))

    def cancel_reminder(self, reminder_id: int) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE reminders SET cancelled = 1 WHERE id = ? AND cancelled = 0",
                (reminder_id,),
            )
            return cursor.rowcount > 0

    def reminder_stats(self) -> dict[str, int]:
        with self._lock:
            pending = self._conn.execute(
                "SELECT COUNT(*) FROM reminders WHERE spoken = 0 AND cancelled = 0"
            ).fetchone()[0]
        return {"pending": int(pending)}

    # --------------------------------------------------------------------- meta
    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def claim_meta(self, key: str, value: str) -> bool:
        """Reserva uma chave de forma atomica; False se ja existia."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )
            return cursor.rowcount > 0

    def delete_meta(self, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM meta WHERE key = ?", (key,))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conversations = self._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            turns = self._conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            records = self._conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        return {
            "conversations": int(conversations),
            "turns": int(turns),
            "records": int(records),
        }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
