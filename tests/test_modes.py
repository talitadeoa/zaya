from __future__ import annotations

from zaya.controller.modes import (
    DEFAULT_MODE,
    PERSONAS,
    get_persona,
    normalize_mode_key,
    parse_mode_switch,
    persona_block,
    resolve_mode,
)


def test_quatro_modos_esperados():
    assert set(PERSONAS) == {
        "pesquisadora",
        "trabalhadora",
        "investidora",
        "assistente",
    }
    assert DEFAULT_MODE in PERSONAS


def test_resolve_por_chave_e_acentos():
    assert get_persona("pesquisadora") is PERSONAS["pesquisadora"]
    assert get_persona("PESQUISADORA") is PERSONAS["pesquisadora"]
    assert get_persona("acadêmica") is PERSONAS["pesquisadora"]
    assert normalize_mode_key("Investidora") == "investidora"


def test_conversa_foi_fundida_em_assistente():
    assert get_persona("conversa") is PERSONAS["assistente"]
    assert get_persona("informal") is PERSONAS["assistente"]
    assert resolve_mode("conversa") is PERSONAS["assistente"]


def test_resolve_desconhecido_cai_no_padrao():
    assert resolve_mode("inexistente") is PERSONAS[DEFAULT_MODE]
    assert resolve_mode(None) is PERSONAS[DEFAULT_MODE]


def test_persona_block_menciona_modo_e_idioma():
    block = persona_block(PERSONAS["trabalhadora"])
    assert "Trabalhadora" in block
    assert "português do Brasil" in block
    assert "Parceira" not in block


def test_trabalhadora_aponta_para_o_playbook_de_vagas():
    directive = PERSONAS["trabalhadora"].directive
    assert "playbook-vagas.md" in directive
    assert "state.json" in directive


def test_parse_mode_switch_falas_naturais():
    assert parse_mode_switch("muda para o modo trabalhadora") == "trabalhadora"
    assert parse_mode_switch("Zaya, entra no modo pesquisadora") == "pesquisadora"
    assert parse_mode_switch("modo assistente") == "assistente"
    assert parse_mode_switch("modo conversa") == "assistente"
    assert parse_mode_switch("vira a parceira de trabalhadora") == "trabalhadora"
    assert parse_mode_switch("vira pesquisadora") == "pesquisadora"
    assert parse_mode_switch("muda para o modo finanças") == "investidora"
    assert parse_mode_switch("modo bolsa") == "investidora"
    assert parse_mode_switch("como faço para mudar de emprego?") is None
    assert parse_mode_switch("quero entender o modo como funciona") is None
