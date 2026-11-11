"""Markdown da TUI: títulos, ênfase e código com cores próprias.

As respostas da Zaya chegam em Markdown. Renderizar o texto cru deixava `**`,
`#` e crases na tela e achatava a hierarquia (título e corpo com o mesmo peso).
Aqui o Markdown é interpretado com a paleta da Zaya: cada nível de título tem
sua cor e um marcador, a ênfase ganha contraste e o código ganha uma superfície
própria — sem competir com o fundo do card.
"""

from __future__ import annotations

from typing import ClassVar

from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from rich.markdown import CodeBlock, Heading, Markdown
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

from zaya.interface.textual_render import ACCENT, CYAN, LILAC, MINT, MUTED, TEXT

# Tema de sintaxe com contraste suave; o fundo é fixado no card, não no Pygments.
CODE_THEME = "one-dark"
CODE_BG = "#1d1929"
INLINE_CODE_BG = "#2a2439"
LINK = CYAN

# Marcador à esquerda de cada título, como o rail dos cards: dá o "degrau" da
# hierarquia mesmo em terminal que não diferencia bem peso de fonte.
_HEADING_MARKERS: dict[str, tuple[str, str]] = {
    "h1": ("▌ ", LILAC),
    "h2": ("▎ ", ACCENT),
    "h3": ("▸ ", CYAN),
}

# Estilos consumidos pelo Rich Markdown. Nomes fixos ("markdown.h2" etc.);
# por isso o tema é aplicado no console do app em vez de por renderização.
MARKDOWN_THEME = Theme(
    {
        "markdown.paragraph": TEXT,
        "markdown.text": TEXT,
        "markdown.h1": f"bold {LILAC}",
        "markdown.h2": f"bold {ACCENT}",
        "markdown.h3": f"bold {CYAN}",
        "markdown.h4": f"bold {MINT}",
        "markdown.h5": f"bold {MUTED}",
        "markdown.h6": f"italic {MUTED}",
        "markdown.strong": "bold #f0ecfb",
        "markdown.em": "italic #cfd4dd",
        "markdown.s": f"strike {MUTED}",
        "markdown.code": f"#e8d9ff on {INLINE_CODE_BG}",
        "markdown.code_block": "#d9d3ea",
        "markdown.block_quote": "#9aa3b2",
        "markdown.item.bullet": f"bold {ACCENT}",
        "markdown.item.number": f"bold {ACCENT}",
        "markdown.hr": "#3a424f",
        "markdown.link": f"underline {LINK}",
        "markdown.link_url": f"underline {MUTED}",
        "markdown.table.border": "#3a424f",
        "markdown.table.header": f"bold {ACCENT}",
        "markdown.kbd": "bold #e0b341",
    }
)

# Durante o streaming re-renderizamos a cada ~150 ms; acima desse tamanho o
# Markdown completo começa a custar caro (~100 ms por frame), então mostramos
# só a cauda.
_STREAM_LIMIT = 800


def _safe_lexer(name: str) -> str:
    """Pygments levanta ClassNotFound para lexers inventados pelo modelo."""
    if not name:
        return "text"
    try:
        get_lexer_by_name(name)
    except ClassNotFound:
        return "text"
    return name


class _ZayaHeading(Heading):
    """Título alinhado à esquerda com marcador colorido por nível."""

    LEVEL_ALIGN: ClassVar[dict[str, str]] = {
        f"h{index}": "left" for index in range(1, 7)
    }

    def __rich_console__(self, console, options):
        text = self.text.copy()
        text.justify = "left"
        marker = _HEADING_MARKERS.get(self.tag)
        if marker is None:
            yield text
            return
        glyph, color = marker
        line = Text()
        line.append(glyph, style=f"bold {color}")
        line.append_text(text)
        yield line


class _ZayaCodeBlock(CodeBlock):
    """Bloco de código com fundo do card e lexer tolerante."""

    def __rich_console__(self, console, options):
        code = str(self.text).rstrip()
        yield Syntax(
            code,
            _safe_lexer(self.lexer_name),
            theme=CODE_THEME,
            word_wrap=True,
            padding=(0, 1),
            background_color=CODE_BG,
        )


class ZayaMarkdown(Markdown):
    """Markdown com os elementos visuais da Zaya."""

    elements: ClassVar[dict] = {
        **Markdown.elements,
        "heading_open": _ZayaHeading,
        "fence": _ZayaCodeBlock,
        "code_block": _ZayaCodeBlock,
    }


def prepare_stream(text: str, limit: int = _STREAM_LIMIT) -> str:
    """Recorta a cauda do streaming e fecha cerca de código aberta.

    Texto parcial com ``` sem fechamento pintaria o resto da resposta como
    código; completar a cerca mantém o preview fiel ao resultado final.
    """
    if len(text) > limit:
        tail = text[-limit:]
        for separator in ("\n\n", "\n", " "):
            cut = tail.find(separator)
            if cut != -1:
                tail = tail[cut + len(separator) :]
                break
        text = "… " + tail.lstrip("\n")
    fences = sum(1 for line in text.splitlines() if line.lstrip().startswith("```"))
    if fences % 2:
        text += "\n```"
    return text


def markdown_body(text: str, *, streaming: bool = False):
    """Renderiza Markdown; cai para texto puro se algo inesperado acontecer."""
    if not text or not text.strip():
        return Text("")
    source = prepare_stream(text) if streaming else text
    try:
        return ZayaMarkdown(source, code_theme=CODE_THEME)
    except Exception:  # pragma: no cover - markdown-it é tolerante; rede de segurança
        return Text(source)
