"""Filtros e rubrica de score, espelhando ``~/SecondBrain/cv/playbook-vagas.md``.

O playbook e a fonte do metodo; se a rubrica mudar la, mude aqui. Os pesos e as
listas ficam neste modulo (e nao em config) porque sao regra de negocio, nao
ajuste de ambiente.
"""

from __future__ import annotations

import re

from zaya.career.models import Job, ScoredJob, normalize

# --------------------------------------------------------------------- cargos
# Cargo alvo primario: suporte/TI. Um acerto aqui ja caracteriza a vaga.
PRIMARY_TITLES: tuple[str, ...] = (
    "suporte tecnico",
    "suporte ao usuario",
    "suporte ao cliente",
    "analista de suporte",
    "tecnico de suporte",
    "auxiliar de suporte",
    "assistente de suporte",
    "assistente de ti",
    "assistente de informatica",
    "analista de ti",
    "tecnico de ti",
    "tecnico de informatica",
    "tecnico de redes",
    "service desk",
    "help desk",
    "helpdesk",
    "it support",
    "technical support",
    "support analyst",
    "support engineer",
    "support specialist",
    "support technician",
    "application support",
    "customer support",
    "user support",
    "desktop support",
    "infraestrutura de ti",
    "infra analyst",
    "analista de infraestrutura",
)

# Cargo alvo secundario: dados/qualidade/automacao. Vale, mas nao e o foco.
SECONDARY_TITLES: tuple[str, ...] = (
    "data quality",
    "qualidade de dados",
    "quality analyst",
    "qa analyst",
    "quality assurance",
    "content quality",
    "content reviewer",
    "annotation",
    "anotacao",
    "rotulagem",
    "rater",
    "automacao",
    "automation analyst",
    "automation engineer",
    "automation specialist",
)

# Familia adjacente: nao e o cargo, mas esta perto o bastante para registrar.
WEAK_TITLES: tuple[str, ...] = (
    "atendimento",
    "tecnologia",
    "dados",
    "data",
)

# Siglas curtas que so valem como palavra inteira ('ti' nao pode casar 'ticket').
WEAK_WORDS_RE = re.compile(r"\b(?:ti|qa)\b")

# Tudo o que caracteriza o alvo (usado pelo filtro de descarte).
TARGET_TITLES: tuple[str, ...] = PRIMARY_TITLES + SECONDARY_TITLES

# Cargos que nunca sao o alvo, mesmo que o titulo cite "suporte" (ex.: suporte
# veterinario, suporte a vendas). Barra antes de pontuar.
OFF_TITLES: tuple[str, ...] = (
    "vendas",
    "vendedor",
    "sales",
    "marketing",
    "executivo de contas",
    "account executive",
    "gerente",
    "manager",
    "coordenador",
    "coordinator",
    "diretor",
    "director",
    "head of",
    "designer",
    "design",
    "enfermeir",
    "veterinar",
    "vétérinar",
    "medic",
    "advogad",
    "contador",
    "contabil",
    "motorista",
    "cozinheir",
    "professor",
    "docente",
    "estagio",
    "intern",
    "aprendiz",
)

# ----------------------------------------------------------------- competencias
# Vocabulario de requisitos e a competencia do perfil que cobre cada um.
# Um requisito sem mapeamento conta como descoberto (nao coberto).
REQUIREMENTS: dict[str, str] = {
    # suporte a usuarios e troubleshooting
    "suporte": "suporte",
    "troubleshooting": "suporte",
    "help desk": "suporte",
    "service desk": "suporte",
    "atendimento": "atendimento",
    "customer service": "atendimento",
    "sla": "suporte",
    "ticket": "suporte",
    "chamados": "suporte",
    # hardware, software e perifericos
    "hardware": "hardware",
    "software": "hardware",
    "manutencao": "hardware",
    "perifericos": "hardware",
    "impressora": "hardware",
    "notebook": "hardware",
    # Windows e redes
    "windows": "windows",
    "redes": "redes",
    "network": "redes",
    "tcp": "redes",
    "dhcp": "redes",
    "vpn": "redes",
    "active directory": "redes",
    "office 365": "windows",
    "microsoft 365": "windows",
    "pacote office": "windows",
    "excel": "windows",
    # ERP e sistemas corporativos
    "erp": "erp",
    "sap": "erp",
    "totvs": "erp",
    "protheus": "erp",
    "salesforce": "erp",
    # atendimento e comunicacao
    "comunicacao": "atendimento",
    "escrita": "atendimento",
    "portugues": "atendimento",
    "ingles": "atendimento",
    # dados
    "data quality": "dados",
    "qualidade de dados": "dados",
    "anotacao": "dados",
    "annotation": "dados",
    "rotulagem": "dados",
    "sql": "dados",
    "planilha": "dados",
    "dashboard": "dados",
    "indicadores": "dados",
    "power bi": "dados",
    "looker": "dados",
    # automacao
    "python": "automacao",
    "automacao": "automacao",
    "automation": "automacao",
    "script": "automacao",
    "rpa": "automacao",
    "n8n": "automacao",
    "zapier": "automacao",
}

# Requisitos que o perfil nao cobre. Basta aparecer para reduzir a nota.
GAPS: dict[str, str] = {
    "kubernetes": "k8s",
    "docker": "containers",
    "aws": "cloud",
    "azure": "cloud",
    "gcp": "cloud",
    "java": "java",
    "c#": "csharp",
    "c++": "cpp",
    "react": "react",
    "angular": "angular",
    "node": "node",
    "php": "php",
    "ruby": "ruby",
    "golang": "go",
    "rust": "rust",
    "scala": "scala",
    "kotlin": "kotlin",
    "swift": "swift",
    "machine learning": "ml",
    "deep learning": "ml",
    "tensorflow": "ml",
    "pytorch": "ml",
    "figma": "figma",
    "photoshop": "photoshop",
    "oracle": "oracle",
    "dynamics": "dynamics",
}

# ---------------------------------------------------------------- localizacao
# Natal/RN: "rn" sozinho casa dentro de "Bern", "Cairns" etc. Por isso o
# casamento e por palavra inteira e o "rn" exige contexto de cidade.
_NATAL_RE = re.compile(
    r"\b(?:natal|parnamirim|mossoro|mossoró|rio grande do norte)\b|\bnatal[\s,/]*r[-\s]?n\b|\brn\b",
    re.IGNORECASE,
)
_REMOTE = (
    "remoto",
    "remote",
    "worldwide",
    "anywhere",
    "global",
    "home office",
)
_BRAZIL = ("brasil", "brazil", "latam", "latin america", "south america", "americas")
# Locais que significam "aceita candidato de qualquer lugar" ou do Brasil.
# Qualquer outro local declarado numa vaga remota e tratado como restricao:
# lista fixa de paises envelhece e deixa passar Albania, Servia etc.
_GLOBAL = (
    "worldwide",
    "anywhere",
    "global",
    "remote",
    "remoto",
    "international",
    "any location",
    "home office",
)

# Senioridade
_SENIOR = ("senior", "sr", "especialista", "specialist iii", "lead", "principal", "staff")
_JUNIOR = ("junior", "jr", "trainee", "assistente", "auxiliar", "entry level", "entry-level")
_PLENO = ("pleno", "pl", "mid", "mid-level", "intermediate")

# Empresas reconheciveis: verificaveis, sem red flag conhecida.
KNOWN_COMPANIES: tuple[str, ...] = (
    "stefanini",
    "accenture",
    "ibm",
    "dell",
    "hp",
    "lenovo",
    "totvs",
    "tivit",
    "teleperformance",
    "concentrix",
    "algar",
    "claro",
    "vivo",
    "tim",
    "oi",
    "banco",
    "nubank",
    "itau",
    "bradesco",
    "santander",
    "telus",
    "turing",
    "outlier",
    "scale ai",
    "mercor",
    "micro1",
    "alignerr",
    "dataannotation",
    "canonical",
    "gitlab",
    "automattic",
)

RED_FLAGS = (
    "banco de talentos",
    "cadastre-se para o futuro",
    "cadastro reserva",
    "sem experiencia necessaria",
    "ganhe ate",
    "renda extra",
    "comissao",
)

_SALARY_FLOOR = 1700.0
_SALARY_GOOD = 2500.0

_YEARS_RE = re.compile(
    r"(?:minimo|mínimo|ao menos|pelo menos|no minimo|no mínimo)?\s*"
    r"(\d+)\s*\+?\s*anos?\s*(?:de\s+)?(?:de\s+)?experi[eê]ncia",
    re.IGNORECASE,
)
_SALARY_RE = re.compile(r"\d[\d.,]*")


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term in text]


def _has_term(text: str, terms: tuple[str, ...]) -> bool:
    """Casa termo no comeco de palavra, para nao achar 'rater' em 'berater'.

    O fim fica livre porque varias listas guardam radicais de genero
    ('enfermeir', 'veterinar', 'advogad').
    """
    return any(re.search(rf"\b{re.escape(term)}", text) for term in terms)


def _salary_value(job: Job) -> tuple[float | None, str]:
    """(menor valor monetario, moeda) do campo de salario, normalizado."""
    raw = normalize(job.salary)
    currency = ""
    if "r$" in raw or "brl" in raw:
        currency = "BRL"
    elif "us$" in raw or "usd" in raw or "$" in raw:
        currency = "USD"
    elif "eur" in raw or "€" in raw:
        currency = "EUR"
    cleaned_raw = raw.replace("r$", "").replace("us$", "").replace("$", "").replace("€", "")
    numbers = _SALARY_RE.findall(cleaned_raw)
    values: list[float] = []
    for number in numbers:
        # "2.800" ou "2,800" sao milhares; "15,50" e decimal.
        cleaned = number.replace(".", "").replace(",", ".")
        try:
            values.append(float(cleaned))
        except ValueError:  # pragma: no cover - regex garante numero
            continue
    if not values:
        return None, currency
    # Valor/hora nao e comparavel com piso mensal: ignora para nao rejeitar.
    if "/h" in raw or "hora" in raw or "hour" in raw or "/ano" in raw or "annual" in raw:
        return None, currency
    return min(values), currency


def _is_natal(place: str) -> bool:
    return bool(_NATAL_RE.search(place or ""))


def _foreign_restricted(job: Job) -> bool:
    """Vaga remota cujo local declarado restringe a um pais/regiao especifica.

    So e "global" o que esta em ``_GLOBAL`` ou no Brasil. Vaga que declara
    "Albania" ou "USA" restringe, mesmo que o pais nao esteja numa lista.
    """
    place = normalize(job.location)
    if not place:
        return False
    if _hits(place, _BRAZIL) or _hits(place, _GLOBAL):
        return False
    return True


def location_ok(job: Job) -> bool:
    """Aceita Natal/RN e remoto de qualquer lugar. Rejeita presencial fora do RN."""
    place = f"{job.location} {job.title}"
    if _is_natal(place):
        return True
    # Fonte que informa a sede: a vaga so e aceita se for remota de fato.
    if job.location_kind == "office":
        if job.remote:
            return True
        # Presencial declarada: so se o proprio texto disser remoto.
        return bool(_hits(normalize(job.location), _REMOTE)) or not job.location.strip()
    # Idioma nao e barreira (fala ingles e espanhol); restricao de pais entra
    # com score menor e o motivo na linha, para ela decidir.
    if job.remote:
        return True
    if _hits(normalize(job.location), _REMOTE):
        return True
    # Fonte sem local declarado: nao da para descartar com seguranca.
    return not job.location.strip()


def local_points(job: Job) -> int:
    place = f"{job.location} {job.title}"
    if _is_natal(place):
        return 15
    if job.location_kind == "office":
        return 15 if job.remote else 0
    if job.remote:
        # Remoto restrito a pais estrangeiro: perde 5 pontos (autorizacao).
        return 10 if _foreign_restricted(job) else 15
    return 0


def seniority_of(job: Job) -> str:
    """'junior', 'pleno', 'senior' ou '' quando nao informado."""
    text = normalize(f"{job.seniority} {job.title}")
    if _hits(text, _SENIOR):
        return "senior"
    if _hits(text, _JUNIOR):
        return "junior"
    if _hits(text, _PLENO):
        return "pleno"
    return ""


def seniority_points(job: Job) -> int:
    level = seniority_of(job)
    if level in {"junior", "pleno"}:
        return 10
    if level == "senior":
        return 0
    return 6


def title_points(job: Job) -> int:
    title = normalize(job.title)
    if _has_term(title, OFF_TITLES):
        return 0
    if _has_term(title, PRIMARY_TITLES):
        return 25
    if _has_term(title, SECONDARY_TITLES):
        return 15
    if _has_term(title, WEAK_TITLES) or WEAK_WORDS_RE.search(title):
        return 8
    return 0


def skill_points(job: Job) -> tuple[int, int, int]:
    """Devolve (pontos, requisitos cobertos, requisitos detectados)."""
    text = normalize(f"{job.title} {job.description} {' '.join(job.tags)}")
    covered = {name for term, name in REQUIREMENTS.items() if term in text}
    gaps = {name for term, name in GAPS.items() if term in text}
    total = len(covered) + len(gaps)
    if total == 0:
        # Sem requisito reconhecivel, nao da para medir: credito parcial.
        return 16, 0, 0
    ratio = len(covered) / total
    return round(ratio * 30), len(covered), total


def salary_points(job: Job) -> int:
    value, currency = _salary_value(job)
    if value is None:
        return 5
    # A faixa do playbook e em reais; outra moeda nao entra na comparacao.
    if currency and currency != "BRL":
        return 5
    if value >= _SALARY_GOOD:
        return 10
    if value >= _SALARY_FLOOR:
        return 6
    return 0


def company_points(job: Job) -> int:
    name = normalize(job.company)
    text = normalize(job.description)
    if not name or _hits(text, RED_FLAGS):
        return 0
    if any(known in name for known in KNOWN_COMPANIES):
        return 10
    return 5


def score_job(job: Job, *, known_keys: set[tuple[str, str]] | None = None) -> ScoredJob:
    """Pontua pela rubrica do playbook e explica o numero em poucas palavras."""
    title = title_points(job)
    skills, covered, total = skill_points(job)
    place = local_points(job)
    level = seniority_points(job)
    money = salary_points(job)
    company = company_points(job)
    score = title + skills + place + level + money + company

    reasons: list[str] = []
    if title >= 25:
        reasons.append("mesmo cargo")
    elif title >= 15:
        reasons.append("mesma família de cargo")
    elif title:
        reasons.append("cargo adjacente")
    if total:
        reasons.append(f"requisitos batem {covered}/{total}")
    if place == 15:
        reasons.append("Natal/RN" if _is_natal(f"{job.location} {job.title}") else "remoto")
    elif place == 10:
        reasons.append("remoto, mas restrito a outro país")
    level_name = seniority_of(job)
    if level_name in {"junior", "pleno"}:
        reasons.append(level_name)
    if money == 10:
        reasons.append("salário dentro da faixa")
    if known_keys and job.key in known_keys:
        reasons.append("já registrada")
    return ScoredJob(job=job, score=max(0, min(100, score)), reasons=tuple(reasons))


# ------------------------------------------------------------------- filtros
def _years_required(job: Job) -> int | None:
    text = f"{job.title} {job.description}"
    match = _YEARS_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:  # pragma: no cover - regex garante digito
        return None


def reject_reason(job: Job) -> str:
    """Motivo de descarte, ou '' se a vaga passa nos filtros do playbook."""
    if not job.url.strip():
        return "sem link"
    if not job.title.strip():
        return "sem cargo"
    if not job.company.strip():
        return "sem empresa"
    title = normalize(job.title)
    if _hits(title, OFF_TITLES):
        return "fora do perfil"
    if not _has_term(title, TARGET_TITLES):
        return "cargo fora do alvo"
    if not location_ok(job):
        return "presencial fora do RN"
    level = seniority_of(job)
    if level == "senior":
        return "sênior"
    years = _years_required(job)
    if years is not None and years >= 3:
        return f"exige {years}+ anos"
    text = normalize(job.description)
    if any(flag in text for flag in RED_FLAGS):
        return "red flag"
    value, currency = _salary_value(job)
    if value is not None and (not currency or currency == "BRL") and value < _SALARY_FLOOR:
        return "salário abaixo do piso"
    return ""


def key_of(job: Job) -> tuple[str, str]:
    return job.key


__all__ = [
    "GAPS",
    "KNOWN_COMPANIES",
    "OFF_TITLES",
    "PRIMARY_TITLES",
    "REQUIREMENTS",
    "SECONDARY_TITLES",
    "TARGET_TITLES",
    "WEAK_TITLES",
    "company_points",
    "key_of",
    "local_points",
    "location_ok",
    "reject_reason",
    "salary_points",
    "score_job",
    "seniority_of",
    "seniority_points",
    "skill_points",
    "title_points",
]
