"""Constantes visuais e helpers de render da interface Textual."""

from __future__ import annotations

from rich.text import Text

# Paleta unica e dessaturada: um acento lavanda, um azul acinzentado para a
# usuaria e um verde salvia para status. Sem blocos de cor saturada.
ACCENT = "#b3a1e0"
CYAN = "#8fb0c9"
MINT = "#8fbfae"
LILAC = "#c9bce8"
MUTED = "#7b8494"
TEXT = "#e3e6eb"
BG = "#0f1115"
SURFACE = "#151922"
SURFACE_ALT = "#1b202a"
BORDER = "#262c37"

# Superficies dos cards: apenas um leve desvio do fundo; o rail colorido faz o
# papel de destaque, sem pintar a caixa inteira.
USER_BG = "#12171f"
ZAYA_BG = "#161420"
MINT_BG = "#121c19"
INPUT_BG = "#151922"
INPUT_BG_FOCUS = "#1b202a"

# Durante o streaming renderizamos so a cauda para o custo por frame ficar
# limitado (o card inteiro e montado uma vez no finish_stream).
_STREAM_PREVIEW_CHARS = 4000

# Wordmark compacto: seis linhas. A arte braille gigante empurrava o conteudo
# para baixo e deixava o topo vazio em terminais normais.
_WORDMARK = [
    " ".join(parts)
    for parts in zip(
        ["█████", "   ██", "  ██ ", " ██  ", "█████"],
        ["█████", "██ ██", "█████", "██ ██", "██ ██"],
        ["██ ██", "██ ██", " ███ ", "  █  ", "  █  "],
        ["█████", "██ ██", "█████", "██ ██", "██ ██"],
        strict=True,
    )
]


def _mix_color(start: str, end: str, ratio: float) -> str:
    a = tuple(int(start[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(end[i : i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(a[i] + (b[i] - a[i]) * ratio) for i in range(3))
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _vgradient(count: int, stops: list[str]) -> list[str]:
    """Gradiente vertical suave ao longo de ``count`` linhas."""
    if count <= 0:
        return []
    if len(stops) == 1:
        return [stops[0]] * count
    colors: list[str] = []
    for index in range(count):
        pos = (index / (count - 1)) * (len(stops) - 1) if count > 1 else 0.0
        low = min(int(pos), len(stops) - 2)
        colors.append(_mix_color(stops[low], stops[low + 1], pos - low))
    return colors


def _welcome_art(max_cols: int = 78) -> list[list[tuple[str, str]]]:
    """Wordmark do banner, com gradiente vertical curto e discreto."""
    colors = _vgradient(len(_WORDMARK), [LILAC, ACCENT, CYAN])
    return [
        [(line[:max_cols], color)]
        for line, color in zip(_WORDMARK, colors, strict=False)
    ]


_SHORTCUTS = "i digitar · f falar · Tab modo · F1 comandos · F2 sessões · Esc parar"

_HELP = (
    "Modo normal: i digitar · t texto · m mudo · v voz on/off · f falar · "
    "c escuta contínua · Tab alterna o modo · Esc interromper. No modo insert, "
    "Esc volta ao normal. F1 paleta · F2 sessões · F3 modo · Ctrl+L limpar. "
    "Cada modo tem o próprio histórico de sessões; /novo abre uma sessão nova. "
    "/consolidar [hoje|ontem|AAAA-MM-DD] salva a memória do dia no Second Brain. "
    "/chines on|off mostra 中文 + pinyin de cada mensagem."
)


def card(label: Text | str, body, accent: str, background: str):
    """Card com rail vertical solido a esquerda.

    Usa o fundo de uma celula de 1 coluna como "linha" continua: diferente
    dos caracteres de borda (│), o fundo pinta a celula inteira, entao a
    linha nunca aparece tracejada, seja qual for a fonte do terminal.
    """
    from rich.padding import Padding
    from rich.table import Table

    table = Table(
        box=None,
        show_header=False,
        show_edge=False,
        show_footer=False,
        pad_edge=False,
        padding=0,
        expand=False,
    )
    table.add_column("rail", width=1, no_wrap=True, style=f"on {accent}")
    table.add_column(
        "card",
        max_width=88,
        overflow="fold",
        style=f"on {background}",
    )
    title = label if isinstance(label, Text) else Text(label)
    title.stylize(f"bold {accent}")
    table.add_row(Text(" "), Padding(title, (0, 1, 0, 1)))
    table.add_row(Text(" "), Padding(body, (0, 1, 0, 1)))
    return table
