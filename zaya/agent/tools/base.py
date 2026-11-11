"""Contrato das ferramentas que o agente pode executar."""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zaya.agent.errors import ToolError


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    diff_name: str = ""
    diff: str = ""

    @classmethod
    def success(cls, content: str, *, diff_name: str = "", diff: str = "") -> ToolResult:
        return cls(True, content, diff_name, diff)

    @classmethod
    def failure(cls, content: str) -> ToolResult:
        return cls(False, content)


@dataclass
class ToolContext:
    """Diretorio de trabalho e politica de acesso das ferramentas."""

    directory: Path
    allow_outside_project: bool = True

    def resolve(self, raw: str) -> Path:
        if not (raw or "").strip():
            raise ToolError("caminho vazio")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.directory / path
        try:
            path = path.resolve()
        except OSError as exc:
            raise ToolError(f"caminho invalido: {raw}") from exc
        if not self.allow_outside_project:
            try:
                path.relative_to(self.directory.resolve())
            except ValueError as exc:
                raise ToolError(
                    f"acesso negado a {path}: fora de {self.directory} "
                    "(agent.allow_outside_project=false)"
                ) from exc
        return path

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.directory.resolve()))
        except (ValueError, OSError):
            return str(path)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], ToolResult]
    requires_approval: bool = False

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def summary(self, arguments: dict[str, Any]) -> str:
        for key in ("command", "path", "pattern", "query", "url"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""


def schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def integer(description: str, minimum: int = 0) -> dict[str, Any]:
    return {"type": "integer", "description": description, "minimum": minimum}


def boolean(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def unified_diff(old: str, new: str, name: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            lineterm="",
        )
    )


@dataclass
class ToolSet:
    """Registro ordenado de ferramentas por nome."""

    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, *tools: Tool) -> None:
        for tool in tools:
            self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self.tools.values()]

    def __iter__(self):
        return iter(self.tools.values())

    def __len__(self) -> int:
        return len(self.tools)
