from __future__ import annotations

import asyncio
import logging
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import zora.controller.orchestrator as orchestrator_module
from zora.controller.commands import CommandsMixin
from zora.controller.contract import OrchestratorContract
from zora.controller.goals import GoalTracker
from zora.controller.initiative import InitiativeMixin
from zora.controller.memory import MemoryMixin
from zora.controller.orchestrator import Orchestrator
from zora.controller.registration import RegistrationMixin
from zora.controller.speech import SpeechMixin
from zora.controller.turns import TurnMixin
from zora.controller.voice import VoiceMixin
from zora.finance.watchlist import Watchlist
from zora.memory.store import MemoryStore
from zora.tasks.store import TaskStore


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

    async def ensure_server(self) -> None:
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
    monkeypatch.setenv("ZORA_WATCHLIST_PATH", str(tmp_path / "watchlist.json"))
    monkeypatch.setenv("ZORA_GOALS_FILE", str(tmp_path / "goals.json"))
    cfg.focus.path = str(tmp_path / "focus.json")
    monkeypatch.setattr(
        orchestrator_module, "TaskStore", lambda: TaskStore(tmp_path / "tasks.json")
    )

    store = MemoryStore(tmp_path / "zora.db")
    brain = FakeBrain(tmp_path / "brain")
    ui = FakeUI()
    tts = FakeTTS()
    client = FakeClient()
    instance = Orchestrator(
        cfg=cfg,
        logger=logging.getLogger("test.zora"),
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
    assert orch.cfg.opencode.auto_approve is True
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

    await asyncio.gather(orch.select_mode("carreira"), orch.select_mode("academica"))

    carreira = orch.store.get_or_create_conversation(str(orch.directory), "carreira")
    academica = orch.store.get_or_create_conversation(str(orch.directory), "academica")
    sid_carreira = carreira["opencode_session_id"]
    sid_academica = academica["opencode_session_id"]

    assert sid_carreira and sid_academica
    assert sid_carreira != sid_academica
    assert "Carreira" in client.created[sid_carreira]
    assert "Acad" in client.created[sid_academica]

    atual = orch.store.get_conversation(orch.conversation_id)
    assert atual["mode"] == orch.persona.key
    assert atual["opencode_session_id"] == orch.session_id


@pytest.mark.asyncio
async def test_sessao_ausente_reinjeta_o_historico_local(orch):
    orch._prepare_conversation()
    conversation_id = orch.conversation_id
    orch.store.add_turn(conversation_id, "user", "quero vaga de suporte")
    orch.store.add_turn(conversation_id, "assistant", "vamos revisar seu curriculo")
    orch.store.set_session(conversation_id, "ses_sumiu")

    await orch._activate_conversation(conversation_id, orch.persona)

    conversa = orch.store.get_conversation(conversation_id)
    assert conversa["opencode_session_id"] != "ses_sumiu"
    contexto = orch._take_resume_context()
    assert "Talita: quero vaga de suporte" in contexto
    assert "Zaia: vamos revisar seu curriculo" in contexto
    assert orch._take_resume_context() == ""

