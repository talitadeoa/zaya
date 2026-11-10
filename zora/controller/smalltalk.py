"""Respostas locais instantaneas para conversa trivial (sem LLM).

Saudacoes, "tudo bem?" e agradecimentos nao precisam de uma ida ao OpenCode.
Aqui a Zaia responde na hora, mantendo o tom caloroso, e qualquer mensagem com
conteudo real (pedido, duvida, tarefa) continua indo para o agente.
"""

from __future__ import annotations

import random
import re
import unicodedata
from datetime import datetime

from zora.controller.proactive import greeting_for

# Mensagens que sao SOMENTE saudacao (texto normalizado, sem acentos/pontuacao).
_GREETING = {
    "oi",
    "oie",
    "ola",
    "ei",
    "hey",
    "hi",
    "hello",
    "opa",
    "eai",
    "e ai",
    "eae",
    "salve",
    "bom dia",
    "boa tarde",
    "boa noite",
    "oi bom dia",
    "oi boa tarde",
    "oi boa noite",
    "ola bom dia",
    "ola boa tarde",
    "ola boa noite",
}

# "Como vai voce?" e variacoes.
_HOW_ARE_YOU = {
    "tudo bem",
    "tudo bom",
    "tudo certo",
    "tudo tranquilo",
    "tudo joia",
    "tudo bem com voce",
    "tudo bom com voce",
    "tudo bem com vc",
    "tudo bom com vc",
    "tudo bem vc",
    "como vai",
    "como vai voce",
    "como voce esta",
    "como voce ta",
    "como vc ta",
    "como vc esta",
    "como ta",
    "como ta voce",
    "como esta",
    "como estas",
    "beleza",
    "de boa",
    "blz",
    "tranquilo",
}

# Saudacao seguida de "tudo bem?" no mesmo turno.
_GREETING_AND_HOW = {
    "oi tudo bem",
    "oi tudo bom",
    "ola tudo bem",
    "ola tudo bom",
    "bom dia tudo bem",
    "boa tarde tudo bem",
    "boa noite tudo bem",
    "e ai tudo bem",
    "opa tudo bem",
    "oi e ai",
}

_THANKS = {
    "obrigada",
    "obrigado",
    "valeu",
    "vlw",
    "muito obrigada",
    "muito obrigado",
    "brigada",
    "brigado",
    "agradeco",
    "thanks",
}

_IDENTITY = {
    "quem e voce",
    "quem e vc",
    "qual seu nome",
    "seu nome",
    "voce e quem",
    "o que voce faz",
    "o que vc faz",
}

_TIME = {
    "que horas sao",
    "que hora e",
    "que hora sao",
    "qual a hora",
    "que horas",
    "hora agora",
    "horas agora",
    "me diz a hora",
    "me fala a hora",
}

_DATE = {
    "que dia e hoje",
    "qual e a data",
    "qual a data",
    "data de hoje",
    "que dia estamos",
    "hoje e que dia",
    "qual e o dia de hoje",
}

_WEEKDAYS = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]

_MONTHS = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]

_GREETING_TAILS = [
    "Como posso ajudar?",
    "Tô por aqui. Como posso ajudar?",
    "O que você precisa?",
    "Em que posso ajudar?",
]

_HOW_TAILS = [
    "Tudo ótimo por aqui! E você?",
    "Tudo bem! E aí, como você está?",
    "Tudo tranquilo por aqui. E você?",
]

_THANKS_TAILS = [
    "De nada! Qualquer coisa, é só chamar.",
    "Por nada! Tô aqui se precisar.",
    "Imagina! Qualquer coisa, só falar.",
]

_MIRROR_GREETINGS = {
    "bom dia": "Bom dia",
    "boa tarde": "Boa tarde",
    "boa noite": "Boa noite",
}


def _normalize(text: str) -> str:
    """Minusculas, sem acentos/pontuacao, sem o nome "zaia" nas pontas."""
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^zaia\b\s*", "", value)
    value = re.sub(r"\s*\bzaia$", "", value)
    return value.strip()


def _is_pure_smalltalk(normalized: str) -> bool:
    """So responde localmente quando a mensagem inteira e conversa trivial."""
    if not normalized:
        return False
    if len(normalized.split()) > 6:
        return False
    return (
        normalized in _GREETING
        or normalized in _HOW_ARE_YOU
        or normalized in _GREETING_AND_HOW
        or normalized in _THANKS
        or normalized in _IDENTITY
        or normalized in _TIME
        or normalized in _DATE
    )


def _mirrored_greeting(normalized: str, now: datetime) -> str:
    for phrase, label in _MIRROR_GREETINGS.items():
        if phrase in normalized:
            return label
    return greeting_for(now)


def fast_reply(
    text: str,
    now: datetime | None = None,
    name: str = "",
) -> str | None:
    """Devolve uma resposta pronta para small talk, ou None para o agente.

    O casamento e pela mensagem inteira: "oi" responde na hora, mas
    "oi, preciso de ajuda com o projeto" segue para o OpenCode.
    """
    normalized = _normalize(text)
    if not _is_pure_smalltalk(normalized):
        return None

    now = now or datetime.now().astimezone()
    nome = f", {name.strip()}" if name and name.strip() else ""

    if normalized in _TIME:
        return f"Agora são {now.strftime('%H:%M')}."

    if normalized in _DATE:
        weekday = _WEEKDAYS[now.weekday()]
        month = _MONTHS[now.month - 1]
        return f"Hoje é {weekday}, {now.day} de {month} de {now.year}."

    if normalized in _IDENTITY:
        return (
            "Sou a Zaia, sua assistente pessoal. "
            "Cuido de agenda, memória e projetos junto com você."
        )

    if normalized in _THANKS:
        return random.choice(_THANKS_TAILS)

    if normalized in _GREETING_AND_HOW:
        greet = _mirrored_greeting(normalized, now)
        return f"{greet}{nome}! Tudo ótimo por aqui. E você?"

    if normalized in _HOW_ARE_YOU:
        return random.choice(_HOW_TAILS)

    greet = _mirrored_greeting(normalized, now)
    return f"{greet}{nome}! {random.choice(_GREETING_TAILS)}"
