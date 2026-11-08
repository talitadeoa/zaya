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


def test_edicao_pequena_reaproveita_tarefa(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    task = store.upsert("vamos melhorar o terminal com ascii art")
    store.set_status(task.id, "done", "plano salvo")
    store.sync_text("- vamos melhorar o terminal com uma ascii art")
    tasks = store.all()
    assert len(tasks) == 1
    assert tasks[0].id == task.id
    assert tasks[0].status == "done"
    assert tasks[0].notes == "plano salvo"


def test_texto_diferente_cria_nova_tarefa(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    store.upsert("fazer ascii art")
    store.sync_text("- integrar com alexa")
    assert len(store.all()) == 2


def test_sync_nao_reseta_status(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    task = store.upsert("tarefa")
    store.set_status(task.id, "done")
    store.sync_text("- tarefa")
    assert store.all()[0].status == "done"


def test_status_persistido(tmp_path):
    path = tmp_path / "tasks.json"
    store = TaskStore(path)
    task = store.upsert("fazer algo")
    store.set_status(task.id, "done", "concluído")
    again = TaskStore(path)
    loaded = again.all()[0]
    assert loaded.status == "done"
    assert loaded.notes == "concluído"
