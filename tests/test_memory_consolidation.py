from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from zora.config import Config, SecondBrainConfig
from zora.memory.consolidation import (
    DailyMemory,
    compose_note,
    day_bounds,
    format_turns,
    parse_day,
)
from zora.memory.store import MemoryStore
from zora.secondbrain.index import SecondBrainIndex
from zora.secondbrain.notes import SecondBrain


class _FakeClient:
    def __init__(self, resumo: str = "", falhar: bool = False) -> None:
        self.resumo = resumo
        self.falhar = falhar
        self.deleted: list[str] = []
        self.prompts: list[str] = []

    async def create_session(self, title: str | None = None):
        return SimpleNamespace(id="ses_memoria")

    async def prompt_sync(self, session_id: str, text: str, **kwargs):
        self.prompts.append(text)
        if self.falhar:
            raise RuntimeError("provedor indisponível")
        return {"content": [{"type": "text", "text": self.resumo}]}

    async def delete_session(self, session_id: str) -> None:
        self.deleted.append(session_id)


@pytest.fixture
def memoria(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=tmp_path / "missing.toml",
        env={"ZORA_DATA_DIR": str(tmp_path / "data")},
    )
    cfg.memory.use_llm = False
    store = MemoryStore(tmp_path / "zora.db")
    sb_cfg = SecondBrainConfig(path=str(tmp_path / "brain"), default_folder="Inbox")
    index = SecondBrainIndex(sb_cfg, tmp_path / "sb.db")
    brain = SecondBrain(sb_cfg, index)
    proj = tmp_path / "proj"
    proj.mkdir()
    yield cfg, store, brain, index, proj
    index.close()
    store.close()


def _adicionar_dia(store: MemoryStore, proj: Path, textos: list[tuple[str, str]]) -> None:
    conv = store.create_conversation(str(proj), mode="conversa")
    for role, text in textos:
        store.add_turn(int(conv["id"]), role, text)


def test_parse_day_aceita_atalhos_e_forcar():
    hoje = date(2026, 11, 9)
    assert parse_day("", today=hoje) == (hoje, False)
    assert parse_day("ontem", today=hoje) == (date(2026, 11, 8), False)
    assert parse_day("2026-11-01 forcar", today=hoje) == (date(2026, 11, 1), True)
    assert parse_day("forçar", today=hoje) == (hoje, True)


def test_day_bounds_cobre_24_horas():
    inicio, fim = day_bounds(date(2026, 11, 9))
    assert datetime.fromisoformat(inicio).date() == date(2026, 11, 9)
    assert datetime.fromisoformat(fim) - datetime.fromisoformat(inicio) == timedelta(days=1)


def test_format_turns_ignora_contexto_e_limita():
    turns = [
        {"role": "user", "text": "olá", "mode": "conversa"},
        {"role": "context", "text": "Notas usadas: x", "mode": "conversa"},
        {"role": "assistant", "text": "oi", "mode": "conversa"},
    ]
    texto = format_turns(turns, max_turns=10, max_chars=500)
    assert "Talita: olá" in texto
    assert "Zaia: oi" in texto
    assert "Notas usadas" not in texto


def test_compose_note_tem_secoes_e_decisoes():
    turns = [
        {"role": "user", "text": "decidimos usar OpenCode", "mode": "conversa"},
        {"role": "assistant", "text": "combinado", "mode": "conversa"},
    ]
    nota = compose_note(date(2026, 11, 9), turns, [])
    assert "## Síntese do dia" in nota
    assert "## Decisões" in nota
    assert "decidimos usar OpenCode" in nota
    assert "## Conversas" in nota


def test_compose_note_nao_duplica_sintese_nem_decisoes():
    turns = [
        {"role": "user", "text": "decidimos usar OpenCode", "mode": "conversa"},
    ]
    nota = compose_note(
        date(2026, 11, 9),
        turns,
        [],
        "## Síntese\nUm dia produtivo.\n\n## Decisões\n- Usar OpenCode.",
    )
    assert nota.count("Síntese") == 1
    assert nota.count("## Decisões") == 1
    # A mensagem aparece em Conversas, mas nao como bullet de Decisoes.
    assert "\n- decidimos usar OpenCode" not in nota

async def test_consolida_dia_cria_nota_e_nao_duplica(memoria):
    cfg, store, brain, index, proj = memoria
    _adicionar_dia(
        store,
        proj,
        [("user", "hoje revisei o README"), ("assistant", "fiz os ajustes")],
    )
    dm = DailyMemory(cfg, store, brain, index, _FakeClient(), proj)

    caminho = await dm.consolidate(date.today())

    assert caminho is not None
    assert caminho.startswith("Memoria/Memória ")
    nota = brain.read_note(caminho)
    assert "hoje revisei o README" in nota.body
    assert "memoria" in nota.tags
    assert store.get_meta(f"memory_consolidated:{date.today().isoformat()}") == caminho
    assert await dm.consolidate(date.today()) is None


async def test_consolidar_de_novo_com_forcar_atualiza_nota(memoria):
    cfg, store, brain, index, proj = memoria
    _adicionar_dia(store, proj, [("user", "primeira conversa")])
    dm = DailyMemory(cfg, store, brain, index, _FakeClient(), proj)
    caminho = await dm.consolidate(date.today())

    conv = store.current_conversation(str(proj), mode="conversa")
    assert conv is not None
    store.add_turn(int(conv["id"]), "user", "segunda conversa")

    novo = await dm.consolidate(date.today(), force=True)

    assert novo == caminho
    assert "segunda conversa" in brain.read_note(caminho).body


async def test_consolida_com_resumo_do_modelo(memoria):
    cfg, store, brain, index, proj = memoria
    cfg.memory.use_llm = True
    _adicionar_dia(store, proj, [("user", "falamos de arquitetura")])
    cliente = _FakeClient(resumo="## Síntese\nResumo feito pelo modelo.")
    dm = DailyMemory(cfg, store, brain, index, cliente, proj)

    caminho = await dm.consolidate(date.today())

    assert caminho is not None
    assert "Resumo feito pelo modelo." in brain.read_note(caminho).body
    assert cliente.deleted == ["ses_memoria"]
    assert "falamos de arquitetura" in cliente.prompts[0]


async def test_consolida_sem_modelo_quando_resumo_falha(memoria):
    cfg, store, brain, index, proj = memoria
    cfg.memory.use_llm = True
    _adicionar_dia(store, proj, [("user", "assunto importante")])
    cliente = _FakeClient(falhar=True)
    dm = DailyMemory(cfg, store, brain, index, cliente, proj)

    caminho = await dm.consolidate(date.today())

    assert caminho is not None
    assert "assunto importante" in brain.read_note(caminho).body
    assert cliente.deleted == ["ses_memoria"]


async def test_dia_sem_conversa_nao_cria_nota(memoria):
    cfg, store, brain, index, proj = memoria
    dm = DailyMemory(cfg, store, brain, index, _FakeClient(), proj)

    assert await dm.consolidate(date.today()) is None
    assert store.get_meta(f"memory_consolidated:{date.today().isoformat()}") is None


async def test_turns_between_filtra_por_projeto_e_intervalo(memoria):
    _cfg, store, _brain, _index, proj = memoria
    _adicionar_dia(store, proj, [("user", "dentro")])
    outro = proj.parent / "outro"
    outro.mkdir()
    _adicionar_dia(store, outro, [("user", "fora")])

    inicio, fim = day_bounds(date.today())
    linhas = store.turns_between(inicio, fim, str(proj))

    assert [linha["text"] for linha in linhas] == ["dentro"]
    assert json.dumps(dict(linhas[0]))  # linha serializável
