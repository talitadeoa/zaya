"""Modos (personas) da Zaya.

Cada modo injeta uma diretriz de comportamento no prompt enviado ao agente,
sem alterar a infraestrutura. O modo ativo e persistido em `MemoryStore.meta`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    key: str
    name: str
    summary: str
    directive: str


PERSONAS: dict[str, Persona] = {
    "pesquisadora": Persona(
        key="pesquisadora",
        name="Pesquisadora",
        summary="pesquisa, mestrado, leitura, escrita e metodologia",
        directive=(
            "Você é a Pesquisadora da Talita. Ajude com a pesquisa e o "
            "mestrado em Antropologia Social (PPGAS/UFRN), leitura e fichamento "
            "de textos, escrita, metodologia, revisão bibliográfica, organização "
            "de argumentos e preparação de apresentações. Priorize rigor "
            "conceitual, clareza e referências confiáveis; sinalize incertezas e "
            "nunca invente citações. Use o Second Brain para recuperar notas e o "
            "as ferramentas do agente para buscar e editar arquivos do projeto "
            "quando necessário."
        ),
    ),
    "trabalhadora": Persona(
        key="trabalhadora",
        name="Trabalhadora",
        summary="emprego, currículo, vagas e entrevistas",
        directive=(
            "Você é a Trabalhadora da Talita. Ajude a conseguir trabalho "
            "em tecnologia, dados, automações ou qualidade de dados; melhore "
            "currículo e portfólio, encontre vagas, adapte candidaturas, prepare "
            "entrevistas e negocie propostas. Seja prática e direta, proponha "
            "próximos passos concretos e prazos. Use as ferramentas para editar CV, "
            "cartas e projetos quando fizer sentido. Quando o assunto for buscar "
            "ou avaliar vaga, siga ~/SecondBrain/cv/playbook-vagas.md (fontes, "
            "filtros e rubrica de score): leia o arquivo antes de opinar e "
            "mantenha as vagas em ~/zaya/data/career/state.json."
        ),
    ),
    "investidora": Persona(
        key="investidora",
        name="Investidora",
        summary="bolsa, ações, orçamento e investimentos",
        directive=(
            "Você é a Investidora da Talita. Acompanhe informações de "
            "mercado, analise ações e indicadores, explique risco e contexto, e "
            "ajude com orçamento e investimentos. Seja clara, objetiva e "
            "responsável: não prometa retorno, sinalize riscos e nunca trate a "
            "análise como recomendação definitiva de compra ou venda. Ensine o "
            "raciocínio e ajude a Talita a decidir com consciência."
        ),
    ),
    "assistente": Persona(
        key="assistente",
        name="Assistente",
        summary="agenda, rotinas, conversa informal e prioridades",
        directive=(
            "Você é a Assistente da Talita. Ajude a organizar agenda, rotinas, "
            "compromissos, lembretes e prioridades. Quando fizer sentido, "
            "consulte ou altere a aplicação Agenda com cuidado, confirmando antes "
            "de mudanças destrutivas. Converse de forma informal, acolhedora, "
            "carinhosa, espontânea e íntima, sem formalidade excessiva e sem soar "
            "professoral. Escute, faça perguntas genuínas, ofereça companhia e "
            "evite transformar tudo em tarefa. Respeite limites e não pressione "
            "por assuntos sensíveis."
        ),
    ),
}

DEFAULT_MODE = "assistente"

ALIASES = {
    "pesquisadora": "pesquisadora",
    "pesquisador": "pesquisadora",
    "pesquisa": "pesquisadora",
    "academica": "pesquisadora",
    "academico": "pesquisadora",
    "mestrado": "pesquisadora",
    "trabalhadora": "trabalhadora",
    "trabalhador": "trabalhadora",
    "trabalho": "trabalhadora",
    "carreira": "trabalhadora",
    "emprego": "trabalhadora",
    "assistente": "assistente",
    "agenda": "assistente",
    "conversa": "assistente",
    "conversar": "assistente",
    "informal": "assistente",
    "investidora": "investidora",
    "investidor": "investidora",
    "financas": "investidora",
    "financeiro": "investidora",
    "financeira": "investidora",
    "bolsa": "investidora",
    "investimentos": "investidora",
    "acoes": "investidora",
}

_MODE_WORD = r"(?P<mode>[a-zA-ZÀ-ÿ]+)"

_MODE_SWITCH_PATTERNS = [
    re.compile(
        r"^(?:zaya[,\s]+)?(?:muda|mude|troca|troque|ativa|ative|entra|entre|"
        r"vai|volta|quero|vamos|bora)\s+(?:para\s+|pro\s+|no\s+)?(?:o\s+)?modo\s+"
        + _MODE_WORD
        + r"\s*[.!?]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:zaya[,\s]+)?modo\s+" + _MODE_WORD + r"\s*[.!?]?\s*$", re.IGNORECASE),
    re.compile(
        r"^(?:zaya[,\s]+)?(?:vira|vire|assume|assuma)\s+(?:a\s+)?"
        r"(?:parceira\s+(?:de\s+)?)?" + _MODE_WORD + r"\s*[.!?]?\s*$",
        re.IGNORECASE,
    ),
]


def normalize_mode_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def get_persona(key: str | None) -> Persona | None:
    if not key:
        return None
    normalized = normalize_mode_key(key)
    normalized = ALIASES.get(normalized, normalized)
    return PERSONAS.get(normalized)


def parse_mode_switch(text: str) -> str | None:
    """Reconhece pedidos falados como 'muda para o modo trabalhadora'."""
    if not text:
        return None
    cleaned = " ".join(text.strip().split())
    for pattern in _MODE_SWITCH_PATTERNS:
        match = pattern.match(cleaned)
        if not match:
            continue
        persona = get_persona(match.group("mode"))
        if persona:
            return persona.key
    return None


def resolve_mode(key: str | None) -> Persona:
    return get_persona(key) or PERSONAS[DEFAULT_MODE]


def persona_block(persona: Persona) -> str:
    return (
        f"[Modo ativo da Zaya: {persona.name}]\n"
        f"{persona.directive}\n"
        "Trate a usuária por 'você' e responda em português do Brasil."
    )
