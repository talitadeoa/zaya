"""Clientes das fontes de vaga abertas e raspaveis.

Cada funcao devolve ``list[Job]`` e nao tenta ser esperta: quem filtra e pontua
e o ``service``. Falha de uma fonte nao derruba a rodada; o chamador isola com
``asyncio.gather(..., return_exceptions=True)``.

Cobertura (testada em nov/2026) e limitada ao que e publico: Remotive,
Himalayas, Jobicy, Arbeitnow, RemoteOK e Programathor. Gupy, Indeed e LinkedIn
bloqueiam automacao e ficam de fora de proposito.
"""

from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Awaitable, Callable

import httpx

from zaya.career.models import Job

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Zaya/0.1 (+pessoal)"
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

Source = Callable[[httpx.AsyncClient], Awaitable[list[Job]]]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _text(value: object) -> str:
    """Campo curto sem HTML nem entidade (titulo, empresa, local)."""
    return _WS_RE.sub(" ", html.unescape(str(value or ""))).strip()


def clean_html(text: str, limit: int = 900) -> str:
    """Texto plano curto a partir do HTML da descricao."""
    value = _TAG_RE.sub(" ", text or "")
    value = html.unescape(value)
    value = _WS_RE.sub(" ", value).strip()
    return value[:limit]


def _money(value: float | int | None) -> str:
    if not value:
        return ""
    return f"{float(value):,.0f}".replace(",", ".")


_PERIOD_PT = {
    "year": "ano",
    "yearly": "ano",
    "annual": "ano",
    "annually": "ano",
    "month": "mês",
    "monthly": "mês",
    "hour": "hora",
    "hourly": "hora",
    "day": "dia",
    "daily": "dia",
}


def _salary(
    minimum: float | int | None,
    maximum: float | int | None,
    currency: str,
    *,
    period: str = "",
) -> str:
    """Faixa salarial legivel; ``period`` diz 'ano'/'mes' quando a fonte sabe.

    Sem o periodo, valor anual de fonte gringa e lido como mensal e a vaga
    parece pagar 10x mais do que paga.
    """
    low, high = _money(minimum), _money(maximum)
    if low and high:
        body = f"{low} a {high}"
    else:
        body = low or high
    if not body:
        return ""
    text = f"{currency} {body}".strip() if currency else body
    if period:
        period = _PERIOD_PT.get(period.casefold(), period)
        return f"{text}/{period}"
    return text


async def remotive(client: httpx.AsyncClient, search: str = "", limit: int = 50) -> list[Job]:
    response = await client.get(
        "https://remotive.com/api/remote-jobs",
        params={"search": search, "limit": str(limit)},
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    jobs: list[Job] = []
    for item in payload.get("jobs") or []:
        url = _text(item.get("url"))
        title = _text(item.get("title"))
        if not url or not title:
            continue
        jobs.append(
            Job(
                title=title,
                company=_text(item.get("company_name")),
                url=url,
                source="Remotive",
                location=_text(item.get("candidate_required_location")),
                salary=_text(item.get("salary")),
                description=clean_html(str(item.get("description") or "")),
                tags=tuple(str(tag) for tag in item.get("tags") or []),
                remote=True,
            )
        )
    return jobs


async def himalayas(client: httpx.AsyncClient, limit: int = 50) -> list[Job]:
    response = await client.get(
        "https://himalayas.app/jobs/api",
        params={"limit": str(limit)},
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    jobs: list[Job] = []
    for item in payload.get("jobs") or []:
        url = str(item.get("applicationLink") or item.get("guid") or "").strip()
        title = _text(item.get("title"))
        if not url or not title:
            continue
        seniority = ", ".join(str(level) for level in item.get("seniority") or [])
        places = item.get("locationRestrictions") or []
        jobs.append(
            Job(
                title=title,
                company=_text(item.get("companyName")),
                url=url,
                source="Himalayas",
                location=", ".join(str(place) for place in places),
                salary=_salary(
                    item.get("minSalary"),
                    item.get("maxSalary"),
                    _text(item.get("currency")),
                    period=_text(item.get("salaryPeriod")),
                ),
                seniority=seniority,
                description=clean_html(str(item.get("excerpt") or item.get("description") or "")),
                tags=tuple(str(tag) for tag in item.get("parentCategories") or []),
                remote=True,
            )
        )
    return jobs


async def jobicy(client: httpx.AsyncClient, count: int = 50, geo: str = "brazil") -> list[Job]:
    response = await client.get(
        "https://jobicy.com/api/v2/remote-jobs",
        params={"count": str(count), "geo": geo},
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    jobs: list[Job] = []
    for item in payload.get("jobs") or []:
        url = _text(item.get("url"))
        title = _text(item.get("jobTitle"))
        if not url or not title:
            continue
        jobs.append(
            Job(
                title=title,
                company=_text(item.get("companyName")),
                url=url,
                source="Jobicy",
                location=_text(item.get("jobGeo")),
                seniority=_text(item.get("jobLevel")),
                description=clean_html(
                    str(item.get("jobExcerpt") or item.get("jobDescription") or "")
                ),
                tags=tuple(str(tag) for tag in item.get("jobIndustry") or []),
                remote=True,
            )
        )
    return jobs


async def arbeitnow(client: httpx.AsyncClient, pages: int = 2) -> list[Job]:
    jobs: list[Job] = []
    for page in range(1, max(1, pages) + 1):
        response = await client.get(
            "https://www.arbeitnow.com/api/job-board-api",
            params={"page": str(page)},
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        for item in response.json().get("data") or []:
            url = _text(item.get("url"))
            title = _text(item.get("title"))
            if not url or not title:
                continue
            jobs.append(
                Job(
                    title=title,
                    company=_text(item.get("company_name")),
                    url=url,
                    source="Arbeitnow",
                    location=_text(item.get("location")),
                    description=clean_html(str(item.get("description") or "")),
                    tags=tuple(str(tag) for tag in item.get("tags") or []),
                    remote=bool(item.get("remote")),
                    location_kind="office",
                )
            )
    return jobs


async def remoteok(client: httpx.AsyncClient, limit: int = 80) -> list[Job]:
    response = await client.get("https://remoteok.com/api", headers={"User-Agent": _USER_AGENT})
    response.raise_for_status()
    payload = response.json()
    # O primeiro item do array e um aviso legal, nao uma vaga.
    jobs: list[Job] = []
    for item in payload[1 : limit + 1]:
        url = _text(item.get("url"))
        title = _text(item.get("position"))
        if not url or not title:
            continue
        jobs.append(
            Job(
                title=title,
                company=_text(item.get("company")),
                url=url,
                source="RemoteOK",
                location=_text(item.get("location")),
                salary=_salary(item.get("salary_min"), item.get("salary_max"), "USD", period="ano"),
                description=clean_html(str(item.get("description") or "")),
                tags=tuple(str(tag) for tag in item.get("tags") or []),
                remote=True,
                location_kind="office",
            )
        )
    return jobs


_CELL_RE = re.compile(r'<a href="(/jobs/[^"]+)".*?</a>', re.S)
_TITLE_RE = re.compile(r'class="text-24[^"]*">([^<]+)</h3>', re.S)
_META_RE = re.compile(r"<span><i class='[^']*'></i>([^<]+)</span>", re.S)


async def programathor(client: httpx.AsyncClient) -> list[Job]:
    response = await client.get(
        "https://programathor.com.br/jobs", headers={"User-Agent": _USER_AGENT}
    )
    response.raise_for_status()
    page = response.text
    jobs: list[Job] = []
    for match in _CELL_RE.finditer(page):
        href = match.group(1)
        block = match.group(0)
        title_match = _TITLE_RE.search(block)
        if not title_match:
            continue
        title = html.unescape(_WS_RE.sub(" ", title_match.group(1))).strip()
        # Metadados na ordem do card: empresa, local, porte, senioridade, vinculo.
        meta = [html.unescape(part).strip() for part in _META_RE.findall(block)]
        company = meta[0] if meta else ""
        location = meta[1] if len(meta) > 1 else ""
        seniority = meta[3] if len(meta) > 3 else ""
        jobs.append(
            Job(
                title=title,
                company=company,
                url=f"https://programathor.com.br{href}",
                source="Programathor",
                location=location,
                seniority=seniority,
                remote="remoto" in location.casefold(),
            )
        )
    return jobs


SOURCES: tuple[tuple[str, Source], ...] = (
    ("Remotive", lambda client: remotive(client)),
    ("Himalayas", lambda client: himalayas(client)),
    ("Jobicy", lambda client: jobicy(client)),
    ("Arbeitnow", lambda client: arbeitnow(client)),
    ("RemoteOK", lambda client: remoteok(client)),
    ("Programathor", lambda client: programathor(client)),
)


async def fetch_all(
    sources: tuple[tuple[str, Source], ...] = SOURCES,
) -> tuple[list[Job], list[str]]:
    """Busca todas as fontes em paralelo; devolve as vagas e as falhas."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(source(client) for _, source in sources), return_exceptions=True
        )
    jobs: list[Job] = []
    failed: list[str] = []
    for (name, _), result in zip(sources, results, strict=False):
        if isinstance(result, BaseException):
            failed.append(f"{name}: {type(result).__name__}")
            continue
        jobs.extend(result)
    return jobs, failed


__all__ = [
    "SOURCES",
    "Source",
    "arbeitnow",
    "clean_html",
    "fetch_all",
    "himalayas",
    "jobicy",
    "programathor",
    "remoteok",
    "remotive",
]
