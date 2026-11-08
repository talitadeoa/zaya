from __future__ import annotations

import pytest

from tests.conftest import write_note
from zora.secondbrain.notes import ConfirmationRequired, NoteConflict


def seed(brain):
    write_note(
        brain.root,
        "Projects/Automacao.md",
        """---
title: Projeto de Automação
tags: [projeto, automacao]
---
# Projeto de Automação

Queremos automatizar a captura de ideias. Link: [[Decisao]].
""",
    )
    write_note(
        brain.root,
        "Decisions/Decisao.md",
        """---
title: Decisao
tags: [decision]
---
# Decisao

Usar OpenCode como motor de execução.
""",
    )
    brain.index.reindex(force=True)


def test_reindex_and_search(brain):
    seed(brain)
    assert brain.index.count() == 2
    hits = brain.index.search("automacao", limit=5)
    assert hits
    assert any("Automação" in hit.title for hit in hits)
    # busca sem acento encontra nota acentuada
    hits2 = brain.index.search("execucao", limit=5)
    assert any(hit.title == "Decisao" for hit in hits2)


def test_search_returns_full_relevant_section(brain):
    write_note(
        brain.root,
        "Memoria.md",
        """---
title: Memória de teste
---
# Memória de teste

## Projeto de pesquisa atual

Título de trabalho: "Fazer ciência para a saúde: experiências, práticas e
expectativas entre pesquisadores do HUOL/UFRN".

## Outra seção

Conteúdo irrelevante.
""",
    )
    brain.index.reindex(force=True)
    hits = brain.index.search("título de trabalho projeto de pesquisa", limit=5)
    assert hits
    assert any("Fazer ciência para a saúde" in hit.snippet for hit in hits)
    assert any(hit.section == "Projeto de pesquisa atual" for hit in hits)


def test_create_without_confirmation_by_default(brain):
    note = brain.create_note("Ideia", "conteúdo")
    assert note.path.exists()


def test_create_requires_confirmation_when_enabled(brain):
    brain.cfg.confirm_create = True
    with pytest.raises(ConfirmationRequired):
        brain.create_note("Ideia", "conteúdo")


def test_update_and_delete_still_require_confirmation(brain):
    seed(brain)
    with pytest.raises(ConfirmationRequired):
        brain.update_note("Decisao", "novo conteúdo")
    with pytest.raises(ConfirmationRequired):
        brain.delete_note("Decisao")


def test_create_update_delete_with_backup(brain):
    seed(brain)
    note = brain.create_note(
        "Nova Ideia",
        "testar links e tags",
        tags=["zora", "idea"],
        links=["Projeto de Automação"],
        confirm=True,
    )
    assert note.path.exists()
    text = note.path.read_text(encoding="utf-8")
    assert "[[Projeto de Automação]]" in text
    assert "tags:" in text
    assert brain.index.search("testar links", limit=5)

    with pytest.raises(NoteConflict):
        brain.create_note("Nova Ideia", "outro", confirm=True)

    updated = brain.update_note("Nova Ideia", "# Nova Ideia\n\nconteúdo atualizado", confirm=True)
    assert "conteúdo atualizado" in updated.path.read_text(encoding="utf-8")
    backups = list((brain.root / ".zora" / "backups").rglob("Nova Ideia.md"))
    assert backups, "backup deveria existir"

    removed = brain.delete_note("Nova Ideia", confirm=True)
    assert not note.path.exists()
    assert removed.exists()
    assert ".zora/trash" in str(removed)


def test_path_traversal_blocked(brain):
    with pytest.raises(ValueError):
        brain._safe_path("../fora.md")
    with pytest.raises(ValueError):
        brain._safe_path("/etc/passwd")


def test_find_by_title(brain):
    seed(brain)
    hit = brain.index.find_by_title("Projeto de Automação")
    assert hit is not None
    assert hit.rel_path.endswith("Automacao.md")


def test_preferences_load_and_append(brain):
    path = brain.preferences_path
    path.write_text(
        """---
title: Preferências
tags: [preferencias]
---
# Preferências

- Nome preferido: Talita.

## Aprendido com o tempo
""",
        encoding="utf-8",
    )
    loaded = brain.load_preferences()
    assert "Talita" in loaded
    assert "---" not in loaded
    assert "# Preferências" not in loaded

    assert brain.append_preference("Prefiro respostas curtas")
    assert not brain.append_preference("prefiro respostas curtas")
    text = path.read_text(encoding="utf-8")
    assert text.count("Prefiro respostas curtas") == 1

    brain.cfg.preferences_file = str(brain.root / "custom_prefs.md")
    assert brain.append_preference("Gosto de café")
    custom = (brain.root / "custom_prefs.md").read_text(encoding="utf-8")
    assert "Gosto de café" in custom
