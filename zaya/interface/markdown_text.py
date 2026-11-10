"""Limpeza de Markdown para exibicao no terminal (sem depender do Rich).

A Zaya fala a resposta enquanto o texto chega; exibir os marcadores crus de
Markdown (`**`, `#`, crases, links) deixa a conversa poluida. Aqui aplicamos uma
limpeza textual best-effort em trechos (frases/linhas) completos.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"^\s*```[^\n]*$", re.M)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_WIKI = re.compile(r"\[\[([^\]]+)\]\]")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_HEADER = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.M)
_HR = re.compile(r"^\s*([-*_])\1{2,}\s*$", re.M)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.M)
_NUM = re.compile(r"^\s*\d+\.\s+", re.M)
_EMPHASIS = re.compile(r"\*\*|__|~~")
_STAR = re.compile(r"\*")
_TABLE = re.compile(r"\|")


def strip_markdown(text: str) -> str:
    """Remove marcacao Markdown comum, mantendo o texto legivel."""
    if not text:
        return ""
    text = _FENCE.sub("", text)
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _WIKI.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _HEADER.sub("", text)
    text = _QUOTE.sub("", text)
    text = _HR.sub("", text)
    text = _BULLET.sub("", text)
    text = _NUM.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = _STAR.sub("", text)
    text = _TABLE.sub(" ", text)
    return text
