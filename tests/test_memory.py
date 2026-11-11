from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from zaya.memory.store import SCHEMA_VERSION, MemoryStore


def test_conversations_and_turns(tmp_path):
    store = MemoryStore(tmp_path / "zaya.db")
    try:
        conv = store.get_or_create_conversation("/proj")
        assert conv["project_path"] == "/proj"
        store.set_session(int(conv["id"]), "ses_123")
        store.add_turn(int(conv["id"]), "user", "olá")
        store.add_turn(int(conv["id"]), "assistant", "oi")

        same = store.get_or_create_conversation("/proj")
        assert int(same["id"]) == int(conv["id"])
        assert same["agent_session_id"] == "ses_123"

        turns = store.turns(int(conv["id"]))
        assert [t["role"] for t in turns] == ["user", "assistant"]

        new_conv = store.create_conversation("/proj", title="nova")
        assert int(new_conv["id"]) != int(conv["id"])
        current = store.get_or_create_conversation("/proj")
        assert int(current["id"]) == int(new_conv["id"])

        store.record_note(int(new_conv["id"]), "note", "/tmp/a.md", "A")
        assert store.stats()["records"] == 1
        assert store.recent_records()[0]["title"] == "A"
    finally:
        store.close()


def test_conversations_per_mode(tmp_path):
    store = MemoryStore(tmp_path / "zaya.db")
    try:
        assistente = store.get_or_create_conversation("/proj", "assistente")
        trabalhadora = store.get_or_create_conversation("/proj", "trabalhadora")
        assert int(assistente["id"]) != int(trabalhadora["id"])

        assert int(store.get_or_create_conversation("/proj", "assistente")["id"]) == int(
            assistente["id"]
        )
        assert int(store.get_or_create_conversation("/proj", "trabalhadora")["id"]) == int(
            trabalhadora["id"]
        )

        nova = store.create_conversation("/proj", title="nova trabalhadora", mode="trabalhadora")
        assert int(store.get_or_create_conversation("/proj", "assistente")["id"]) == int(
            assistente["id"]
        )
        assert int(store.get_or_create_conversation("/proj", "trabalhadora")["id"]) == int(
            nova["id"]
        )

        ids = {int(row["id"]) for row in store.list_conversations("/proj", "trabalhadora")}
        assert ids == {int(trabalhadora["id"]), int(nova["id"])}

        store.set_current(int(trabalhadora["id"]))
        assert int(store.get_or_create_conversation("/proj", "trabalhadora")["id"]) == int(
            trabalhadora["id"]
        )
    finally:
        store.close()


def test_migracao_v3_renomeia_modos_e_funde_conversa(tmp_path):
    path = tmp_path / "zaya.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_path TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'assistente',
            opencode_session_id TEXT,
            title TEXT,
            is_current INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    conn.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    conn.execute("INSERT INTO meta VALUES ('mode', 'conversa')")
    antiga = "2026-11-09T09:00:00"
    recente = "2026-11-09T11:00:00"
    for mode, updated in (
        ("academica", antiga),
        ("conversa", antiga),
        ("assistente", recente),
    ):
        conn.execute(
            "INSERT INTO conversations "
            "(project_path, mode, title, is_current, created_at, updated_at) "
            "VALUES ('/proj', ?, ?, 1, ?, ?)",
            (mode, mode, updated, updated),
        )
    conn.commit()
    conn.close()

    store = MemoryStore(path)
    try:
        assert store.get_meta("mode") == "assistente"
        assert store.get_or_create_conversation("/proj", "pesquisadora")["title"] == "academica"

        atuais = store.list_conversations("/proj", "assistente")
        assert {row["title"] for row in atuais} == {"assistente", "conversa"}
        assert sum(1 for row in atuais if row["is_current"]) == 1
        assert store.get_or_create_conversation("/proj", "assistente")["title"] == "assistente"
    finally:
        store.close()


def test_rehome_conversations_e_richest_mode(tmp_path):
    store = MemoryStore(tmp_path / "zaya.db")
    try:
        antiga = store.get_or_create_conversation("/old/proj", "assistente")
        store.add_turn(int(antiga["id"]), "user", "oi")
        store.add_turn(int(antiga["id"]), "assistant", "olá")
        vazia = store.get_or_create_conversation("/old/proj", "investidora")
        nova = store.get_or_create_conversation("/new/proj", "assistente")

        assert store.project_has_turns("/old/proj")
        assert not store.project_has_turns("/new/proj")
        assert store.richest_mode("/old/proj") == "assistente"

        assert store.rehome_conversations("/old/proj", "/new/proj") == 2
        assert not store.project_has_turns("/old/proj")
        assert store.project_has_turns("/new/proj")

        atual = store.get_or_create_conversation("/new/proj", "assistente")
        assert int(atual["id"]) == int(antiga["id"])
        ids = {
            int(row["id"])
            for row in store.list_conversations("/new/proj", "assistente")
        }
        assert ids == {int(antiga["id"]), int(nova["id"])}
        assert (
            int(store.get_or_create_conversation("/new/proj", "investidora")["id"])
            == int(vazia["id"])
        )
    finally:
        store.close()


def test_reminders(tmp_path):
    store = MemoryStore(tmp_path / "zaya.db")
    try:
        rid = store.add_reminder("beber água", "2026-11-08T10:00:00+00:00")
        assert store.reminder_stats()["pending"] == 1
        assert store.due_reminders("2026-11-08T09:59:00+00:00") == []
        due = store.due_reminders("2026-11-08T10:00:00+00:00")
        assert len(due) == 1
        assert due[0]["text"] == "beber água"

        store.mark_reminder_spoken(rid)
        assert store.pending_reminders() == []
        assert store.reminder_stats()["pending"] == 0

        rid2 = store.add_reminder("estudar", "2026-11-09T09:00:00+00:00")
        assert store.cancel_reminder(rid2) is True
        assert store.cancel_reminder(rid2) is False
        assert store.pending_reminders() == []
    finally:
        store.close()


def test_meta(tmp_path):
    store = MemoryStore(tmp_path / "zaya.db")
    try:
        assert store.get_meta("model") is None
        store.set_meta("model", "deepseek/deepseek-flash")
        assert store.get_meta("model") == "deepseek/deepseek-flash"
        store.set_meta("model", "deepseek/deepseek-v4-pro")
        assert store.get_meta("model") == "deepseek/deepseek-v4-pro"
    finally:
        store.close()


def test_pragmas_configured(tmp_path):
    store = MemoryStore(tmp_path / "zaya.db")
    try:
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        store.close()


def test_concurrent_writes_are_serialized(tmp_path):
    store = MemoryStore(tmp_path / "zaya.db")
    try:
        conv = store.get_or_create_conversation("/proj")
        conversation_id = int(conv["id"])

        def worker(index: int) -> int:
            store.add_turn(conversation_id, "user", f"msg {index}")
            return store.stats()["turns"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(50)))

        assert store.stats()["turns"] == 50
        texts = [row["text"] for row in store.turns(conversation_id, limit=50)]
        assert len(texts) == 50
    finally:
        store.close()


def test_close_is_idempotent_and_context_manager(tmp_path):
    with MemoryStore(tmp_path / "zaya.db") as store:
        store.set_meta("k", "v")
    store.close()
    store.close()


def test_schema_version_recorded(tmp_path):
    store = MemoryStore(tmp_path / "zaya.db")
    try:
        assert store.schema_version() == SCHEMA_VERSION
        assert store.get_meta("schema_version") == str(SCHEMA_VERSION)
    finally:
        store.close()


def test_migrates_old_database_without_mode(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_path TEXT NOT NULL,
            opencode_session_id TEXT,
            title TEXT,
            is_current INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_conv_project ON conversations(project_path, is_current);
        INSERT INTO conversations (project_path, is_current, created_at, updated_at)
            VALUES ('/proj', 1, 'x', 'x');
        """
    )
    conn.commit()
    conn.close()

    with MemoryStore(db_path) as store:
        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        assert "mode" in columns
        assert store.schema_version() == SCHEMA_VERSION
        assert store.current_conversation("/proj")["mode"] == "assistente"
        # o indice foi recriado cobrindo a coluna mode
        indexes = {row["name"] for row in store._conn.execute("PRAGMA index_list(conversations)")}
        assert "idx_conv_project" in indexes
