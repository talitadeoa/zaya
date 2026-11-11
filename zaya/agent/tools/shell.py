"""Ferramenta de shell: executa comandos no diretorio do projeto."""

from __future__ import annotations

import subprocess
from typing import Any

from zaya.agent.errors import ToolError
from zaya.agent.tools.base import ToolContext, ToolResult, integer, schema, string

OUTPUT_LIMIT = 20_000


def bash(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = str(arguments.get("command") or "").strip()
    if not command:
        return ToolResult.failure("command vazio")
    timeout = min(max(float(arguments.get("timeout") or 120), 1.0), 600.0)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(ctx.directory),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult.failure(f"comando excedeu {timeout:.0f}s e foi encerrado")
    except OSError as exc:
        raise ToolError(f"nao foi possivel executar o comando: {exc}") from exc

    parts = []
    if proc.stdout:
        parts.append(proc.stdout.rstrip())
    if proc.stderr:
        parts.append("[stderr]\n" + proc.stderr.rstrip())
    output = "\n".join(parts) or "(sem saida)"
    if len(output) > OUTPUT_LIMIT:
        output = output[:OUTPUT_LIMIT] + f"\n\n[saida truncada em {OUTPUT_LIMIT} caracteres]"
    if proc.returncode != 0:
        return ToolResult.failure(f"[exit {proc.returncode}]\n{output}")
    return ToolResult.success(output)


BASH_TOOL: dict[str, Any] = dict(
    name="bash",
    description=(
        "Executa um comando de shell no diretorio do projeto e devolve stdout/stderr. "
        "Use para testes, git, builds e comandos pontuais."
    ),
    parameters=schema(
        {
            "command": string("Comando a executar."),
            "timeout": integer("Tempo maximo em segundos (1-600, padrao 120).", 1),
        },
        ["command"],
    ),
    handler=bash,
    requires_approval=True,
)
