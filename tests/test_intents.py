from __future__ import annotations

from zora.controller.intents import (
    derive_title,
    format_context,
    needs_second_brain_context,
    parse_preference,
    parse_register,
)


def test_parse_decision():
    intent = parse_register("registre a decisão: vamos usar OpenCode como motor")
    assert intent is not None
    assert intent.kind == "decision"
    assert intent.content == "vamos usar OpenCode como motor"
    assert "decision" in intent.tags
    assert intent.title


def test_parse_idea_prefix_stripped():
    intent = parse_register("registre a ideia: testar criação automática de nota")
    assert intent is not None
    assert intent.kind == "idea"
    assert intent.content == "testar criação automática de nota"
    assert intent.title == "testar criação automática de nota"


def test_parse_idea_with_que():
    intent = parse_register("anote que preciso revisar o deploy antes de migrar")
    assert intent is not None
    assert intent.kind == "note"
    assert intent.content == "preciso revisar o deploy antes de migrar"


def test_parse_quoted_title():
    intent = parse_register('salve a nota "Ideias de automação": capturar voz e registrar decisões')
    assert intent is not None
    assert intent.title == "Ideias de automação"
    assert "capturar voz" in intent.content


def test_non_register():
    assert parse_register("qual é a capital da França?") is None
    assert parse_register("Zora, revise o projeto") is None


def test_parse_preference():
    assert (
        parse_preference("anote como preferência: prefiro respostas curtas")
        == "prefiro respostas curtas"
    )
    assert (
        parse_preference("registre a preferência: não gosto de formalidade")
        == "não gosto de formalidade"
    )
    assert parse_preference("lembre que eu prefiro café sem açúcar") == "Prefiro café sem açúcar"
    assert parse_preference("qual é a capital da França?") is None


def test_context_keywords():
    assert needs_second_brain_context("continue aquele projeto de automação")
    assert needs_second_brain_context("o que decidimos sobre o deploy?")
    assert not needs_second_brain_context("escreva uma função fibonacci em python")


def test_derive_title_and_format_context():
    assert derive_title("  Uma ideia muito legal sobre memória contextual  ") == (
        "Uma ideia muito legal sobre memória contextual"
    )

    class Hit:
        title = "Nota"
        rel_path = "Inbox/Nota.md"

        def as_context(self, max_chars=500):
            return "- **Nota** (`Inbox/Nota.md`): resumo"

    text = format_context([Hit()])
    assert "Contexto do Second Brain" in text
    assert "Nota" in text
