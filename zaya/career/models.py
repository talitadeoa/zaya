"""Modelos de vaga e de vaga pontuada."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field


def normalize(text: object) -> str:
    """Minusculo, sem acento e com espacos colapsados (chave de comparacao)."""
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.casefold().split())


@dataclass(frozen=True, slots=True)
class Job:
    """Uma vaga crua, como veio da fonte. Sem link nao existe.

    ``location_kind`` diz o que o campo de local significa:
    ``"restriction"`` = onde a vaga aceita candidato (Remotive, Himalayas,
    Jobicy, Programathor); ``"office"`` = onde fica a empresa ou o escritorio
    (RemoteOK, Arbeitnow) e por isso nao restringe quem pode aplicar.
    """

    title: str
    company: str
    url: str
    source: str
    location: str = ""
    salary: str = ""
    seniority: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    remote: bool = False
    location_kind: str = "restriction"

    @property
    def key(self) -> tuple[str, str]:
        """Dedupe por empresa + cargo, como manda o playbook (nunca por link)."""
        return normalize(self.company), normalize(self.title)


@dataclass(frozen=True, slots=True)
class ScoredJob:
    """Vaga que passou nos filtros, com score de aderencia e o porque."""

    job: Job
    score: int
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str:
        return ", ".join(self.reasons) if self.reasons else "aderência parcial"
