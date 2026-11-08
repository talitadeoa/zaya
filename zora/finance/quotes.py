"""Cotacoes simples via Yahoo Finance."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class FinanceError(RuntimeError):
    pass


@dataclass
class Quote:
    symbol: str
    price: float | None
    previous_close: float | None
    currency: str
    change_pct: float | None


def normalize_ticker(ticker: str) -> str:
    symbol = (ticker or "").strip().upper()
    if not symbol:
        return ""
    if symbol.startswith("^") or "." in symbol:
        return symbol
    if any(ch.isdigit() for ch in symbol):
        return f"{symbol}.SA"
    return symbol


async def fetch_quote(ticker: str, http: httpx.AsyncClient | None = None) -> Quote:
    symbol = normalize_ticker(ticker)
    if not symbol:
        raise FinanceError("ticker vazio")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "5d", "interval": "1d"}
    owns = http is None
    client = http or httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.get(
            url, params=params, headers={"User-Agent": "Mozilla/5.0"}
        )
    except httpx.HTTPError as exc:
        raise FinanceError(f"falha ao buscar cotação: {exc}") from exc
    finally:
        if owns:
            await client.aclose()
    if not response.is_success:
        raise FinanceError(f"Yahoo respondeu {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise FinanceError("resposta inválida do Yahoo") from exc
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise FinanceError(f"ticker não encontrado: {ticker}")
    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    indicators = result[0].get("indicators") or {}
    quote_rows = indicators.get("quote") or []
    closes = quote_rows[0].get("close") if quote_rows else []
    valid = [value for value in (closes or []) if isinstance(value, (int, float))]
    previous = valid[-2] if len(valid) >= 2 else None
    if previous is None:
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    change = None
    if price is not None and previous:
        change = (price - previous) / previous * 100.0
    return Quote(
        symbol=str(meta.get("symbol") or symbol),
        price=price,
        previous_close=previous,
        currency=str(meta.get("currency") or ""),
        change_pct=change,
    )


def format_quote(quote: Quote) -> str:
    price = f"{quote.price:.2f}" if quote.price is not None else "?"
    change = (
        f"{quote.change_pct:+.2f}% no dia"
        if quote.change_pct is not None
        else "variação indisponível"
    )
    currency = quote.currency or ""
    return f"{quote.symbol}: {currency} {price} ({change})"
