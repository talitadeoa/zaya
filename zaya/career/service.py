"""Estado, orquestracao e mensagem da rotina semanal de vagas.

Fluxo do playbook: buscar -> filtrar -> deduplicar -> pontuar -> registrar ->
compor a mensagem. Nada aqui chama o modelo; o texto sai determinístico.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from zaya.career.models import Job, ScoredJob, normalize
from zaya.career.score import reject_reason, score_job
from zaya.career.sources import SOURCES, Source, fetch_all

_WEEKDAYS = (
    "segunda",
    "terça",
    "quarta",
    "quinta",
    "sexta",
    "sábado",
    "domingo",
)


# --------------------------------------------------------------------- estado
def state_path(data_dir: Path | str, explicit: str = "") -> Path:
    """Onde vive o ``state.json`` (perfil + vagas + historico)."""
    if explicit.strip():
        return Path(explicit).expanduser()
    return Path(data_dir).expanduser() / "career" / "state.json"


def load_state(path: Path | str) -> dict[str, Any]:
    """Le o estado; devolve um esqueleto valido se o arquivo nao existir."""
    path = Path(path).expanduser()
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"perfil": {}, "vagas": [], "updated_at": ""}
    except json.JSONDecodeError as exc:
        raise ValueError(f"state.json inválido em {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"state.json deveria ser um objeto em {path}")
    data.setdefault("vagas", [])
    if not isinstance(data["vagas"], list):
        raise ValueError(f"state.json: 'vagas' deveria ser uma lista em {path}")
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Grava de forma atomica: um crash no meio nao corrompe o estado."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def known_keys(state: dict[str, Any]) -> set[tuple[str, str]]:
    """Chaves (empresa, cargo) ja registradas — o dedupe real e este."""
    keys: set[tuple[str, str]] = set()
    for item in state.get("vagas") or []:
        if not isinstance(item, dict):
            continue
        company = normalize(item.get("empresa"))
        title = normalize(item.get("cargo"))
        if company and title:
            keys.add((company, title))
    return keys


def _next_id(state: dict[str, Any]) -> int:
    maior = 0
    for item in state.get("vagas") or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("id") or "")
        if raw.startswith("vaga-"):
            try:
                maior = max(maior, int(raw.removeprefix("vaga-")))
            except ValueError:
                continue
    return maior + 1


def _entry(vaga: ScoredJob, number: int) -> dict[str, Any]:
    job = vaga.job
    return {
        "id": f"vaga-{number:03d}",
        "cargo": job.title,
        "empresa": job.company,
        "local": job.location,
        "remoto": job.remote,
        "local_tipo": job.location_kind,
        "salario": job.salary or None,
        "fonte": job.source,
        "link": job.url,
        "status": "nova",
        "entrega": "pendente",
        "score": vaga.score,
        "motivo": vaga.reason,
        "encontrada_em": date.today().isoformat(),
    }


def is_pending(item: dict[str, Any]) -> bool:
    """Vaga registrada pela rotina e ainda nao entregue no Telegram.

    Entradas manuais antigas nao tem ``score`` nem ``entrega`` — nunca foram
    para o Telegram por esta rotina e ficam de fora. Ja as vagas pontuadas
    sem o campo (gravadas antes da marca de entrega existir) contam como
    pendentes: e o que evita perde-las quando o envio falha.
    """
    if item.get("status", "nova") != "nova":
        return False
    if item.get("entrega") == "entregue":
        return False
    return item.get("score") is not None


# ----------------------------------------------------------------- a rodada
@dataclass
class SearchResult:
    """O que a rodada produziu, antes de virar mensagem."""

    novas: list[ScoredJob] = field(default_factory=list)
    descartadas: list[tuple[Job, str]] = field(default_factory=list)
    conhecidas: int = 0
    duplicadas: int = 0
    falhas: list[str] = field(default_factory=list)
    bruto: int = 0

    @property
    def total(self) -> int:
        return len(self.novas)


async def collect(
    state: dict[str, Any],
    *,
    sources: Sequence[tuple[str, Source]] = SOURCES,
) -> SearchResult:
    """Busca todas as fontes e devolve so o que e novo e passa nos filtros."""
    jobs, falhas = await fetch_all(sources=tuple(sources))
    result = SearchResult(falhas=falhas, bruto=len(jobs))

    seen = known_keys(state)
    batch: set[tuple[str, str]] = set()
    for job in jobs:
        if job.key in seen:
            result.conhecidas += 1
            continue
        if job.key in batch:
            result.duplicadas += 1
            continue
        motivo = reject_reason(job)
        if motivo:
            result.descartadas.append((job, motivo))
            continue
        batch.add(job.key)
        result.novas.append(score_job(job, known_keys=seen))

    result.novas.sort(key=lambda vaga: (-vaga.score, normalize(vaga.job.title)))
    return result


def record_results(state: dict[str, Any], result: SearchResult) -> list[dict[str, Any]]:
    """Anota as vagas novas no estado e devolve os registros criados."""
    vagas = state.setdefault("vagas", [])
    number = _next_id(state)
    criadas: list[dict[str, Any]] = []
    for vaga in result.novas:
        entry = _entry(vaga, number)
        number += 1
        vagas.append(entry)
        criadas.append(entry)
    state["updated_at"] = date.today().isoformat()
    return criadas


def career_due(
    now_hm: str,
    at: str,
    until: str,
    weekday: int,
    days: Iterable[int],
) -> bool:
    """Diz se a rotina deve sair agora (0 = segunda)."""
    if weekday not in set(days):
        return False
    return at <= now_hm < until


def _money(text: str) -> str:
    return text.strip()


def _line(index: int, job: Job, score: int, reason: str) -> str:
    if job.remote:
        where = "remoto"
        if job.location.strip() and job.location_kind == "restriction":
            where = f"remoto · {job.location.strip()}"
    else:
        where = job.location.strip() or "local não informado"
    parts = [where]
    if job.salary.strip():
        parts.append(_money(job.salary))
    parts.append(f"score {score}")
    return "\n".join(
        [
            f"{index}. {job.title} — {job.company}",
            f"   {' · '.join(parts)}",
            f"   {job.url}",
            f"   Por quê: {reason}.",
        ]
    )


def job_line(index: int, vaga: ScoredJob) -> str:
    return _line(index, vaga.job, vaga.score, vaga.reason)


def entry_job(item: dict[str, Any]) -> Job:
    """Reconstroi a vaga a partir do que ficou gravado no estado."""
    return Job(
        title=str(item.get("cargo") or ""),
        company=str(item.get("empresa") or ""),
        url=str(item.get("link") or ""),
        source=str(item.get("fonte") or ""),
        location=str(item.get("local") or ""),
        salary=str(item.get("salario") or ""),
        remote=bool(item.get("remoto")),
        location_kind=str(item.get("local_tipo") or "restriction"),
    )


def entry_line(index: int, item: dict[str, Any]) -> str:
    score = int(item.get("score") or 0)
    return _line(index, entry_job(item), score, str(item.get("motivo") or "aderência parcial"))


def pending_jobs(state: dict[str, Any], *, limit: int = 0) -> list[dict[str, Any]]:
    """Vagas registradas e ainda nao entregues, da maior nota para a menor."""
    pendentes = [
        item for item in state.get("vagas") or [] if isinstance(item, dict) and is_pending(item)
    ]
    pendentes.sort(key=lambda item: (-int(item.get("score") or 0), normalize(item.get("cargo"))))
    return pendentes[:limit] if limit > 0 else pendentes


def mark_delivered(state: dict[str, Any], ids: Iterable[str], *, when: str = "") -> int:
    """Marca como entregues no Telegram; so isso libera o reenvio."""
    stamp = when or datetime.now().astimezone().isoformat(timespec="seconds")
    alvo = set(ids)
    marcadas = 0
    for item in state.get("vagas") or []:
        if not isinstance(item, dict) or item.get("id") not in alvo:
            continue
        item["entrega"] = "entregue"
        item["entregue_em"] = stamp
        marcadas += 1
    return marcadas


def compose_message(
    state: dict[str, Any],
    *,
    result: SearchResult | None = None,
    day: date | None = None,
    limit: int = 8,
) -> str:
    """Mensagem curta do Telegram, montada a partir do que falta entregar."""
    day = day or date.today()
    pendentes = pending_jobs(state)
    if not pendentes:
        lines = [f"🔎 Vagas — {_WEEKDAYS[day.weekday()]}, {day:%d/%m}", ""]
        lines.append("Sem vagas novas esta semana.")
        if result is not None and result.bruto:
            lines.append(f"Olhei {result.bruto} vagas — nenhuma passou nos filtros.")
        if result is not None and result.falhas:
            lines.append("Fonte com problema: " + ", ".join(result.falhas) + ".")
        return "\n".join(lines)

    lines = [
        f"🔎 Vagas novas — {len(pendentes)} encontrada{'s' if len(pendentes) > 1 else ''}",
        f"{_WEEKDAYS[day.weekday()]}, {day:%d/%m}",
        "",
    ]
    for index, item in enumerate(pendentes[:limit], start=1):
        lines.append(entry_line(index, item))
        lines.append("")
    resto = len(pendentes) - limit
    if resto > 0:
        lines.append(f"+{resto} com score menor registradas no estado.")
        lines.append("")
    if result is not None and result.falhas:
        lines.append("Fonte com problema: " + ", ".join(result.falhas) + ".")
        lines.append("")
    lines.append("Nenhuma aplicada ainda. Quer que eu prepare o CV da primeira?")
    return "\n".join(lines).rstrip() + "\n"


def summary_line(result: SearchResult) -> str:
    """Uma linha para o log/CLI."""
    return (
        f"{result.bruto} vagas lidas · {result.total} novas · "
        f"{len(result.descartadas)} descartadas · {result.duplicadas} repetidas · "
        f"{result.conhecidas} já registradas"
    )


def mark_applied(state: dict[str, Any], job_id: str, *, when: str = "") -> bool:
    """Marca uma vaga como aplicada (o envio em si e sempre da Talita)."""
    stamp = when or datetime.now().astimezone().isoformat(timespec="seconds")
    for item in state.get("vagas") or []:
        if isinstance(item, dict) and item.get("id") == job_id:
            item["status"] = "aplicada"
            item["aplicada_em"] = stamp
            return True
    return False


__all__ = [
    "SearchResult",
    "career_due",
    "collect",
    "compose_message",
    "entry_line",
    "is_pending",
    "job_line",
    "known_keys",
    "load_state",
    "mark_applied",
    "mark_delivered",
    "pending_jobs",
    "record_results",
    "save_state",
    "state_path",
    "summary_line",
]
