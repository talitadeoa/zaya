from __future__ import annotations

from zaya.interface.markdown_text import strip_markdown


def test_strip_markdown_headers_and_emphasis():
    text = "# Título\n\nAqui está **forte** e _ênis_ e ~~riscado~~."
    clean = strip_markdown(text)
    assert "#" not in clean
    assert "**" not in clean
    assert "~~" not in clean
    assert "forte" in clean
    assert "Título" in clean


def test_strip_markdown_links_images_and_wiki():
    text = "Veja [o site](https://ex.com) e [[Minha Nota]] e ![logo](x.png)."
    clean = strip_markdown(text)
    assert "o site" in clean
    assert "Minha Nota" in clean
    assert "https://ex.com" not in clean
    assert "logo" not in clean
    assert "[[" not in clean


def test_strip_markdown_code_and_fences():
    text = "Use `print()` agora.\n```python\nprint('oi')\n```\nFim."
    clean = strip_markdown(text)
    assert "`" not in clean
    assert "```" not in clean
    assert "print()" in clean
    assert "Fim." in clean


def test_strip_markdown_lists_and_tables():
    text = "- item um\n- item dois\n1. primeiro\n| a | b |"
    clean = strip_markdown(text)
    assert "- item" not in clean
    assert "item um" in clean
    assert "primeiro" in clean
    assert "|" not in clean


def test_strip_markdown_empty():
    assert strip_markdown("") == ""
