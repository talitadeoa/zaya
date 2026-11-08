from __future__ import annotations

from zora.controller.modes import (
    DEFAULT_MODE,
    PERSONAS,
    get_persona,
    normalize_mode_key,
    parse_mode_switch,
    persona_block,
    resolve_mode,
)


def test_cinco_modos_esperados():
    assert set(PERSONAS) == {
        "academica",
        "carreira",
        "assistente",
        "conversa",
        "financas",
    }
    assert DEFAULT_MODE in PERSONAS


def test_resolve_por_chave_e_acentos():
    assert get_persona("acadêmica") is PERSONAS["academica"]
    assert get_persona("ACADEMICA") is PERSONAS["academica"]
    assert normalize_mode_key("Conversa") == "conversa"


def test_resolve_desconhecido_cai_no_padrao():
    assert resolve_mode("inexistente") is PERSONAS[DEFAULT_MODE]
    assert resolve_mode(None) is PERSONAS[DEFAULT_MODE]


def test_persona_block_menciona_modo_e_idioma():
    block = persona_block(PERSONAS["carreira"])
    assert "Parceira de Carreira" in block
    assert "português do Brasil" in block


def test_parse_mode_switch_falas_naturais():
    assert parse_mode_switch("muda para o modo carreira") == "carreira"
    assert parse_mode_switch("Zora, entra no modo acadêmica") == "academica"
    assert parse_mode_switch("modo conversa") == "conversa"
    assert parse_mode_switch("vira a parceira de carreira") == "carreira"
    assert parse_mode_switch("muda para o modo finanças") == "financas"
    assert parse_mode_switch("modo bolsa") == "financas"
    assert parse_mode_switch("como faço para mudar de emprego?") is None
    assert parse_mode_switch("quero entender o modo como funciona") is None
