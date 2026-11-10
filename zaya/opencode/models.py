from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelRef:
    """Referencia de modelo no formato `provider/model`."""

    provider_id: str
    model_id: str

    @classmethod
    def parse(cls, value: str) -> ModelRef:
        value = (value or "").strip()
        if not value:
            raise ValueError("modelo vazio")
        if "/" not in value:
            raise ValueError(f"modelo invalido '{value}': use provider/model")
        provider, _, model = value.partition("/")
        if not provider or not model:
            raise ValueError(f"modelo invalido '{value}': use provider/model")
        return cls(provider_id=provider, model_id=model)

    @property
    def slug(self) -> str:
        return f"{self.provider_id}/{self.model_id}"

    def prompt_payload(self) -> dict[str, str]:
        """Formato aceito por POST /session/{id}/message e prompt_async."""
        return {"providerID": self.provider_id, "modelID": self.model_id}

    def create_payload(self) -> dict[str, str]:
        """Formato aceito por POST /session (campo `model`)."""
        return {"id": self.model_id, "providerID": self.provider_id}


@dataclass
class SessionInfo:
    id: str
    title: str = ""
    directory: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> SessionInfo:
        location = data.get("location") or {}
        directory = data.get("directory") or (
            location.get("directory") if isinstance(location, dict) else ""
        )
        return cls(
            id=data.get("id", ""),
            title=data.get("title", "") or "",
            directory=str(directory or ""),
            raw=data,
        )
