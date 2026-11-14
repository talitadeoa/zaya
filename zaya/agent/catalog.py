"""Catalogo de modelos: provedores com credencial e modelos disponiveis.

A lista e montada na hora (``GET /models`` de cada provedor com chave), porque
os nomes mudam com frequencia. Quando um provedor falha, o erro fica em
``ModelCatalog.errors`` e os outros continuam valendo.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from zaya.agent.providers import (
    PRESETS,
    PUBLIC_PROVIDERS,
    build_provider,
    normalize_provider,
)
from zaya.agent.types import ModelRef

if TYPE_CHECKING:
    from zaya.config import Config

# Teto de opcoes numeraveis de uma vez. O resto continua acessivel pelo slug,
# entao o catalogo nunca vira uma parede de 300 linhas no terminal.
MAX_CHOICES = 40


@dataclass(frozen=True)
class ModelOption:
    provider: str
    model: str
    current: bool = False

    @property
    def slug(self) -> str:
        return f"{self.provider}/{self.model}"

    def as_ref(self) -> ModelRef:
        return ModelRef(provider_id=self.provider, model_id=self.model)


@dataclass
class ModelCatalog:
    options: list[ModelOption] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def slugs(self) -> list[str]:
        return [option.slug for option in self.options]

    def find(self, value: str) -> ModelOption | None:
        """Aceita ``provider/model`` (exato) ou um id de modelo unico."""
        needle = value.strip()
        if not needle:
            return None
        for option in self.options:
            if option.slug == needle:
                return option
        matches = [item for item in self.options if item.model == needle]
        if len(matches) == 1:
            return matches[0]
        return None


def provider_candidates(cfg: Config) -> list[str]:
    """Provedores consultaveis, o atual primeiro.

    Só entram presets com a chave própria no ambiente/.env (``OPENAI_API_KEY``,
    ``DEEPSEEK_API_KEY``…) e o provedor configurado, que pode usar
    ``ZAYA_AGENT_API_KEY``, ``base_url`` próprio ou nenhuma chave (ollama).
    A chave genérica não serve de senha para todos: ela pertence ao provedor
    do config.
    """
    current = normalize_provider(cfg.agent.provider)
    names: list[str] = [current]
    for name in PUBLIC_PROVIDERS:
        if name == current or name in names or not _has_own_key(name):
            continue
        names.append(name)
    return names


def _has_own_key(name: str) -> bool:
    return bool(_own_key(name))


def _own_key(name: str) -> str:
    preset = PRESETS.get(normalize_provider(name))
    if preset is None:
        return ""
    for key in preset.env_keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def render_choices(options: list[ModelOption], *, limit: int = MAX_CHOICES) -> list[str]:
    """Linhas numeradas, com o modelo ativo marcado por ●."""
    return [
        f"{index:>2}. {'●' if option.current else '○'} {option.slug}"
        for index, option in enumerate(options[:limit], start=1)
    ]


async def collect_models(
    cfg: Config,
    *,
    client: object | None = None,
    timeout: float = 6.0,
) -> ModelCatalog:
    """Consulta os provedores em paralelo e devolve as opcoes ordenadas.

    ``client`` e o ``AgentClient`` ja pronto: o provedor em uso reaproveita a
    conexao dele, entao a lista bate com a que o turno usaria.
    """
    current_provider = normalize_provider(cfg.agent.provider)
    candidates = provider_candidates(cfg)

    async def fetch(name: str) -> tuple[str, list[str], str]:
        if name == current_provider and client is not None:
            lister = getattr(client, "list_models", None)
            if callable(lister):
                try:
                    models = await asyncio.wait_for(lister(), timeout)
                except Exception as exc:
                    return name, [], str(exc) or exc.__class__.__name__
                return name, [str(item) for item in models], ""
        return await _fetch_provider(cfg, name, current_provider, timeout)

    catalog = ModelCatalog()
    results = await asyncio.gather(*(fetch(name) for name in candidates))
    for name, models, error in results:
        if error:
            catalog.errors.append((name, error))
        for model in models:
            if model.strip():
                catalog.options.append(ModelOption(provider=name, model=model))
    catalog.options = _finalize(
        catalog.options, f"{current_provider}/{(cfg.agent.model or '').strip()}"
    )
    return catalog


async def _fetch_provider(
    cfg: Config, name: str, current: str, timeout: float
) -> tuple[str, list[str], str]:
    agent = cfg.agent.model_copy(
        update={
            "provider": name,
            # O base_url do config e do provedor atual; os presets usam o seu.
            "base_url": "" if name != current else cfg.agent.base_url,
            # A chave do config (e a ZAYA_AGENT_API_KEY) pertencem ao provedor
            # atual; os demais só valem com a chave do próprio ambiente.
            "api_key": cfg.agent.api_key if name == current else _own_key(name),
        }
    )
    provider = None
    try:
        provider = build_provider(agent)
        models = await asyncio.wait_for(provider.list_models(), timeout)
        return name, [str(item) for item in models], ""
    except Exception as exc:
        return name, [], str(exc) or exc.__class__.__name__
    finally:
        if provider is not None:
            with contextlib.suppress(Exception):
                await provider.aclose()


def _finalize(options: list[ModelOption], current_slug: str) -> list[ModelOption]:
    """Tira repetidos, marca o ativo e o coloca em primeiro."""
    seen: set[str] = set()
    ordered: list[ModelOption] = []
    for option in options:
        if option.slug in seen:
            continue
        seen.add(option.slug)
        ordered.append(option)
    found = next(
        (index for index, item in enumerate(ordered) if item.slug == current_slug),
        None,
    )
    if found is None:
        provider, _, model = current_slug.partition("/")
        if model:
            ordered.insert(0, ModelOption(provider=provider, model=model, current=True))
        return ordered
    active = ordered.pop(found)
    ordered.insert(0, ModelOption(active.provider, active.model, current=True))
    return ordered
