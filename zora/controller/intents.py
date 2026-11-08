"""Deteccao deterministica de intencoes (sem depender do LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

REGISTER_RE = re.compile(
    r"^\s*(?:zora[,\s]+)?"
    r"(?:registre|registra|anote|anota|salve|salva|guarde|guarda|"
    r"crie\s+uma\s+nota(?:\s+sobre)?|cria\s+uma\s+nota(?:\s+sobre)?|nova\s+nota)"
    r"\b[:,]?\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)

PREFERENCE_RE = re.compile(
    r"^\s*(?:zora[,\s]+)?"
    r"(?:anote|anota|registre|registra|salve|salva|guarde|guarda)"
    r"\s+(?:isso\s+)?(?:a\s+|uma\s+)?(?:como\s+)?prefer[êe]ncia\s*[:,]?\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)

LEMBRAR_RE = re.compile(
    r"^\s*(?:zora[,\s]+)?lembre(?:-se)?\s+que\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)

TITLE_RE = re.compile(
    r"(?:chamad[ao]|titulad[ao]|t[íi]tulo|sobre)\s+[\"“']?(.+?)[\"”']?(?:[.;]|$)",
    re.IGNORECASE,
)

CONTEXT_KEYWORDS = re.compile(
    r"\b(projeto[s]?|decidim\w*|decis\w+|lembr\w+|mem[óo]ria|"
    r"second\s*brain|nota[s]?|ideia[s]?|reuni[ãa]o|reuni[õo]es|aprendi\w*|"
    r"hist[óo]rico|j[áa]\s+falamos|falamos\s+sobre)\b",
    re.IGNORECASE,
)

DECISION_RE = re.compile(r"\b(decid\w*|decis\w+|escolhemos|vamos\s+usar|adotamos)\b", re.I)
MEETING_RE = re.compile(r"\b(reuni[ãa]o|meeting|call|sincroniza\w+)\b", re.I)
LEARNING_RE = re.compile(r"\b(aprendi|aprendizado|descobri|li\s+que)\b", re.I)
IDEA_RE = re.compile(r"\b(ideia|insight|pensamento)\b", re.I)


@dataclass
class RegisterIntent:
    kind: str
    content: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    folder: str | None = None


def classify_kind(text: str) -> str:
    if DECISION_RE.search(text):
        return "decision"
    if MEETING_RE.search(text):
        return "meeting"
    if LEARNING_RE.search(text):
        return "learning"
    if IDEA_RE.search(text):
        return "idea"
    return "note"


def derive_title(content: str, max_words: int = 9) -> str:
    clean = re.sub(r"\s+", " ", content).strip()
    if not clean:
        return "Nota sem titulo"
    first = re.split(r"(?<=[.!?])\s+", clean, maxsplit=1)[0]
    words = first.split()
    title = " ".join(words[:max_words]).strip(" .,:;!?-")
    return (title or "Nota sem titulo")[:80]


def parse_register(text: str) -> RegisterIntent | None:
    match = REGISTER_RE.match(text)
    if not match:
        return None
    raw_content = match.group(1).strip()
    kind = classify_kind(raw_content)
    content = re.sub(
        r"^(?:a\s+|uma\s+|o\s+)?"
        r"(?:nota|ideia|decis[ãa]o|reuni[ãa]o|aprendizado)"
        r"\s*(?:de\s+que\s+|que\s+|[:,]\s*)?",
        "",
        raw_content,
        count=1,
        flags=re.I,
    ).strip()
    content = re.sub(r"^(?:que|o seguinte|isso|isto)\s*[:,]?\s*", "", content, flags=re.I)
    title = ""
    title_match = TITLE_RE.search(content)
    if title_match:
        title = title_match.group(1).strip().strip("\"'“”")
    quoted = re.match(r"^[\"“'](.+?)[\"”']\s*[:\-]?\s*(.*)$", content, re.S)
    if quoted:
        title = title or quoted.group(1).strip()
        content = quoted.group(2).strip() or quoted.group(1).strip()
    tags = ["zora", kind]
    inline_tags = re.findall(r"(?<!\w)#([\w/\-]+)", content)
    tags.extend(inline_tags)
    content = re.sub(r"(?<!\w)#[\w/\-]+", "", content).strip()
    return RegisterIntent(
        kind=kind,
        content=content,
        title=title or derive_title(content),
        tags=_dedupe(tags),
    )


def parse_preference(text: str) -> str | None:
    """Detecta pedidos explicitos para aprender uma preferencia (taste)."""
    match = PREFERENCE_RE.match(text)
    if match:
        clean = re.sub(r"\s+", " ", match.group(1)).strip().lstrip("-* ").strip()
        return clean or None

    match = LEMBRAR_RE.match(text)
    if match:
        remainder = re.sub(r"\s+", " ", match.group(1)).strip()
        if re.search(
            r"\b(prefiro|gosto|não gosto|nao gosto|evite|não quero|nao quero)\b",
            remainder,
            re.IGNORECASE,
        ):
            remainder = re.sub(r"^eu\s+", "", remainder, flags=re.IGNORECASE)
            return remainder[0].upper() + remainder[1:]
    return None


def needs_second_brain_context(text: str) -> bool:
    return bool(CONTEXT_KEYWORDS.search(text))


def format_context(
    hits, header: str = "## Contexto do Second Brain", max_chars: int = 1200
) -> str:
    if not hits:
        return ""
    lines = [header, ""]
    for hit in hits:
        lines.append(hit.as_context(max_chars))
    return "\n".join(lines)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result
