"""Construcao do adaptador a partir da configuracao."""

from __future__ import annotations

import os
from dataclasses import dataclass

from zaya.agent.errors import AgentError
from zaya.agent.providers.anthropic import AnthropicProvider
from zaya.agent.providers.base import Provider
from zaya.agent.providers.openai_compat import OpenAICompatibleProvider


@dataclass(frozen=True)
class Preset:
    api: str
    base_url: str
    env_keys: tuple[str, ...]


PRESETS: dict[str, Preset] = {
    "deepseek": Preset(
        api="openai",
        base_url="https://api.deepseek.com/v1",
        env_keys=("DEEPSEEK_API_KEY",),
    ),
    "openai": Preset(
        api="openai",
        base_url="https://api.openai.com/v1",
        env_keys=("OPENAI_API_KEY",),
    ),
    "openrouter": Preset(
        api="openai",
        base_url="https://openrouter.ai/api/v1",
        env_keys=("OPENROUTER_API_KEY",),
    ),
    "ollama": Preset(
        api="openai",
        base_url="http://127.0.0.1:11434/v1",
        env_keys=(),
    ),
    "anthropic": Preset(
        api="anthropic",
        base_url="https://api.anthropic.com",
        env_keys=("ANTHROPIC_API_KEY",),
    ),
}

# Aliases amigaveis para o mesmo adaptador/preset.
ALIASES = {
    "claude": "anthropic",
    "openai-compatible": "openai",
    "compatible": "openai",
    "local": "ollama",
}

PUBLIC_PROVIDERS = tuple(PRESETS)


def normalize_provider(value: str) -> str:
    name = (value or "").strip().lower()
    return ALIASES.get(name, name)


def resolve_api_key(provider: str, configured: str = "") -> str:
    if configured.strip():
        return configured.strip()
    key = os.environ.get("ZAYA_AGENT_API_KEY", "").strip()
    if key:
        return key
    preset = PRESETS.get(normalize_provider(provider))
    if preset:
        for name in preset.env_keys:
            value = os.environ.get(name, "").strip()
            if value:
                return value
    return ""


def describe_provider(provider: str) -> dict[str, str]:
    name = normalize_provider(provider)
    preset = PRESETS.get(name)
    if preset is None:
        return {"name": name, "api": "openai", "base_url": ""}
    return {"name": name, "api": preset.api, "base_url": preset.base_url}


def build_provider(cfg) -> Provider:
    """Cria o provedor a partir de ``config.AgentConfig``."""
    name = normalize_provider(cfg.provider)
    preset = PRESETS.get(name)
    base_url = (cfg.base_url or "").strip() or (preset.base_url if preset else "")
    if not base_url:
        raise AgentError(
            f"provider desconhecido '{cfg.provider}': defina agent.base_url "
            f"ou use um de: {', '.join(PUBLIC_PROVIDERS)}"
        )
    api_key = resolve_api_key(cfg.provider, cfg.api_key)
    api = preset.api if preset else "openai"
    if api == "anthropic":
        return AnthropicProvider(
            name=name,
            base_url=base_url,
            api_key=api_key,
            timeout=cfg.request_timeout,
            include_usage=cfg.usage,
        )
    return OpenAICompatibleProvider(
        name=name,
        base_url=base_url,
        api_key=api_key,
        timeout=cfg.request_timeout,
        include_usage=cfg.usage,
    )
