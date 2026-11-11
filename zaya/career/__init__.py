"""Rotina semanal de vagas: busca, filtra, pontua e entrega no Telegram.

O metodo (fontes, filtros, rubrica) esta em ``~/SecondBrain/cv/playbook-vagas.md``
e o estado em ``<data_dir>/career/state.json``. Este pacote so mecaniza as duas
coisas de forma deterministica, sem LLM.
"""

from __future__ import annotations

from zaya.career.models import Job, ScoredJob
from zaya.career.service import (
    SearchResult,
    career_due,
    collect,
    compose_message,
    record_results,
    state_path,
)

__all__ = [
    "Job",
    "ScoredJob",
    "SearchResult",
    "career_due",
    "collect",
    "compose_message",
    "record_results",
    "state_path",
]
