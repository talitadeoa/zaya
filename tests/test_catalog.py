from __future__ import annotations

from types import SimpleNamespace

import pytest

from zaya.agent.catalog import (
    ModelCatalog,
    ModelOption,
    collect_models,
    provider_candidates,
    render_choices,
)


def _cfg(provider: str = "custom", model: str = "atual-1", base_url: str = "") -> object:
    agent = SimpleNamespace(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key="",
        model_copy=lambda update: SimpleNamespace(**{**vars(agent), **update}),
    )
    return SimpleNamespace(agent=agent)


def test_render_choices_marca_o_ativo_e_numera():
    options = [
        ModelOption(provider="x", model="ativo", current=True),
        ModelOption(provider="x", model="outro"),
    ]
    linhas = render_choices(options)

    assert linhas[0].startswith(" 1. ● x/ativo")
    assert linhas[1].startswith(" 2. ○ x/outro")


def test_render_choices_respeita_o_limite():
    options = [ModelOption(provider="x", model=f"m{index}") for index in range(50)]
    assert len(render_choices(options, limit=5)) == 5


def test_catalogo_encontra_slug_e_id_unico():
    catalog = ModelCatalog(
        options=[
            ModelOption(provider="a", model="um"),
            ModelOption(provider="b", model="dois"),
            ModelOption(provider="c", model="dois"),
        ]
    )

    assert catalog.find("a/um").slug == "a/um"
    assert catalog.find("um").slug == "a/um"
    # id repetido em dois provedores não tem escolha óbvia
    assert catalog.find("dois") is None
    assert catalog.find("") is None
    assert catalog.slugs == ["a/um", "b/dois", "c/dois"]


def test_provider_candidates_coloca_o_atual_primeiro(monkeypatch):
    monkeypatch.setattr("zaya.agent.catalog._has_own_key", lambda name: False)
    cfg = _cfg(provider="openai")

    names = provider_candidates(cfg)

    # Só o provedor configurado: sem chave própria, os presets não entram.
    assert names == ["openai"]


def test_provider_candidates_inclui_presets_com_chave_propria(monkeypatch):
    monkeypatch.setattr(
        "zaya.agent.catalog._has_own_key", lambda name: name == "deepseek"
    )
    cfg = _cfg(provider="openai")

    names = provider_candidates(cfg)

    assert names == ["openai", "deepseek"]


def test_provider_candidates_sem_repeticao(monkeypatch):
    monkeypatch.setattr("zaya.agent.catalog._has_own_key", lambda name: True)
    cfg = _cfg(provider="deepseek")

    names = provider_candidates(cfg)

    assert names.count("deepseek") == 1
    assert names[0] == "deepseek"


@pytest.mark.asyncio
async def test_collect_models_reaproveita_o_cliente_atual(monkeypatch):
    monkeypatch.setattr("zaya.agent.catalog.provider_candidates", lambda cfg: ["custom"])

    class Client:
        async def list_models(self):
            return ["principal", "extra"]

    catalog = await collect_models(_cfg(), client=Client())

    assert catalog.slugs == ["custom/atual-1", "custom/principal", "custom/extra"]
    assert catalog.options[0].current is True
    assert catalog.errors == []


@pytest.mark.asyncio
async def test_collect_models_erro_do_cliente_nao_derruba_o_catalogo(monkeypatch):
    monkeypatch.setattr("zaya.agent.catalog.provider_candidates", lambda cfg: ["custom"])

    class Client:
        async def list_models(self):
            raise RuntimeError("fora do ar")

    catalog = await collect_models(_cfg(), client=Client())

    assert catalog.slugs == ["custom/atual-1"]
    assert catalog.errors == [("custom", "fora do ar")]


@pytest.mark.asyncio
async def test_collect_models_marca_o_atual_mesmo_fora_da_lista(monkeypatch):
    monkeypatch.setattr("zaya.agent.catalog.provider_candidates", lambda cfg: ["a", "b"])

    async def fake_fetch(cfg, name, current, timeout):
        return name, ["m1", "m2"], ""

    monkeypatch.setattr("zaya.agent.catalog._fetch_provider", fake_fetch)
    catalog = await collect_models(_cfg(provider="a", model="sumiu"))

    assert catalog.slugs[0] == "a/sumiu"
    assert catalog.options[0].current is True
    assert "a/m1" in catalog.slugs
    assert catalog.errors == []


@pytest.mark.asyncio
async def test_collect_models_deduplica_e_ignora_vazios(monkeypatch):
    monkeypatch.setattr("zaya.agent.catalog.provider_candidates", lambda cfg: ["a"])

    async def fake_fetch(cfg, name, current, timeout):
        return name, ["m1", "m1", "  "], ""

    monkeypatch.setattr("zaya.agent.catalog._fetch_provider", fake_fetch)
    catalog = await collect_models(_cfg(provider="a", model="m1"))

    assert catalog.slugs == ["a/m1"]
