"""Constantes visuais e helpers de render da interface Textual."""

from __future__ import annotations

from rich.text import Text

from zora.interface.ascii_art import DIVINE_LINES, fit_braille

ACCENT = "#D644FB"
CYAN = "#79DCFD"
MINT = "#A8EFDB"
LILAC = "#E6C3FD"
MUTED = "grey62"

# Superfícies (fundos) com contraste sobre o fundo da tela (#0b0614).
USER_BG = "#0d3b49"
ZORA_BG = "#33174f"
MINT_BG = "#0e3a31"
INPUT_BG = "#1a1230"
INPUT_BG_FOCUS = "#241a3d"

# Durante o streaming renderizamos só a cauda para o custo por frame ficar
# limitado (o card inteiro é montado uma vez no finish_stream).
_STREAM_PREVIEW_CHARS = 4000

_ASCII_FIGURE = """
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣷⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⡇⠀⠀⠀⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣾⣿⣿⠀⠀⢸⣧⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣼⣿⣿⣿⣧⡀⢸⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠰⠶⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡶⠄⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠈⠙⢿⣿⣿⣿⡿⠋⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢿⣿⡿⠀⢰⣿⣿⣷⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⠇⠀⣾⣿⣿⣿⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⠀⠈⣿⢀⣼⣿⣿⣿⣿⣿⣄⡀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⣀⣠⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣤⡀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠠⣴⣶⣾⣿⣿⣿⣿⣗⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣶⣦⡤
⠀⠀⠀⠀⠀⠀⠀⠈⠉⣿⡟⠿⣿⣿⣿⣿⣿⣿⣿⣯⣿⣿⡿⠟⠋⠉⠉⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢰⣿⣧⠀⠀⠝⣿⣿⣿⣿⣿⣿⡟⠁⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣼⣿⣿⡄⠀⠀⠘⣿⣿⣿⣿⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⣠⣿⣿⣿⣧⠀⠀⠀⢹⣿⣿⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⢀⣠⣼⣿⣿⣿⣿⣿⣷⣤⡀⠘⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠤⣶⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡧⠄⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠉⠙⠻⢿⣿⣿⣿⣿⣿⣿⠿⠛⠉⢹⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠈⢻⣿⣿⣿⡿⠃⠀⠀⠀⢸⡏⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⣿⠃⠀⠀⠀⠀⢸⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⣿⠀⠀⠀⠀⠀⠈⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠘⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠹⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
""".strip("\n").splitlines()

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


def _center_lines(lines: list[str], height: int) -> list[str]:
    """Centraliza verticalmente um bloco de linhas na altura pedida."""
    if len(lines) >= height:
        return lines
    top = (height - len(lines)) // 2
    return [""] * top + lines + [""] * (height - top - len(lines))


def _welcome_art(max_cols: int = 78) -> list[list[tuple[str, str]]]:
    """Banner de boas-vindas com as duas artes lado a lado.

    Cada linha e uma lista de segmentos ``(texto, cor)`` para que a figura e a
    arte braille mantenham gradientes próprios. Em terminais estreitos, cai de
    volta para o layout empilhado.
    """
    figure = list(_ASCII_FIGURE)
    figure_w = max((len(line) for line in figure), default=0)
    gap = 2
    available = max_cols - figure_w - gap

    if available < 12:
        figure_colors = _vgradient(len(figure), [LILAC, ACCENT, CYAN, MINT])
        art: list[list[tuple[str, str]]] = [
            [(line, color)] for line, color in zip(figure, figure_colors, strict=False)
        ]
        divine = fit_braille(DIVINE_LINES, max_cols=max_cols, max_rows=26)
        if divine:
            art.append([("", MUTED)])
            divine_colors = _vgradient(len(divine), [MINT, CYAN, LILAC, ACCENT])
            art.extend(
                [[(line, color)] for line, color in zip(divine, divine_colors, strict=False)]
            )
        return art

    divine = fit_braille(DIVINE_LINES, max_cols=available, max_rows=26)
    height = max(len(figure), len(divine))
    figure = _center_lines(figure, height)
    divine = _center_lines(divine, height)
    figure_colors = _vgradient(height, [LILAC, ACCENT, CYAN, MINT])
    divine_colors = _vgradient(height, [MINT, CYAN, LILAC, ACCENT])

    rows: list[list[tuple[str, str]]] = []
    for index in range(height):
        rows.append(
            [
                (figure[index].ljust(figure_w), figure_colors[index]),
                (" " * gap, MUTED),
                (divine[index], divine_colors[index]),
            ]
        )
    return rows


_SHORTCUTS = (
    "i digitar · t texto · m mudo · v voz · f falar · c contínua · "
    "Tab modo · Esc interromper · F1 comandos · F2 sessões · F3 modo · "
    "Ctrl+T tema · Ctrl+L limpar"
)

_HELP = (
    "Modo normal: i digitar · t texto · m mudo · v voz on/off · f falar · "
    "c escuta contínua · Tab alterna o modo · Esc interromper. No modo insert, "
    "Esc volta ao normal. F1 paleta · F2 sessões · F3 modo · Ctrl+T tema · "
    "Ctrl+L limpar. Cada modo tem o próprio histórico de sessões; /novo abre "
    "uma sessão nova. /consolidar [hoje|ontem|AAAA-MM-DD] salva a memória do dia "
    "no Second Brain. /chines on|off mostra 中文 + pinyin de cada mensagem."
)


def card(label: Text | str, body, accent: str, background: str):
    """Card com rail vertical sólido à esquerda.

    Usa o fundo de uma célula de 1 coluna como "linha" contínua: diferente
    dos caracteres de borda (│), o fundo pinta a célula inteira, então a
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
        expand=True,
    )
    table.add_column("rail", width=1, no_wrap=True, style=f"on {accent}")
    table.add_column("card", ratio=1, style=f"on {background}")
    title = label if isinstance(label, Text) else Text(label)
    title.stylize(f"bold {accent}")
    table.add_row(Text(" "), Padding(title, (0, 1, 0, 1)))
    table.add_row(Text(" "), Padding(body, (0, 1, 0, 1)))
    return table
