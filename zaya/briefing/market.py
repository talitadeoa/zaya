"""Resumo da carteira de ativos para o briefing do mercado, sem LLM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from zaya.finance.quotes import Quote

_WEEKDAYS = (
    "segunda",
    "terça",
    "quarta",
    "quinta",
    "sexta",
    "sábado",
    "domingo",
)


@dataclass(frozen=True)
class MarketRow:
    label: str
    ticker: str
    quote: Quote | None = None


def market_due(
    now_hm: str,
    market_at: str,
    market_until: str,
    weekday: int,
    days: list[int] | tuple[int, ...],
) -> bool:
    """Diz se o resumo da carteira deve sair agora (0 = segunda)."""
    return weekday in days and market_at <= now_hm < market_until


def _number(value: float) -> str:
    text = f"{value:,.2f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def _change(value: float) -> str:
    return f"{value:+.2f}".replace(".", ",")


def _price(row: MarketRow) -> str:
    quote = row.quote
    if quote is None or quote.price is None:
        return "cotação indisponível"
    price = _number(quote.price)
    if quote.symbol.startswith("^"):
        value = f"{price} pts"
    else:
        currency = {"BRL": "R$", "USD": "US$"}.get(quote.currency, quote.currency)
        value = f"{currency} {price}".strip()
    if quote.change_pct is None:
        return value
    return f"{value} ({_change(quote.change_pct)}%)"


def _highlights(rows: list[MarketRow]) -> str:
    scored: list[tuple[MarketRow, float]] = []
    for row in rows:
        quote = row.quote
        if quote is None or quote.change_pct is None:
            continue
        scored.append((row, quote.change_pct))
    if len(scored) < 2:
        return ""
    best_row, best_pct = max(scored, key=lambda pair: pair[1])
    worst_row, worst_pct = min(scored, key=lambda pair: pair[1])
    if best_pct == worst_pct:
        return ""
    return (
        f"Melhor: {best_row.label} {_change(best_pct)}%"
        f" · Pior: {worst_row.label} {_change(worst_pct)}%"
    )


def market_message(day: date, rows: list[MarketRow]) -> str:
    """Mensagem determinística com a cotação de cada ativo da carteira."""
    lines = [f"Bolsa — {_WEEKDAYS[day.weekday()]}, {day:%d/%m}", ""]
    if not rows:
        lines.append("Carteira vazia: use /carteira add <ticker>.")
        return "\n".join(lines)
    lines.extend(f"{row.label} ({row.ticker}): {_price(row)}" for row in rows)
    highlights = _highlights(rows)
    if highlights:
        lines.extend(["", highlights])
    return "\n".join(lines)
