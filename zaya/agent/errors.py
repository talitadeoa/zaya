"""Erros do agente independente."""

from __future__ import annotations


class AgentError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class AgentCancelled(AgentError):
    """Turno interrompido a pedido do usuario."""


class ToolError(RuntimeError):
    """Falha de validacao/execucao de uma ferramenta."""
