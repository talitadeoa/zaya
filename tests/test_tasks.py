from __future__ import annotations

from zora.tasks.store import TaskStore


def test_sync_quebra_br_e_bullets(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    tasks = store.sync_text("- fazer A<br>- fazer B<br><br>- fazer C")
    assert [task.title for task in tasks] == ["fazer A", "fazer B", "fazer C"]
    assert all(task.status == "pending" for task in tasks)


def test_sync_e_idempotente(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    store.sync_text("tarefa única")
    store.sync_text("tarefa única")
    assert len(store.all()) == 1


def test_status_persistido(tmp_path):
    path = tmp_path / "tasks.json"
    store = TaskStore(path)
    task = store.upsert("fazer algo")
    store.set_status(task.id, "done", "concluído")
    again = TaskStore(path)
    loaded = again.all()[0]
    assert loaded.status == "done"
    assert loaded.notes == "concluído"
