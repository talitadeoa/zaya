from __future__ import annotations

import io

from rich.console import Console
from rich.text import Text

from zaya.interface.textual_markdown import (
    CODE_THEME,
    MARKDOWN_THEME,
    ZayaMarkdown,
    _safe_lexer,
    markdown_body,
    prepare_stream,
)


def _render(renderable, width: int = 80) -> str:
    console = Console(width=width, record=True, file=io.StringIO())
    console.push_theme(MARKDOWN_THEME)
    console.print(renderable)
    return console.export_text()


def test_safe_lexer_returns_text_for_unknown_name():
    assert _safe_lexer("linguagem-que-nao-existe") == "text"
    assert _safe_lexer("") == "text"
    assert _safe_lexer("python") == "python"


def test_markdown_body_empty_returns_empty_text():
    assert isinstance(markdown_body(""), Text)
    assert str(markdown_body("")) == ""
    assert str(markdown_body("   \n  ")) == ""


def test_markdown_body_returns_zaya_markdown():
    body = markdown_body("# Título\n\nTexto com **forte**.")
    assert isinstance(body, ZayaMarkdown)


def test_headings_render_left_aligned_with_markers():
    out = _render(ZayaMarkdown("# Um\n\n## Dois\n\n### Três\n"))
    assert "▌ " in out
    assert "▎ " in out
    assert "▸ " in out
    assert "Um" in out and "Dois" in out and "Três" in out
    # o markdown cru não deve vazar
    assert "#" not in out


def test_inline_code_and_emphasis_are_styled():
    body = ZayaMarkdown("Aqui `x = 1` e **forte** e _ênfase_.")
    out = _render(body)
    assert "`" not in out
    assert "x = 1" in out
    assert "**" not in out


def test_code_block_uses_valid_lexer_and_unknown_falls_back():
    out = _render(ZayaMarkdown("```nao-existe\nprint('oi')\n```\n"))
    assert "print('oi')" in out
    py = _render(ZayaMarkdown("```python\nprint('oi')\n```\n"))
    assert "print" in py


def test_prepare_stream_closes_open_fence():
    assert prepare_stream("```python\nprint(1)").endswith("\n```")
    assert prepare_stream("```\na\n```\n").count("```") == 2


def test_prepare_stream_truncates_tail_at_boundary():
    text = "início " * 200 + "\n\ncauda final"
    out = prepare_stream(text, limit=40)
    assert out.startswith("… ")
    assert len(out) <= 45
    assert "cauda final" in out


def test_prepare_stream_keeps_short_text_untouched():
    text = "curto **aqui**"
    assert prepare_stream(text, limit=100) == text


def test_markdown_body_streaming_does_not_leak_raw_fence():
    out = _render(markdown_body("```python\nprint(1)", streaming=True))
    assert "``" not in out
    assert "print(1)" in out


def test_code_theme_is_exported():
    assert CODE_THEME == "one-dark"
