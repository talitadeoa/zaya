"""Ferramentas padrao do agente."""

from __future__ import annotations

from zaya.agent.tools.base import Tool, ToolContext, ToolResult, ToolSet
from zaya.agent.tools.filesystem import (
    EDIT_TOOL,
    GLOB_TOOL,
    GREP_TOOL,
    LIST_TOOL,
    READ_TOOL,
    WRITE_TOOL,
)
from zaya.agent.tools.shell import BASH_TOOL


def build_tools(ctx: ToolContext) -> ToolSet:
    tools = ToolSet()
    tools.add(
        Tool(**READ_TOOL),
        Tool(**WRITE_TOOL),
        Tool(**EDIT_TOOL),
        Tool(**LIST_TOOL),
        Tool(**GLOB_TOOL),
        Tool(**GREP_TOOL),
        Tool(**BASH_TOOL),
    )
    return tools


__all__ = ["Tool", "ToolContext", "ToolResult", "ToolSet", "build_tools"]
