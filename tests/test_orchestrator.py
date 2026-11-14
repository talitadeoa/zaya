from __future__ import annotations

import asyncio
import contextlib
import logging
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import zaya.controller.orchestrator as orchestrator_module
from zaya.controller.briefing import BriefingMixin
from zaya.controller.career import CareerMixin
from zaya.controller.commands import CommandsMixin
from zaya.controller.contract import OrchestratorContract
from zaya.controller.goals import GoalTracker
from zaya.controller.initiative import InitiativeMixin
from zaya.controller.memory import MemoryMixin
from zaya.controller.orchestrator import Orchestrator
from zaya.controller.registration import RegistrationMixin
from zaya.controller.speech import SpeechMixin
from zaya.controller.turns import TurnMixin
from zaya.controller.voice import VoiceMixin
from zaya.finance.watchlist import Watchlist
from zaya.memory.store import MemoryStore
from zaya.tasks.store import TaskStore


class FakeUI:
    """UI que so registra as chamadas (sem terminal real)."""

    def __init__(self) -> None:
        self.console = types.SimpleNamespace(width=80, print=lambda *a, **k: None)
        self.events: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.events.append((name, args, kwargs))
            if name == "confirm":
                return False
            if name == "permission":
                return "once"
            if name == "prompt":
                return ""
            return None

        return record

    def called(self, name: str) -> list[tuple[tuple, dict]]:
        return [(args, kwargs) for event, args, kwargs in self.events if event == name]


class FakeTTS:
    def __init__(self) -> None:
        self.enabled = True
        self.provider = "fake"
        self.spoken: list[str] = []

    async def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop(self) -> None:
        pass

    async def prepare(self, text: str):
        return None

    async def play_prepared(self, prepared) -> None:
        pass

    def set_provider(self, provider: str) -> None:
        self.provider = provider


class FakeIndex:
    def count(self) -> int:
        return 0

    def search(self, query: str, limit: int = 3):
        return []

    def stats(self) -> dict[str, int]:
        return {"notes": 0}

    def reindex(self, force: bool = False) -> dict[str, int]:
        return {"indexed": 0, "removed": 0, "total": 0}


class FakeBrain:
    def __init__(self, root: Path, preferences: str = "") -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.preferences_path = self.root / "preferences.md"
        self.preferences_path.write_text(preferences, encoding="utf-8")

    def load_preferences(self) -> str:
        return self.preferences_path.read_text(encoding="utf-8")

    def append_preference(self, text: str, *args) -> bool:
        with self.preferences_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n- {text}\n")
        return True


class FakeClient:
    def __init__(self) -> None:
        self.endpoint = "http://fake"
        self.model = "fake/model"
        self.aborted: list[str] = []

    async def health(self):
        return {}

    async def ensure_ready(self) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def abort(self, session_id: str) -> None:
        self.aborted.append(session_id)

    async def create_session(self, **kwargs):
        return types.SimpleNamespace(id="ses-fake")

    async def get_session(self, session_id: str):
        return None

    async def session_status(self, session_id: str):
        return {"type": "idle"}

    async def latest_assistant(self, session_id: str):
        return None


class FakeTranscriber:
    def warmup(self) -> None:
        pass

    def transcribe(self, audio, partial: bool = True) -> str:
        return ""


@pytest.fixture
def orch(tmp_path, cfg, monkeypatch):
    monkeypatch.setenv("ZAYA_WATCHLIST_PATH", str(tmp_path / "watchlist.json"))
    monkeypatch.setenv("ZAYA_GOALS_FILE", str(tmp_path / "goals.json"))
    cfg.focus.path = str(tmp_path / "focus.json")
    monkeypatch.setattr(
        orchestrator_module, "TaskStore", lambda: TaskStore(tmp_path / "tasks.json")
    )

    store = MemoryStore(tmp_path / "zaya.db")
    brain = FakeBrain(tmp_path / "brain")
    ui = FakeUI()
    tts = FakeTTS()
    client = FakeClient()
    instance = Orchestrator(
        cfg=cfg,
        logger=logging.getLogger("test.zaya"),
        ui=ui,
        store=store,
        index=FakeIndex(),
        brain=brain,
        tts=tts,
        transcriber=FakeTranscriber(),
        recorder=types.SimpleNamespace(),
        client=client,
    )
    instance.watchlist = Watchlist(tmp_path / "watchlist.json")
    instance.goals = GoalTracker(tmp_path / "goals.json")
    yield instance
    store.close()


def test_mixins_share_the_explicit_contract():
    mixins = (
        SpeechMixin,
        TurnMixin,
        VoiceMixin,
        CommandsMixin,
        MemoryMixin,
        BriefingMixin,
        CareerMixin,
        RegistrationMixin,
        InitiativeMixin,
    )
    for mixin in mixins:
        assert issubclass(mixin, OrchestratorContract)
    assert issubclass(Orchestrator, OrchestratorContract)


def test_contract_is_abstract_and_orchestrator_concrete():
    with pytest.raises(TypeError):
        OrchestratorContract()
    assert not Orchestrator.__abstractmethods__


def test_state_is_initialized(orch):
    assert orch.conversation_id is None
    assert orch.session_id == ""
    assert orch.cfg.interface.fast_smalltalk is True
    assert orch._voice_mode == bool(orch.cfg.audio.continuous)


def test_quick_reply_respects_fast_smalltalk(orch):
    orch.cfg.interface.fast_smalltalk = True
    reply = orch._quick_reply("oi")
    assert reply

    orch.cfg.interface.fast_smalltalk = False
    assert orch._quick_reply("oi") is None


def test_quick_reply_ignores_real_requests(orch):
    orch.cfg.interface.fast_smalltalk = True
    assert orch._quick_reply("revise a arquitetura do projeto") is None


def test_is_short_prompt(orch):
    assert orch._is_short_prompt("ok") is True
    assert orch._is_short_prompt("uma duas tres quatro cinco seis sete oito nove dez onze") is False
    assert orch._is_short_prompt("linha\noutra") is False


@pytest.mark.asyncio
async def test_run_turn_repassa_a_mensagem_original(orch, monkeypatch):
    captured: dict[str, object] = {}

    async def fake_turn(text, system=None, session_id=None, **kwargs):
        captured["text"] = text
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(orch, "_turn_impl", fake_turn)

    await orch._run_turn("prompt com contexto", system="s", history_text="pergunta")

    assert captured["text"] == "prompt com contexto"
    assert captured["history_text"] == "pergunta"


@pytest.mark.asyncio
async def test_prompt_dinamico_vai_na_mensagem_nao_no_system(orch, monkeypatch):
    orch._prepare_conversation()
    captured: dict[str, object] = {}

    async def fake_turn(text, system=None, session_id=None, **kwargs):
        captured["text"] = text
        captured["system"] = system
        captured.update(kwargs)
        return ""

    monkeypatch.setattr(orch, "_run_turn", fake_turn)

    await orch._handle_task_impl("qual a capital da França?", speak=False)

    assert "[Agora:" not in captured["system"]
    assert "[Agora:" in captured["text"]
    assert captured["history_text"] == "qual a capital da França?"
    assert "Diretriz da Zaya" in captured["system"]


@pytest.mark.asyncio
async def test_dispatch_silent_answers_smalltalk_locally(orch):
    orch._prepare_conversation()
    answer = await orch._dispatch_silent("oi")

    assert answer
    turns = orch.store.turns(orch.conversation_id)
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["text"] == answer


@pytest.mark.asyncio
async def test_toggle_commands_update_config(orch):
    assert await orch._handle_command("/sair") is True

    await orch._handle_command("/rapido on")
    assert orch.cfg.interface.fast is True

    await orch._handle_command("/contexto off")
    assert orch.cfg.secondbrain.auto_context is False

    await orch._handle_command("/confirmar on")
    assert orch.cfg.secondbrain.confirm_create is True

    await orch._handle_command("/texto")
    assert orch.cfg.audio.continuous is False

    await orch._handle_command("/fala")
    assert orch.cfg.audio.continuous is True

    await orch._handle_command("/voz off")
    assert orch.tts.enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/novo", "/nova"])
async def test_novo_e_nova_abrem_sessao_nova(orch, command):
    antes = orch.session_id
    assert await orch._handle_command(command) is False
    assert orch.session_id != antes
    assert orch.ui.called("success")


@pytest.mark.asyncio
async def test_prepare_liga_o_worker_de_memoria(orch, monkeypatch):
    chamadas: list[bool] = []

    async def fake_bootstrap() -> None:
        orch.session_id = "ses_teste"

    monkeypatch.setattr(orch, "_bootstrap", fake_bootstrap)
    monkeypatch.setattr(orch, "_start_memory_worker", lambda: chamadas.append(True))

    await orch.prepare()

    assert chamadas == [True]


@pytest.mark.asyncio
async def test_comando_consolidar_usa_o_dia_pedido(orch, monkeypatch):
    chamadas: list[tuple] = []

    async def fake(day=None, *, force=False, timeout_seconds=None):
        chamadas.append((day, force))
        return "Memoria/Memória teste.md"

    monkeypatch.setattr(orch, "consolidate_day", fake)

    await orch._handle_command("/consolidar ontem forcar")

    esperado = datetime.now().astimezone().date() - timedelta(days=1)
    assert chamadas == [(esperado, True)]
    assert orch.ui.called("success")


@pytest.mark.asyncio
async def test_auto_approve_warns(orch):
    await orch._handle_command("/autoaprovar on")
    assert orch.cfg.agent.auto_approve is True
    assert orch.ui.called("warning")


@pytest.mark.asyncio
async def test_unknown_command_warns(orch):
    assert await orch._handle_command("/inexistente") is False
    warnings = orch.ui.called("warning")
    assert warnings and "desconhecido" in warnings[-1][0][0]


@pytest.mark.asyncio
async def test_mode_command_without_argument_lists_modes(orch):
    await orch._handle_command("/modo")
    infos = orch.ui.called("info")
    assert any("modo atual" in args[0] for args, _ in infos)


@pytest.mark.asyncio
async def test_switching_to_unknown_mode_warns(orch):
    await orch._switch_mode("nao-existe")
    warnings = orch.ui.called("warning")
    assert warnings and "modo desconhecido" in warnings[-1][0][0]


@pytest.mark.asyncio
async def test_status_reports_state(orch):
    await orch._handle_command("/status")
    assert orch.ui.called("status")


@pytest.mark.asyncio
async def test_history_empty_conversation_informs(orch):
    orch._prepare_conversation()
    await orch._handle_command("/historico")
    infos = orch.ui.called("info")
    assert any("sem histórico" in args[0] for args, _ in infos)


def test_preferences_block_reads_brain(orch):
    orch.brain.preferences_path.write_text("Gosta de respostas curtas.", encoding="utf-8")
    assert "respostas curtas" in orch._preferences_block()


def test_user_name_from_preferences(orch):
    orch.brain.preferences_path.write_text("Nome preferido: Talita.", encoding="utf-8")
    assert orch._user_name() == "Talita"


def test_local_greeting_uses_name(orch):
    orch.brain.preferences_path.write_text("Nome preferido: Talita.", encoding="utf-8")
    greeting = orch._local_greeting(__import__("datetime").datetime.now().astimezone())
    assert "Talita" in greeting


def test_idle_message_does_not_repeat(orch):
    first = orch._idle_message()
    second = orch._idle_message()
    assert first and second
    assert first != second


def test_find_task_matches_id_and_title(orch):
    task = orch.tasks.upsert("Revisar o README")
    assert orch._find_task(task.id) is task
    assert orch._find_task("readme") is task
    assert orch._find_task("inexistente") is None


class RecordingClient(FakeClient):
    """Cria sessoes com atraso para expor intercalacoes entre trocas de modo."""

    def __init__(self) -> None:
        super().__init__()
        self.created: dict[str, str] = {}

    async def create_session(self, title: str | None = None, **kwargs):
        await asyncio.sleep(0.01)
        session_id = f"ses-{len(self.created) + 1}"
        self.created[session_id] = title or ""
        return types.SimpleNamespace(id=session_id)


@pytest.mark.asyncio
async def test_trocas_concorrentes_de_modo_nao_cruzam_sessoes(orch):
    client = RecordingClient()
    orch.client = client

    await asyncio.gather(orch.select_mode("trabalhadora"), orch.select_mode("pesquisadora"))

    trabalhadora = orch.store.get_or_create_conversation(str(orch.directory), "trabalhadora")
    pesquisadora = orch.store.get_or_create_conversation(str(orch.directory), "pesquisadora")
    sid_trabalhadora = trabalhadora["agent_session_id"]
    sid_pesquisadora = pesquisadora["agent_session_id"]

    assert sid_trabalhadora and sid_pesquisadora
    assert sid_trabalhadora != sid_pesquisadora
    assert "Trabalhadora" in client.created[sid_trabalhadora]
    assert "Pesquisadora" in client.created[sid_pesquisadora]

    atual = orch.store.get_conversation(orch.conversation_id)
    assert atual["mode"] == orch.persona.key
    assert atual["agent_session_id"] == orch.session_id


class ForeignDirectoryClient(FakeClient):
    """Sessao viva, mas de outro diretorio (a API v2 responde 200 mesmo assim)."""

    async def get_session(self, session_id: str):
        return types.SimpleNamespace(id=session_id, directory="/Users/talita/zora")

    async def create_session(self, title: str | None = None, **kwargs):
        return types.SimpleNamespace(id="ses-nova")


@pytest.mark.asyncio
async def test_sessao_de_outro_diretorio_nao_e_retomada(orch):
    orch._prepare_conversation()
    conversation_id = orch.conversation_id
    orch.store.add_turn(conversation_id, "user", "oi, tudo bem?")
    orch.store.set_session(conversation_id, "ses_de_zora")

    orch.client = ForeignDirectoryClient()
    await orch._activate_conversation(conversation_id, orch.persona)

    assert orch.session_id == "ses-nova"
    conversa = orch.store.get_conversation(conversation_id)
    assert conversa["agent_session_id"] == "ses-nova"
    assert "Talita: oi, tudo bem?" in orch._take_resume_context()


@pytest.mark.asyncio
async def test_sessao_ausente_reinjeta_o_historico_local(orch):
    orch._prepare_conversation()
    conversation_id = orch.conversation_id
    orch.store.add_turn(conversation_id, "user", "quero vaga de suporte")
    orch.store.add_turn(conversation_id, "assistant", "vamos revisar seu curriculo")
    orch.store.set_session(conversation_id, "ses_sumiu")

    await orch._activate_conversation(conversation_id, orch.persona)

    conversa = orch.store.get_conversation(conversation_id)
    assert conversa["agent_session_id"] != "ses_sumiu"
    contexto = orch._take_resume_context()
    assert "Talita: quero vaga de suporte" in contexto
    assert "Zaya: vamos revisar seu curriculo" in contexto
    assert orch._take_resume_context() == ""


class FakeAgendaBriefing:
    enabled = True

    def __init__(self, notes: list[dict], routines: list[dict] | None = None) -> None:
        self.notes = notes
        self.rotinas = routines or []

    async def notes_on(self, date: str) -> list[dict]:
        return self.notes

    async def routines(self) -> list[dict]:
        return self.rotinas


class FakeTelegram:
    enabled = True

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, text: str) -> None:
        self.sent.append(text)


class FalhaUmaVezTelegram(FakeTelegram):
    """Falha na primeira entrega e funciona depois (Telegram fora do ar)."""

    def __init__(self) -> None:
        super().__init__()
        self.falhas = 1

    async def send_message(self, text: str) -> None:
        if self.falhas:
            self.falhas -= 1
            raise RuntimeError("Telegram fora do ar")
        await super().send_message(text)


def _notas_briefing() -> list[dict]:
    return [
        {
            "id": "t1",
            "text": "Responder a Stefanini",
            "is_checkbox": True,
            "completed": False,
            "parent_id": None,
            "routine_id": None,
            "order": 1,
        },
        {
            "id": "r",
            "text": "Rotina matinal",
            "is_checkbox": True,
            "completed": False,
            "parent_id": None,
            "routine_id": "rot1",
            "order": 2,
        },
        {
            "id": "r1",
            "text": "alongar",
            "is_checkbox": True,
            "completed": True,
            "parent_id": "r",
            "routine_id": "rot1",
            "order": 1,
        },
        {
            "id": "r2",
            "text": "meditar",
            "is_checkbox": True,
            "completed": False,
            "parent_id": "r",
            "routine_id": "rot1",
            "order": 2,
        },
    ]


@pytest.mark.asyncio
async def test_briefing_envia_uma_vez_por_dia(orch):
    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    orch.cfg.briefing.morning_routine = "matinal"
    dia = datetime.now().astimezone().date()

    assert await orch._send_briefing("morning", dia) is True
    assert len(orch.telegram.sent) == 1
    mensagem = orch.telegram.sent[0]
    assert "Bom dia" in mensagem
    assert "Responder a Stefanini" in mensagem
    assert "Rotina matinal" not in mensagem
    assert "alongar" not in mensagem

    assert await orch._send_briefing("morning", dia) is False
    assert len(orch.telegram.sent) == 1


@pytest.mark.asyncio
async def test_briefing_do_meio_dia_mostra_pendentes(orch):
    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    dia = datetime.now().astimezone().date()

    assert await orch._send_briefing("noon", dia) is True
    mensagem = orch.telegram.sent[0]
    assert "Feito até agora" in mensagem
    assert "Ainda falta" in mensagem
    assert "☐ Responder a Stefanini" in mensagem
    assert "Rotina matinal" not in mensagem
    assert "alongar" not in mensagem


@pytest.mark.asyncio
async def test_career_envia_uma_vez_por_dia_e_grava_o_estado(orch, monkeypatch):
    from zaya.career.models import Job
    from zaya.career.service import load_state

    jobs = [
        Job(
            title="Analista de Suporte N1",
            company="Stefanini",
            url="https://exemplo.test/1",
            source="Remotive",
            location="Brazil",
            salary="R$ 2.800",
            seniority="Júnior",
            description="troubleshooting, windows e redes",
            remote=True,
        )
    ]

    async def fake_fetch_all(sources=()):
        return jobs, []

    monkeypatch.setattr("zaya.career.service.fetch_all", fake_fetch_all)
    orch.telegram = FakeTelegram()
    dia = datetime.now().astimezone().date()

    assert await orch._send_career(dia) is True
    assert len(orch.telegram.sent) == 1
    assert "Vagas novas" in orch.telegram.sent[0]
    assert "Stefanini" in orch.telegram.sent[0]

    state = load_state(orch._career_state_path())
    assert state["vagas"][0]["empresa"] == "Stefanini"
    assert state["vagas"][0]["status"] == "nova"

    # Segundo disparo no mesmo dia nao busca nem envia de novo.
    assert await orch._send_career(dia) is False
    assert len(orch.telegram.sent) == 1


@pytest.mark.asyncio
async def test_career_deduplica_na_segunda_rodada(orch, monkeypatch):
    from zaya.career.models import Job
    from zaya.career.service import load_state

    jobs = [
        Job(
            title="Técnico de Suporte",
            company="Acme",
            url="https://exemplo.test/a",
            source="Remotive",
            location="Brazil",
            remote=True,
        )
    ]

    async def fake_fetch_all(sources=()):
        return jobs, []

    monkeypatch.setattr("zaya.career.service.fetch_all", fake_fetch_all)
    orch.telegram = FakeTelegram()
    dia = datetime.now().astimezone().date()

    assert await orch._send_career(dia) is True
    state = load_state(orch._career_state_path())
    assert len(state["vagas"]) == 1

    # Rodada seguinte (outro dia) ve a mesma vaga: nao registra de novo.
    outra = dia + timedelta(days=1)
    orch._career_last_attempt = 0.0
    assert await orch._send_career(outra) is True
    state = load_state(orch._career_state_path())
    assert len(state["vagas"]) == 1
    assert "Sem vagas novas" in orch.telegram.sent[-1]


@pytest.mark.asyncio
async def test_career_nao_roda_sem_telegram(orch):
    orch.telegram = types.SimpleNamespace(enabled=False)

    orch._start_career_worker()

    assert orch._career_task is None


@pytest.mark.asyncio
async def test_start_career_liga_a_rotina_sem_bootstrap(orch):
    """O `telegram serve` so chama start_briefing/start_career no boot."""
    orch.telegram = FakeTelegram()

    orch.start_career()

    assert orch._career_task is not None
    # Idempotente: chamar de novo nao duplica a tarefa.
    task = orch._career_task
    orch.start_career()
    assert orch._career_task is task
    orch._career_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await orch._career_task
    orch._career_task = None


@pytest.mark.asyncio
async def test_career_reenvia_o_que_o_telegram_nao_entregou(orch, monkeypatch):
    from zaya.career.models import Job
    from zaya.career.service import load_state, pending_jobs

    jobs = [
        Job(
            title="Analista de Suporte N1",
            company="Stefanini",
            url="https://exemplo.test/1",
            source="Remotive",
            location="Brazil",
            remote=True,
        )
    ]

    async def fake_fetch_all(sources=()):
        return jobs, []

    monkeypatch.setattr("zaya.career.service.fetch_all", fake_fetch_all)
    orch.telegram = FalhaUmaVezTelegram()
    dia = datetime.now().astimezone().date()

    # Primeira tentativa: a vaga e gravada e continua pendente.
    assert await orch._send_career(dia) is False
    state = load_state(orch._career_state_path())
    assert len(state["vagas"]) == 1
    assert len(pending_jobs(state)) == 1
    assert state["vagas"][0]["status"] == "nova"

    # Segunda tentativa no mesmo dia: entrega sem precisar buscar vaga nova.
    orch._career_last_attempt = 0.0
    assert await orch._send_career(dia) is True
    assert len(orch.telegram.sent) == 1
    assert "Stefanini" in orch.telegram.sent[0]

    state = load_state(orch._career_state_path())
    assert pending_jobs(state) == []
    assert state["vagas"][0]["entrega"] == "entregue"
    assert state["vagas"][0]["entregue_em"]


def test_career_due_respeita_config(orch):
    from datetime import date as _date

    from zaya.career.service import career_due

    cfg = orch.cfg.career
    dia = _date(2026, 11, 9)  # segunda

    assert career_due(cfg.at, cfg.at, cfg.until, dia.weekday(), cfg.days) is True
    assert career_due(cfg.until, cfg.at, cfg.until, dia.weekday(), cfg.days) is False


class InsightClient(FakeClient):
    """Cliente que devolve um insight pronto (sem rede)."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def prompt_sync(self, session_id, text, **kwargs):
        self.prompts.append(text)
        return types.SimpleNamespace(text="Você fechou o que era trabalho e deixou a decisão pra depois.")

    async def delete_session(self, session_id) -> None:
        pass


@pytest.mark.asyncio
async def test_fecho_da_noite_manda_feito_nao_foi_e_insight(orch):
    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    orch.client = InsightClient()
    orch.cfg.briefing.morning_routine = "matinal"
    dia = datetime.now().astimezone().date()

    assert await orch._send_evening_report(dia) is True
    assert len(orch.telegram.sent) == 1
    mensagem = orch.telegram.sent[0]
    assert mensagem.startswith("Boa noite")
    assert "Feito hoje" in mensagem
    assert "☑ alongar" in mensagem
    assert "Não foi" in mensagem
    assert "☐ Responder a Stefanini" in mensagem
    assert "☐ meditar" in mensagem
    assert "Você fechou o que era trabalho" in mensagem

    # o prompt levou o recap do dia
    assert "Tarefas que não foram:" in orch.client.prompts[0]
    # a fake de Agenda não expõe metas: fica de fora do recap, sem quebrar
    assert "Metas ativas" not in orch.client.prompts[0]

    # uma vez por dia: de novo, não manda
    assert await orch._send_evening_report(dia) is False
    assert len(orch.telegram.sent) == 1


@pytest.mark.asyncio
async def test_fecho_da_noite_sem_insight_quando_o_modelo_falha(orch):
    class SemInsight(InsightClient):
        async def prompt_sync(self, session_id, text, **kwargs):
            raise RuntimeError("provedor fora do ar")

    orch.agenda = FakeAgendaBriefing(_notas_briefing())
    orch.telegram = FakeTelegram()
    orch.client = SemInsight()
    dia = datetime.now().astimezone().date()

    assert await orch._send_evening_report(dia) is True
    mensagem = orch.telegram.sent[0]
    assert "Feito hoje" in mensagem
    assert "Você fechou" not in mensagem

