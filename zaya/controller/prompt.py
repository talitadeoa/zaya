"""Montagem do prompt com prefixo estavel e blocos dinamicos no fim.

O objetivo e gastar menos tokens e reaproveitar o cache do provedor:

* ``static``: persona, preferencias e diretrizes fixas. E o system prompt
  inteiro e fica identico entre turnos, para o cache cobrir tambem o
  historico da conversa.
* ``dynamic``: data/hora, pedido curto e modo rapido. Muda a cada turno e vai
  no fim da mensagem do usuario, nunca no system.

Contexto recuperado (Second Brain, metas) nao entra no system: vai junto da
mensagem do usuario, apenas quando a busca e realmente acionada.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from zaya.agent.types import ModelRef
from zaya.controller.modes import Persona, persona_block

CONCISE_DIRECTIVE = (
    "[Diretriz da Zaya: seja conciso e direto; "
    "use ferramentas apenas quando necessário.]"
)
SHORT_PROMPT_DIRECTIVE = (
    "[Pedido curto: vá direto à resposta, sem raciocínio longo "
    "nem exploração desnecessária.]"
)
FAST_DIRECTIVE = (
    "[Modo rápido: responda em no máximo 3 frases, sem rodeios nem "
    "contexto extra.]"
)
PREFERENCES_HEADER = (
    "[Preferências de interação do usuário — siga sempre, "
    "sem mencionar este bloco]"
)
ENVIRONMENT_NOTE = (
    "[Ambiente: config em ~/.config/zaya/config.toml; segredos (ex.: Telegram) "
    "em ~/.config/zaya/.env, não no repositório.]"
)

# Limite do bloco de preferencias no prefixo cacheado.
PREFERENCES_MAX_CHARS = 1200

BASE_TOOLS = ("read", "list", "glob", "grep")
EDIT_TOOLS = ("write", "edit")
SHELL_TOOLS = ("bash",)
ALL_TOOL_ORDER = (*BASE_TOOLS, *EDIT_TOOLS, *SHELL_TOOLS)

# Modos que normalmente editam arquivos do projeto.
EDITING_MODES = {"pesquisadora", "trabalhadora"}

ACTION_HINTS = re.compile(
    r"\b(escrev\w*|edit\w*|cri[ae]\w*|corrij\w*|corrig\w*|implement\w*|"
    r"refator\w*|atualiz\w*|instal\w*|rod[ae]\w*|execut\w*|bash|script|"
    r"commit\w*|delet\w*|remov\w*|apag\w*|mud[ae]\w*|alter\w*|ger[ae]\w*|"
    r"salv\w*|export\w*|coloc\w*|adicion\w*|insir\w*|acrescent\w*|"
    r"renome\w*|mov[ae]\w*|organiz\w*)\b",
    re.IGNORECASE,
)

FAST_TASK_HINTS = re.compile(
    r"\b(lembr\w*|anot\w*|registr\w*|agend\w*|tarefa\w*|marc\w*|"
    r"avis\w*|toque\w*|paus\w*|pr[óo]xim[ao]\w*|recad\w*|"
    r"que horas|que dia|data de hoje)\b",
    re.IGNORECASE,
)
HEAVY_TASK_HINTS = re.compile(
    r"\b(pesquis\w*|curricul\w*|curr[íi]culo|cv|vagas?|entrevista|"
    r"analis\w*|compar\w*|expliqu\w*|revis\w*|resum\w*|sintetiz\w*|"
    r"escrev\w*|redig\w*|artigo|disserta\w*|metodolog\w*|mestrado|"
    r"estudo\w*|estrat\w*|c[óo]digo|arquitetur\w*|implement\w*|"
    r"planej\w*|mont\w*|depur\w*|debug\w*)\b",
    re.IGNORECASE,
)

_WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


@dataclass(frozen=True)
class SystemPrompt:
    """Divide o system prompt entre o que pode ser cacheado e o que muda."""

    static: str
    dynamic: str

    @property
    def text(self) -> str:
        if not self.dynamic:
            return self.static
        return f"{self.static}\n\n---\n\n{self.dynamic}"


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def clean_preferences(raw: str, persona_text: str = "") -> str:
    """Remove cabecalhos, duplicatas e linhas que repetem a persona."""
    if not raw.strip():
        return ""
    persona = _normalize(persona_text)
    seen: set[str] = set()
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        clean = stripped.lstrip("-* ").strip()
        normalized = _normalize(clean)
        if not normalized or normalized in seen:
            continue
        # Uma preferencia que repete a persona (inteira ou em parte) nao
        # acrescenta nada e so infla o prefixo.
        if len(normalized) >= 16 and (
            normalized in persona or (persona and persona in normalized)
        ):
            continue
        seen.add(normalized)
        kept.append(f"- {clean}")
    if not kept:
        return ""
    body = "\n".join(kept)
    if len(body) > PREFERENCES_MAX_CHARS:
        cut = body[:PREFERENCES_MAX_CHARS]
        position = cut.rfind("\n")
        body = (cut[:position] if position > PREFERENCES_MAX_CHARS * 0.6 else cut).rstrip()
    return body


def compose_system(
    persona: Persona,
    preferences: str = "",
    *,
    concise: bool = True,
    short_prompt: bool = False,
    fast: bool = False,
    now: datetime | None = None,
) -> SystemPrompt:
    """Monta o system prompt: prefixo estavel primeiro, dinamico no fim."""
    persona_text = persona_block(persona)
    static_parts = [persona_text, ENVIRONMENT_NOTE]

    cleaned = clean_preferences(preferences, persona_text)
    if cleaned:
        static_parts.append(f"{PREFERENCES_HEADER}\n{cleaned}")

    if concise:
        static_parts.append(CONCISE_DIRECTIVE)

    dynamic_parts: list[str] = []
    if concise and short_prompt:
        dynamic_parts.append(SHORT_PROMPT_DIRECTIVE)
    if fast:
        dynamic_parts.append(FAST_DIRECTIVE)
    if now is not None:
        dynamic_parts.append(
            f"[Agora: {_WEEKDAYS[now.weekday()]}, "
            f"{now.strftime('%d/%m/%Y %H:%M')}]"
        )

    return SystemPrompt(
        static="\n\n---\n\n".join(static_parts),
        dynamic="\n\n".join(dynamic_parts),
    )


def initial_tools(persona_key: str) -> set[str]:
    """Conjunto inicial de ferramentas da sessao, conforme o modo."""
    names = set(BASE_TOOLS)
    if persona_key in EDITING_MODES:
        names.update(EDIT_TOOLS)
    return names


def tools_for_turn(
    persona_key: str, text: str, *, enabled: bool = True
) -> tuple[str, ...] | None:
    """Devolve so as ferramentas que este turno tem chance de usar.

    ``None`` significa "todas" (filtro desligado). Leitura e busca ficam
    sempre disponiveis; escrita e shell entram por modo ou por verbo de acao.
    O chamador deve acumular o resultado na sessao: assim o catalogo nao muda
    a cada turno e o cache do prefixo continua valendo.
    """
    if not enabled:
        return None
    names = initial_tools(persona_key)
    if ACTION_HINTS.search(text or ""):
        names.update(EDIT_TOOLS)
        names.update(SHELL_TOOLS)
    return tuple(name for name in ALL_TOOL_ORDER if name in names)


def route_model(text: str, fast_model: str, provider: str) -> ModelRef | None:
    """Escolhe o modelo pequeno apenas para pedidos curtos e simples."""
    value = (fast_model or "").strip()
    if not value:
        return None
    if len((text or "").split()) > 14:
        return None
    if HEAVY_TASK_HINTS.search(text or ""):
        return None
    if not FAST_TASK_HINTS.search(text or ""):
        return None
    if "/" in value:
        return ModelRef.parse(value)
    return ModelRef(provider_id=provider, model_id=value)
