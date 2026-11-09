from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from zora.memory.store import MemoryStore


def test_conversations_and_turns(tmp_path):
    store = MemoryStore(tmp_path / "zora.db")
    try:
        conv = store.get_or_create_conversation("/proj")
        assert conv["project_path"] == "/proj"
        store.set_session(int(conv["id"]), "ses_123")
        store.add_turn(int(conv["id"]), "user", "olá")
        store.add_turn(int(conv["id"]), "assistant", "oi")

        same = store.get_or_create_conversation("/proj")
        assert int(same["id"]) == int(conv["id"])
        assert same["opencode_session_id"] == "ses_123"

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
    store = MemoryStore(tmp_path / "zora.db")
    try:
        assistente = store.get_or_create_conversation("/proj", "assistente")
        carreira = store.get_or_create_conversation("/proj", "carreira")
        assert int(assistente["id"]) != int(carreira["id"])

        assert (
            int(store.get_or_create_conversation("/proj", "assistente")["id"])
            == int(assistente["id"])
        )
        assert (
            int(store.get_or_create_conversation("/proj", "carreira")["id"])
            == int(carreira["id"])
        )

        nova = store.create_conversation("/proj", title="nova carreira", mode="carreira")
        assert (
            int(store.get_or_create_conversation("/proj", "assistente")["id"])
            == int(assistente["id"])
        )
        assert (
            int(store.get_or_create_conversation("/proj", "carreira")["id"])
            == int(nova["id"])
        )

        ids = {int(row["id"]) for row in store.list_conversations("/proj", "carreira")}
        assert ids == {int(carreira["id"]), int(nova["id"])}

        store.set_current(int(carreira["id"]))
        assert (
            int(store.get_or_create_conversation("/proj", "carreira")["id"])
            == int(carreira["id"])
        )
    finally:
        store.close()


def test_reminders(tmp_path):
    store = MemoryStore(tmp_path / "zora.db")
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
    store = MemoryStore(tmp_path / "zora.db")
    try:
        assert store.get_meta("model") is None
        store.set_meta("model", "deepseek/deepseek-flash")
        assert store.get_meta("model") == "deepseek/deepseek-flash"
        store.set_meta("model", "deepseek/deepseek-v4-pro")
        assert store.get_meta("model") == "deepseek/deepseek-v4-pro"
    finally:
        store.close()


def test_pragmas_configured(tmp_path):
    store = MemoryStore(tmp_path / "zora.db")
    try:
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        store.close()


def test_concurrent_writes_are_serialized(tmp_path):
    store = MemoryStore(tmp_path / "zora.db")
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
    with MemoryStore(tmp_path / "zora.db") as store:
        store.set_meta("k", "v")
    store.close()
    store.close()
