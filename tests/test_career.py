from __future__ import annotations

import json
from datetime import date

import pytest

from zaya.career.models import Job, ScoredJob, normalize
from zaya.career.score import (
    company_points,
    local_points,
    location_ok,
    reject_reason,
    salary_points,
    score_job,
    seniority_of,
    title_points,
)
from zaya.career.service import (
    SearchResult,
    career_due,
    collect,
    compose_message,
    known_keys,
    load_state,
    mark_applied,
    record_results,
    save_state,
    state_path,
    summary_line,
)
from zaya.career.sources import clean_html

DAY = date(2026, 11, 9)  # segunda-feira


def job(**overrides) -> Job:
    base = {
        "title": "Analista de Suporte N1",
        "company": "Stefanini",
        "url": "https://exemplo.test/vaga/1",
        "source": "Remotive",
        "location": "Brazil",
        "salary": "R$ 2.800",
        "seniority": "Júnior",
        "description": "Atendimento a usuários, troubleshooting, Windows e redes.",
        "tags": ("suporte",),
        "remote": True,
    }
    base.update(overrides)
    return Job(**base)


# --------------------------------------------------------------------- estado
def test_state_path_usa_data_dir_por_padrao(tmp_path):
    assert state_path(tmp_path) == tmp_path / "career" / "state.json"
    assert state_path(tmp_path, "~/x.json") == __import__("pathlib").Path("~/x.json").expanduser()


def test_load_state_sem_arquivo_devolve_esqueleto(tmp_path):
    state = load_state(tmp_path / "nao-existe.json")

    assert state["vagas"] == []
    assert state["perfil"] == {}


def test_load_state_rejeita_json_invalido(tmp_path):
    path = tmp_path / "ruim.json"
    path.write_text("{nada", encoding="utf-8")

    with pytest.raises(ValueError, match="inválido"):
        load_state(path)


def test_valor_inesperado_em_vagas_falha(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"vagas": "nao e lista"}), encoding="utf-8")

    with pytest.raises(ValueError, match="lista"):
        load_state(path)


def test_save_state_grava_de_forma_atomica(tmp_path):
    path = tmp_path / "career" / "state.json"
    save_state(path, {"vagas": [{"id": "vaga-001"}], "updated_at": "2026-11-09"})

    assert json.loads(path.read_text(encoding="utf-8"))["vagas"][0]["id"] == "vaga-001"
    assert not (tmp_path / "career" / "state.json.tmp").exists()


def test_known_keys_deduplica_por_empresa_e_cargo_sem_acento():
    state = {
        "vagas": [
            {"empresa": "Stefanini", "cargo": "Analista de Suporte"},
            {"empresa": "", "cargo": "Ignorada"},
            {"empresa": "Acme", "cargo": "Técnico"},
        ]
    }

    assert known_keys(state) == {("stefanini", "analista de suporte"), ("acme", "tecnico")}


def test_record_results_numera_e_preserva_status():
    state = {"vagas": [{"id": "vaga-003"}, {"id": "outro"}]}
    result = SearchResult(novas=[score_job(job())])

    criadas = record_results(state, result)

    assert criadas[0]["id"] == "vaga-004"
    assert criadas[0]["status"] == "nova"
    assert criadas[0]["entrega"] == "pendente"
    assert criadas[0]["link"] == "https://exemplo.test/vaga/1"
    assert len(state["vagas"]) == 3
    assert state["updated_at"] == str(date.today())


def test_mark_applied_marca_so_o_id_pedido():
    state = {
        "vagas": [
            {"id": "vaga-001", "status": "nova"},
            {"id": "vaga-002", "status": "nova"},
        ]
    }

    assert mark_applied(state, "vaga-001", when="2026-11-09T10:00:00") is True
    assert state["vagas"][0]["status"] == "aplicada"
    assert state["vagas"][0]["aplicada_em"] == "2026-11-09T10:00:00"
    assert state["vagas"][1]["status"] == "nova"
    assert mark_applied(state, "vaga-99") is False


# ---------------------------------------------------------------------- score
def test_normalize_tira_acento_e_caixa():
    assert normalize("  Técnico de Suporte ") == "tecnico de suporte"


def test_title_points_reconhece_alvo_adjacente_e_estranho():
    assert title_points(job(title="Analista de Suporte N1")) == 25
    assert title_points(job(title="Automation Analyst")) == 15
    assert title_points(job(title="Atendimento ao cliente")) == 8
    assert title_points(job(title="Designer Gráfico")) == 0


def test_seniority_of_le_titulo_e_campo():
    assert seniority_of(job(seniority="", title="Suporte Júnior")) == "junior"
    assert seniority_of(job(seniority="Pleno", title="Suporte")) == "pleno"
    assert seniority_of(job(seniority="Senior", title="Suporte")) == "senior"
    assert seniority_of(job(seniority="", title="Suporte")) == ""


def test_salary_points_respeita_piso_e_teto():
    assert salary_points(job(salary="R$ 3.000")) == 10
    assert salary_points(job(salary="R$ 2.000")) == 6
    assert salary_points(job(salary="R$ 1.200")) == 0
    assert salary_points(job(salary="")) == 5
    assert salary_points(job(salary="USD 15/h")) == 5


def test_company_points_premia_conhecida_e_pune_red_flag():
    assert company_points(job(company="Grupo Stefanini")) == 10
    assert company_points(job(company="Loja X", description="banco de talentos")) == 0
    assert company_points(job(company="Loja Y")) == 5


def test_local_points_e_location_ok():
    assert local_points(job(location="Natal, RN")) == 15
    assert local_points(job(location="Brazil")) == 15
    assert local_points(job(location="Lisboa", remote=False)) == 0
    assert location_ok(job(location="Lisboa", remote=False)) is False
    assert location_ok(job(location="", remote=False)) is True


def test_score_job_soma_a_rubrica_e_explica():
    vaga = score_job(job())

    assert vaga.score == vaga.score  # valor derivado, nao aleatorio
    assert 70 <= vaga.score <= 100
    assert "mesmo cargo" in vaga.reasons
    assert "remoto" in vaga.reasons


def test_score_job_de_vaga_ruim_fica_baixo():
    vaga = score_job(
        job(
            title="Engenheiro Sênior Java",
            company="",
            location="Lisboa",
            salary="",
            seniority="Senior",
            description="Kubernetes, AWS, Java, React e machine learning.",
            remote=False,
        )
    )

    assert vaga.score < 50


@pytest.mark.parametrize(
    ("overrides", "trecho"),
    [
        ({"url": ""}, "sem link"),
        ({"title": "Designer Gráfico"}, "fora do perfil"),
        ({"title": "Vendedor"}, "fora do perfil"),
        ({"title": "Analista de Suporte", "seniority": "Senior", "description": ""}, "sênior"),
        ({"salary": "R$ 1.200"}, "piso"),
        ({"location": "Lisboa", "remote": False}, "fora do RN"),
        ({"description": "Exige 3 anos de experiência em suporte"}, "anos"),
        ({"description": "Venha participar do nosso banco de talentos"}, "red flag"),
    ],
)
def test_reject_reason_cobre_os_filtros_do_playbook(overrides, trecho):
    assert trecho in reject_reason(job(**overrides))


def test_reject_reason_aceita_vaga_boa():
    assert reject_reason(job()) == ""


# --------------------------------------------------------------------- fontes
def test_clean_html_vira_texto_curto():
    texto = clean_html("<p>Olá&nbsp;<b>mundo</b></p>")

    assert texto == "Olá mundo"


# --------------------------------------------------------------------- rodada
@pytest.mark.asyncio
async def test_collect_filtra_deduplica_e_ordena(monkeypatch):
    existente = job(title="Analista de Suporte N1", company="Stefanini")
    ruim = job(title="Vendedor", company="Loja", url="https://exemplo.test/2")
    boa = job(title="Técnico de Suporte", company="Acme", url="https://exemplo.test/3")

    async def fake_fetch_all(sources=()):
        return [existente, ruim, boa, boa], ["Jobicy: HTTPStatusError"]

    monkeypatch.setattr("zaya.career.service.fetch_all", fake_fetch_all)
    state = {"vagas": [{"empresa": "Stefanini", "cargo": "Analista de Suporte N1"}]}

    result = await collect(state)

    assert [vaga.job.company for vaga in result.novas] == ["Acme"]
    assert result.conhecidas == 1
    assert result.duplicadas == 1
    assert result.bruto == 4
    assert [motivo for _, motivo in result.descartadas] == ["fora do perfil"]
    assert result.falhas == ["Jobicy: HTTPStatusError"]


@pytest.mark.asyncio
async def test_collect_sem_novidade(monkeypatch):
    async def fake_fetch_all(sources=()):
        return [job()], []

    monkeypatch.setattr("zaya.career.service.fetch_all", fake_fetch_all)

    result = await collect({"vagas": []})

    assert result.total == 1


# ------------------------------------------------------------------ horario
def test_career_due_so_no_dia_e_na_janela():
    assert career_due("09:00", "09:00", "18:00", 0, [0]) is True
    assert career_due("09:30", "09:00", "18:00", 0, [0]) is True
    assert career_due("18:00", "09:00", "18:00", 0, [0]) is False
    assert career_due("08:59", "09:00", "18:00", 0, [0]) is False
    assert career_due("09:00", "09:00", "18:00", 1, [0]) is False


# ------------------------------------------------------------------ mensagem
def _estado(*vagas: ScoredJob) -> dict:
    state: dict = {"vagas": []}
    record_results(state, SearchResult(novas=list(vagas)))
    return state


def test_compose_message_lista_com_link_score_e_motivo():
    state = _estado(score_job(job()), score_job(job(title="Técnico de TI", company="Acme")))
    result = SearchResult(novas=[], bruto=50, conhecidas=3)

    texto = compose_message(state, result=result, day=DAY)

    assert "Vagas novas — 2 encontradas" in texto
    assert "segunda" in texto
    assert "1. Analista de Suporte N1 — Stefanini" in texto
    assert "https://exemplo.test/vaga/1" in texto
    assert "Por quê:" in texto
    assert "Quer que eu prepare o CV da primeira?" in texto


def test_compose_message_semana_vazia_avisa_em_vez_de_calar():
    result = SearchResult(bruto=12, falhas=["RemoteOK: ConnectError"])

    texto = compose_message({"vagas": []}, result=result, day=DAY)

    assert "Sem vagas novas esta semana" in texto
    assert "12 vagas" in texto
    assert "RemoteOK: ConnectError" in texto


def test_compose_message_respeita_limite_e_avisa_resto():
    state = _estado(
        *[score_job(job(title=f"Suporte {i}", company=f"Empresa {i}")) for i in range(4)]
    )

    texto = compose_message(state, day=DAY, limit=2)

    assert "+2 com score menor" in texto


def test_pendentes_ignoram_o_que_ja_foi_entregue_e_o_que_nao_tem_score():
    state = {
        "vagas": [
            {"id": "vaga-001", "cargo": "Manual", "empresa": "Antiga", "status": "nova"},
            {
                "id": "vaga-002",
                "cargo": "Enviada",
                "empresa": "A",
                "status": "nova",
                "score": 70,
                "entrega": "entregue",
            },
            {"id": "vaga-003", "cargo": "Presa", "empresa": "B", "status": "nova", "score": 80},
            {
                "id": "vaga-004",
                "cargo": "Aplicada",
                "empresa": "C",
                "status": "aplicada",
                "score": 90,
                "entrega": "pendente",
            },
        ]
    }

    pendentes = __import__("zaya.career.service", fromlist=["pending_jobs"]).pending_jobs(state)

    assert [item["id"] for item in pendentes] == ["vaga-003"]


def test_mark_delivered_libera_o_reenvio_so_do_que_foi_enviado():
    from zaya.career.service import mark_delivered

    state = _estado(score_job(job()), score_job(job(title="Outra", company="Acme")))

    assert mark_delivered(state, ["vaga-001"], when="2026-11-09T10:00:00") == 1
    assert state["vagas"][0]["entrega"] == "entregue"
    assert state["vagas"][0]["entregue_em"] == "2026-11-09T10:00:00"
    assert state["vagas"][1]["entrega"] == "pendente"


def test_summary_line_conta_a_rodada():
    result = SearchResult(novas=[score_job(job())], bruto=10, conhecidas=2)
    result.descartadas.append((job(), "sem link"))

    linha = summary_line(result)

    assert "10 vagas lidas" in linha
    assert "1 novas" in linha
    assert "1 descartadas" in linha
    assert "2 já registradas" in linha


# ------------------------------------------------- idioma nao e filtro (nov/2026)
def test_vaga_remota_no_exterior_entra_com_score_menor():
    vaga = job(
        title="Customer Support Consultant",
        company="SupportYourApp",
        location="Albania",
        salary="",
        seniority="",
        description="Atendimento ao cliente em inglês.",
        remote=True,
    )

    assert reject_reason(vaga) == ""
    assert local_points(vaga) == 10
    assert "restrito a outro país" in ", ".join(score_job(vaga).reasons)


def test_vaga_remota_brasileira_continua_com_15():
    assert local_points(job(location="Brazil", remote=True)) == 15
    assert local_points(job(location="Worldwide", remote=True)) == 15
    assert local_points(job(location="LATAM", remote=True)) == 15


def test_presencial_no_exterior_continua_fora_mesmo_com_idioma():
    vaga = job(
        title="IT Support Specialist",
        company="Acme GmbH",
        location="Hamburg",
        remote=False,
        location_kind="office",
    )

    assert reject_reason(vaga) == "presencial fora do RN"


def test_casamento_de_cargo_e_por_palavra_inteira():
    from zaya.career.score import title_points as tp

    # "rater" dentro de "berater" nao faz de um conselheiro de saude uma vaga.
    assert tp(job(title="Gesundheitsberater (w/m/d)")) == 0
    assert tp(job(title="AI Content Reviewer")) == 15
    # "ti" dentro de "ticket" nao conta como area de TI.
    assert tp(job(title="Ticket Operations Lead")) == 0
    # "suporte" como palavra continua valendo em qualquer posicao.
    assert tp(job(title="Analista de Suporte N1")) == 25


def test_job_line_mostra_remoto_sem_a_sede_da_empresa():
    vaga = score_job(job(location="Seattle, Washington", remote=True, location_kind="office"))
    linha = __import__("zaya.career.service", fromlist=["job_line"]).job_line(1, vaga)

    assert "remoto" in linha
    assert "Seattle" not in linha


def test_salario_anual_nao_e_lido_como_mensal():
    from zaya.career.sources import _salary

    anual = _salary(80000, 90000, "USD", period="ano")
    mensal = _salary(2800, 3200, "BRL", period="monthly")
    horario = _salary(20, 30, "USD", period="hourly")

    assert anual == "USD 80.000 a 90.000/ano"
    assert mensal == "BRL 2.800 a 3.200/mês"
    assert horario == "USD 20 a 30/hora"
    assert _salary(None, None, "USD") == ""
