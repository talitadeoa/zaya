"""Ferramentas de arquivos: leitura, escrita, edicao, busca e listagem."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from zaya.agent.errors import ToolError
from zaya.agent.tools.base import (
    ToolContext,
    ToolResult,
    boolean,
    integer,
    schema,
    string,
    unified_diff,
)

READ_LIMIT = 200_000
# Sem offset/limit, arquivos grandes mostram so o inicio: o modelo deve
# localizar com grep e ler o trecho exato.
READ_PREVIEW_LINES = 400
WRITE_IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache"}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except IsADirectoryError as exc:
        raise ToolError(f"{path} e um diretorio") from exc
    except OSError as exc:
        raise ToolError(f"nao foi possivel ler {path}: {exc}") from exc


def read(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.resolve(str(arguments.get("path") or ""))
    if not path.exists():
        return ToolResult.failure(f"arquivo nao encontrado: {path}")
    if path.is_dir():
        return ToolResult.failure(f"{path} e um diretorio; use list")
    text = _read_text(path)
    offset = max(1, int(arguments.get("offset") or 1))
    limit = arguments.get("limit")
    lines = text.splitlines()
    if offset > 1 or limit:
        end = offset - 1 + int(limit) if limit else None
        selected = lines[offset - 1 : end]
        body = "\n".join(selected)
        header = f"[linhas {offset}-{offset + len(selected) - 1} de {len(lines)}]\n"
        text = header + body
    elif len(lines) > READ_PREVIEW_LINES:
        body = "\n".join(lines[:READ_PREVIEW_LINES])
        text = (
            f"[linhas 1-{READ_PREVIEW_LINES} de {len(lines)} — arquivo grande; "
            "use offset/limit ou grep para o trecho exato]\n" + body
        )
    if len(text) > READ_LIMIT:
        text = text[:READ_LIMIT] + f"\n\n[truncado em {READ_LIMIT} caracteres]"
    return ToolResult.success(text or "(arquivo vazio)")


def write(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.resolve(str(arguments.get("path") or ""))
    content = str(arguments.get("content") or "")
    old = ""
    if path.exists() and path.is_file():
        old = _read_text(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"nao foi possivel escrever {path}: {exc}") from exc
    name = ctx.relative(path)
    patch = unified_diff(old, content, name)
    action = "atualizado" if old else "criado"
    return ToolResult.success(f"{action}: {name}", diff_name=name, diff=patch)


def edit(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.resolve(str(arguments.get("path") or ""))
    old_string = str(arguments.get("old_string") or "")
    new_string = str(arguments.get("new_string") or "")
    replace_all = bool(arguments.get("replace_all"))
    if not old_string:
        return ToolResult.failure("old_string vazio: informe o trecho a substituir")
    text = _read_text(path)
    occurrences = text.count(old_string)
    if occurrences == 0:
        return ToolResult.failure(f"trecho nao encontrado em {ctx.relative(path)}")
    if occurrences > 1 and not replace_all:
        return ToolResult.failure(
            f"o trecho aparece {occurrences} vezes; seja mais especifico ou use replace_all"
        )
    updated = text.replace(old_string, new_string) if replace_all else text.replace(
        old_string, new_string, 1
    )
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"nao foi possivel editar {path}: {exc}") from exc
    name = ctx.relative(path)
    return ToolResult.success(
        f"editado: {name}", diff_name=name, diff=unified_diff(text, updated, name)
    )


def list_dir(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.resolve(str(arguments.get("path") or "."))
    if not path.exists():
        return ToolResult.failure(f"diretorio nao encontrado: {path}")
    if not path.is_dir():
        return ToolResult.failure(f"{path} nao e um diretorio")
    entries: list[str] = []
    try:
        for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            suffix = "/" if item.is_dir() else ""
            entries.append(f"{item.name}{suffix}")
    except OSError as exc:
        raise ToolError(f"nao foi possivel listar {path}: {exc}") from exc
    if not entries:
        return ToolResult.success("(diretorio vazio)")
    return ToolResult.success("\n".join(entries[:500]))


def glob_paths(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    pattern = str(arguments.get("pattern") or "").strip()
    if not pattern:
        return ToolResult.failure("pattern vazio")
    base = ctx.resolve(str(arguments.get("path") or "."))
    if not base.is_dir():
        return ToolResult.failure(f"{base} nao e um diretorio")
    matches: list[str] = []
    try:
        for item in base.glob(pattern):
            matches.append(ctx.relative(item))
    except (OSError, ValueError) as exc:
        raise ToolError(f"glob invalido: {exc}") from exc
    if not matches:
        return ToolResult.success("(nenhum arquivo encontrado)")
    return ToolResult.success("\n".join(sorted(matches)[:500]))


def _iter_files(base: Path):
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        if any(part in WRITE_IGNORED_DIRS for part in relative.parts):
            continue
        yield path, relative


def grep(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    pattern = str(arguments.get("pattern") or "")
    if not pattern:
        return ToolResult.failure("pattern vazio")
    flags = re.IGNORECASE if arguments.get("ignore_case") else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise ToolError(f"regex invalida: {exc}") from exc
    base = ctx.resolve(str(arguments.get("path") or "."))
    file_glob = str(arguments.get("glob") or "").strip()
    if base.is_file():
        files = [(base, Path(base.name))]
    else:
        files = _iter_files(base)
    matches: list[str] = []
    for path, relative in files:
        if file_glob and not fnmatch.fnmatch(str(relative), file_glob):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue
        text = raw.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{relative}:{number}: {line.strip()[:400]}")
                if len(matches) >= 200:
                    return ToolResult.success("\n".join(matches) + "\n[...limite de 200]")
    if not matches:
        return ToolResult.success("(nenhuma ocorrencia)")
    return ToolResult.success("\n".join(matches))


READ_TOOL: dict[str, Any] = dict(
    name="read",
    description=(
        "Le um arquivo de texto. Prefira grep para localizar e offset/limit para "
        "o trecho exato; sem offset/limit mostra no maximo 400 linhas."
    ),
    parameters=schema(
        {
            "path": string("Caminho do arquivo (relativo ao projeto ou absoluto)."),
            "offset": integer("Linha inicial (1-based).", 1),
            "limit": integer("Quantidade maxima de linhas."),
        },
        ["path"],
    ),
    handler=read,
)

WRITE_TOOL: dict[str, Any] = dict(
    name="write",
    description="Cria ou substitui um arquivo com o conteudo informado.",
    parameters=schema(
        {
            "path": string("Caminho do arquivo."),
            "content": string("Conteudo completo do arquivo."),
        },
        ["path", "content"],
    ),
    handler=write,
    requires_approval=True,
)

EDIT_TOOL: dict[str, Any] = dict(
    name="edit",
    description=(
        "Substitui um trecho exato de um arquivo. old_string precisa ser unico, "
        "a menos que replace_all seja true."
    ),
    parameters=schema(
        {
            "path": string("Caminho do arquivo."),
            "old_string": string("Trecho exato a substituir."),
            "new_string": string("Novo trecho."),
            "replace_all": boolean("Substituir todas as ocorrencias."),
        },
        ["path", "old_string", "new_string"],
    ),
    handler=edit,
    requires_approval=True,
)

LIST_TOOL: dict[str, Any] = dict(
    name="list",
    description="Lista arquivos e diretorios de um caminho.",
    parameters=schema(
        {"path": string("Diretorio (padrao: raiz do projeto).")}
    ),
    handler=list_dir,
)

GLOB_TOOL: dict[str, Any] = dict(
    name="glob",
    description="Encontra arquivos por padrao glob, ex.: '**/*.py'.",
    parameters=schema(
        {
            "pattern": string("Padrao glob."),
            "path": string("Diretorio base (padrao: raiz do projeto)."),
        },
        ["pattern"],
    ),
    handler=glob_paths,
)

GREP_TOOL: dict[str, Any] = dict(
    name="grep",
    description="Busca por expressao regular no conteudo dos arquivos.",
    parameters=schema(
        {
            "pattern": string("Expressao regular."),
            "path": string("Arquivo ou diretorio base."),
            "glob": string("Filtro de nome, ex.: '*.py'."),
            "ignore_case": boolean("Ignorar maiusculas/minusculas."),
        },
        ["pattern"],
    ),
    handler=grep,
)
