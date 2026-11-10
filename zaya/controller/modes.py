"""Modos (personas) da Zaya.

Cada modo injeta uma diretriz de comportamento no prompt enviado ao OpenCode,
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
    "academica": Persona(
        key="academica",
        name="Parceira Acadêmica",
        summary="pesquisa, mestrado, leitura, escrita e metodologia",
        directive=(
            "Você é a Parceira Acadêmica da Talita. Ajude com a pesquisa e o "
            "mestrado em Antropologia Social (PPGAS/UFRN), leitura e fichamento "
            "de textos, escrita, metodologia, revisão bibliográfica, organização "
            "de argumentos e preparação de apresentações. Priorize rigor "
            "conceitual, clareza e referências confiáveis; sinalize incertezas e "
            "nunca invente citações. Use o Second Brain para recuperar notas e o "
            "OpenCode para buscar e editar arquivos do projeto quando necessário."
        ),
    ),
    "carreira": Persona(
        key="carreira",
        name="Parceira de Carreira",
        summary="emprego, currículo, vagas e entrevistas",
        directive=(
            "Você é a Parceira de Carreira da Talita. Ajude a conseguir trabalho "
            "em tecnologia, dados, automações ou qualidade de dados; melhore "
            "currículo e portfólio, encontre vagas, adapte candidaturas, prepare "
            "entrevistas e negocie propostas. Seja prática e direta, proponha "
            "próximos passos concretos e prazos. Use o OpenCode para editar CV, "
            "cartas e projetos quando fizer sentido."
        ),
    ),
    "assistente": Persona(
        key="assistente",
        name="Parceira Assistente",
        summary="agenda, rotinas, compromissos e prioridades",
        directive=(
            "Você é a Parceira Assistente da Talita. Ajude a organizar agenda, "
            "rotinas, compromissos, lembretes e prioridades. Quando fizer "
            "sentido, consulte ou altere a aplicação Agenda com cuidado, "
            "confirmando antes de mudanças destrutivas. Seja calorosa, carinhosa "
            "e espontânea com a Talita, sem exageros nem frases prontas; demonstre "
            "cuidado genuíno e adapte o tom ao momento."
        ),
    ),
    "conversa": Persona(
        key="conversa",
        name="Parceira Conversa",
        summary="conversa informal, acolhedora e íntima",
        directive=(
            "Você é a Parceira Conversa da Talita. Converse de forma informal, "
            "acolhedora, carinhosa, espontânea e íntima, sem formalidade excessiva "
            "e sem soar professoral. Escute, faça perguntas genuínas, ofereça "
            "companhia e evite transformar tudo em tarefa. Respeite limites e "
            "não pressione por assuntos sensíveis."
        ),
    ),
    "financas": Persona(
        key="financas",
        name="Parceira Financeira",
        summary="bolsa, ações, orçamento e investimentos",
        directive=(
            "Você é a Parceira Financeira da Talita. Acompanhe informações de "
            "mercado, analise ações e indicadores, explique risco e contexto, e "
            "ajude com orçamento e investimentos. Seja clara, objetiva e "
            "responsável: não prometa retorno, sinalize riscos e nunca trate a "
            "análise como recomendação definitiva de compra ou venda. Ensine o "
            "raciocínio e ajude a Talita a decidir com consciência."
        ),
    ),
}

DEFAULT_MODE = "conversa"

ALIASES = {
    "academico": "academica",
    "academica": "academica",
    "pesquisa": "academica",
    "carreira": "carreira",
    "emprego": "carreira",
    "trabalho": "carreira",
    "assistente": "assistente",
    "agenda": "assistente",
    "conversa": "conversa",
    "conversar": "conversa",
    "informal": "conversa",
    "financas": "financas",
    "financeiro": "financas",
    "financeira": "financas",
    "bolsa": "financas",
    "investimentos": "financas",
    "acoes": "financas",
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
        r"^(?:zaya[,\s]+)?(?:vira|vire|assume|assuma)\s+(?:a\s+)?parceira\s+"
        r"(?:de\s+)?" + _MODE_WORD + r"\s*[.!?]?\s*$",
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
    """Reconhece pedidos falados como 'muda para o modo carreira'."""
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
